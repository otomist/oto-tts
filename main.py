#!/usr/bin/env python3
import argparse, subprocess, sys, re
from pathlib import Path

PRESETS = {
    "en_f": ("af_sarah", "en-us"),
    "en_m": ("am_adam", "en-us"),
    "zh_f": ("zf_xiaoxiao", "cmn"),
    "zh_m": ("zm_yunjian", "cmn"),
}

# Covers CJK Unified Ideographs (+ Extension A) and CJK Compatibility Ideographs
CJK_REGEX = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")

def contains_cjk(s: str) -> bool:
    return CJK_REGEX.search(s) is not None

def run_tts(text: str, outfile: str | None, voice: str, lang: str):
    cmd = ["uv", "run", "kokoro-tts", "-"]
    if outfile:
        cmd.append(outfile)
    cmd += ["--voice", voice, "--lang", lang]
    if not outfile:
        cmd.append("--stream")
    result = subprocess.run(cmd, input=text.encode("utf-8"))
    if result.returncode != 0:
        sys.exit(f"TTS failed with code {result.returncode}")

def main():
    p = argparse.ArgumentParser(description="Kokoro TTS wrapper with auto CN/EN")
    p.add_argument("input", help="Text to speak or path to a .txt file")
    p.add_argument("-o", "--output", help="Output file (default: stream only)")
    p.add_argument("--voice", help="Explicit voice ID (overrides presets)")
    p.add_argument("--lang", help="Explicit language code (overrides presets)")
    p.add_argument("--en_f", action="store_true", help="Preset: English female")
    p.add_argument("--en_m", action="store_true", help="Preset: English male")
    p.add_argument("--zh_f", action="store_true", help="Preset: Chinese female")
    p.add_argument("--zh_m", action="store_true", help="Preset: Chinese male")
    args = p.parse_args()

    # Read input (string or file)
    path = Path(args.input)
    text = path.read_text(encoding="utf-8") if path.exists() else args.input

    # Default preset
    voice, lang = PRESETS["en_f"]

    # Apply explicit preset flags if any
    for key in ("en_f", "en_m", "zh_f", "zh_m"):
        if getattr(args, key):
            voice, lang = PRESETS[key]

    # If no explicit preset/overrides, auto-detect CJK and switch to zh_f
    if not any(getattr(args, k) for k in ("en_f", "en_m", "zh_f", "zh_m")) and not args.voice and not args.lang:
        if contains_cjk(text):
            voice, lang = PRESETS["zh_f"]

    # Explicit overrides win last
    if args.voice:
        voice = args.voice
    if args.lang:
        lang = args.lang

    run_tts(text, args.output, voice, lang)

if __name__ == "__main__":
    main()
