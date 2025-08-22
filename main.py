#!/usr/bin/env python3
import argparse, subprocess, sys, re, tempfile, wave, os, shutil, time
from pathlib import Path
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, Future

PRESETS = {
    "en_f": ("af_sarah", "en-us"),
    "en_m": ("am_adam", "en-us"),
    "zh_f": ("zf_xiaoxiao", "cmn"),
    "zh_m": ("zm_yunjian", "cmn"),
}

CJK_REGEX = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
SPLIT_REGEX = re.compile(r"(?<=[\.!\?;:。！？；：…])\s+|(?<=[，、])\s+|\n+")

def contains_cjk(s: str) -> bool:
    return CJK_REGEX.search(s) is not None

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def split_into_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in SPLIT_REGEX.split(text) if p and p.strip()]
    return parts if parts else [text.strip()]

def chunk_text(text: str, max_chars: int = 240) -> List[str]:
    sentences = split_into_sentences(text)
    chunks, cur = [], ""
    for sent in sentences:
        if not cur:
            cur = sent
            continue
        if len(cur) + 1 + len(sent) <= max_chars:
            cur = f"{cur} {sent}"
        else:
            chunks.append(cur)
            cur = sent
    if cur:
        chunks.append(cur)
    return chunks

def run_kokoro(text: str, outfile: Optional[str], voice: str, lang: str) -> int:
    cmd = ["uv", "run", "kokoro-tts", "-"]
    if outfile:
        cmd.append(outfile)
    cmd += ["--voice", voice, "--lang", lang]
    if not outfile:
        cmd.append("--stream")
    return subprocess.run(cmd, input=text.encode("utf-8")).returncode

def concat_wavs(infiles: List[str], outfile: str) -> None:
    if not infiles:
        raise ValueError("No WAVs to concatenate")
    with wave.open(infiles[0], "rb") as w0:
        params = w0.getparams()
        frames = [w0.readframes(w0.getnframes())]
    for f in infiles[1:]:
        with wave.open(f, "rb") as wf:
            if wf.getparams() != params:
                raise RuntimeError("Inconsistent WAV params; cannot concatenate")
            frames.append(wf.readframes(wf.getnframes()))
    with wave.open(outfile, "wb") as out:
        out.setparams(params)
        for fr in frames:
            out.writeframes(fr)

def choose_preset(text: str, args) -> Tuple[str, str]:
    voice, lang = PRESETS["en_f"]
    for key in ("en_f", "en_m", "zh_f", "zh_m"):
        if getattr(args, key):
            voice, lang = PRESETS[key]
    if not any(getattr(args, k) for k in ("en_f", "en_m", "zh_f", "zh_m")) and not args.voice and not args.lang:
        if contains_cjk(text):
            voice, lang = PRESETS["zh_f"]
    if args.voice:
        voice = args.voice
    if args.lang:
        lang = args.lang
    return voice, lang

# ---- Streaming helpers (prefetch + playback) -------------------------------

def find_player() -> Optional[List[str]]:
    """Pick an available CLI player."""
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    if shutil.which("play"):
        return ["play", "-q"]
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    return None

def play_wav(player_cmd: List[str], wav_path: str) -> int:
    return subprocess.run(player_cmd + [wav_path]).returncode

def synth_to_wav(text: str, wav_path: str, voice: str, lang: str) -> int:
    return run_kokoro(text, wav_path, voice, lang)

def stream_with_prefetch(chunks: List[str], voice: str, lang: str) -> None:
    """
    Stream chunk 1 directly (kokoro --stream).
    While playing chunk i, pre-synthesize chunk i+1 to a temp WAV in the background.
    Then play the pre-synthesized WAVs for subsequent chunks to reduce gaps.
    """
    n = len(chunks)
    player = find_player()

    # If we can't play WAVs locally, fall back to streaming each chunk
    if player is None or n == 1:
        for i, ch in enumerate(chunks, 1):
            print(f"[Reading {i}/{n}] (stream) len={len(ch)} chars")
            rc = run_kokoro(ch, None, voice, lang)
            if rc != 0:
                sys.exit(f"TTS failed on chunk {i} with code {rc}")
        return

    tmpdir = tempfile.mkdtemp(prefix="kokoro_stream_")
    futures: List[Optional[Future]] = [None] * n
    wavs = [None] * n

    def prefetch(idx: int, executor: ThreadPoolExecutor):
        if idx < n:
            wav_path = os.path.join(tmpdir, f"prefetch_{idx+1:04d}.wav")
            print(f"[Preprocessing {idx+1}/{n}] → {wav_path}")
            futures[idx] = executor.submit(synth_to_wav, chunks[idx], wav_path, voice, lang)
            wavs[idx] = wav_path

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # 1) Play first via stream, prefetch 2nd
            print(f"[Reading 1/{n}] (stream) len={len(chunks[0])} chars")
            prefetch(1, pool)  # pre-synthesize chunk #2 while we stream #1
            rc = run_kokoro(chunks[0], None, voice, lang)
            if rc != 0:
                sys.exit(f"TTS failed on chunk 1 with code {rc}")

            # 2) For chunks 2..n: play pre-rendered WAV; while playing, prefetch the next
            for i in range(1, n):
                # Ensure current WAV is ready
                fut = futures[i]
                if fut is not None:
                    fut.result()  # raise if failed
                if wavs[i] is None or not os.path.exists(wavs[i]):
                    sys.exit(f"Prefetch for chunk {i+1} missing; cannot play")

                # Kick off prefetch for next (i+1 -> index i+1)
                prefetch(i + 1, pool)

                # Play current WAV
                print(f"[Reading {i+1}/{n}] (pre-rendered) {wavs[i]}")
                rc = play_wav(player, wavs[i])
                if rc != 0:
                    sys.exit(f"Playback failed on chunk {i+1} with code {rc}")

            # Make sure any last prefetch finished cleanly
            if n >= 2 and futures[-1] is not None:
                futures[-1].result()

    finally:
        # Clean up temp WAVs/dir (best-effort)
        try:
            for p in wavs:
                if p and os.path.exists(p):
                    os.remove(p)
            os.rmdir(tmpdir)
        except Exception:
            pass

# ---- Main -------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Kokoro TTS wrapper with chunking + prefetch streaming + auto CN/EN")
    p.add_argument("input", help="Text to speak or path to a .txt file")
    p.add_argument("-o", "--output", help="Output file (default: stream with prefetch)")
    p.add_argument("--voice", help="Explicit voice ID (overrides presets)")
    p.add_argument("--lang", help="Explicit language code (overrides presets)")
    p.add_argument("--en_f", action="store_true", help="Preset: English female")
    p.add_argument("--en_m", action="store_true", help="Preset: English male")
    p.add_argument("--zh_f", action="store_true", help="Preset: Chinese female")
    p.add_argument("--zh_m", action="store_true", help="Preset: Chinese male")
    p.add_argument("--max-chars", type=int, default=240, help="Max chars per chunk (default 240)")
    args = p.parse_args()

    path = Path(args.input)
    text = path.read_text(encoding="utf-8") if path.exists() else args.input
    text = normalize_text(text)
    chunks = chunk_text(text, max_chars=args.max_chars)

    voice, lang = choose_preset(text, args)

    if not args.output:
        # Streaming with one-chunk-ahead prefetch
        stream_with_prefetch(chunks, voice, lang)
        return

    # Save-to-file path (unchanged): synth each chunk -> concat
    tmpdir = tempfile.mkdtemp(prefix="kokoro_chunks_")
    wavs = []
    try:
        if len(chunks) == 1:
            rc = run_kokoro(chunks[0], args.output, voice, lang)
            if rc != 0:
                sys.exit(f"TTS failed with code {rc}")
            print(f"Saved to {args.output}")
            return

        for i, ch in enumerate(chunks, 1):
            tmpwav = os.path.join(tmpdir, f"part_{i:04d}.wav")
            print(f"[Synth {i}/{len(chunks)}] → {tmpwav}")
            rc = run_kokoro(ch, tmpwav, voice, lang)
            if rc != 0:
                sys.exit(f"TTS failed on chunk {i} with code {rc}")
            wavs.append(tmpwav)

        concat_wavs(wavs, args.output)
        print(f"Saved to {args.output}")
    finally:
        for f in wavs:
            try: os.remove(f)
            except Exception: pass
        try: os.rmdir(tmpdir)
        except Exception: pass

if __name__ == "__main__":
    main()
