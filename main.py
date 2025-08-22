#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

PRESETS = {
    "en_f": ("af_sarah", "en-us"),
    "en_m": ("am_adam", "en-us"),
    "zh_f": ("zf_xiaoxiao", "cmn"),
    "zh_m": ("zm_yunjian", "cmn"),
}

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
    parser = argparse.ArgumentParser(description="Kokoro TTS wrapper")
    parser.add_argument("input", help="Text to speak or path to a .txt file")
    parser.add_argument("-o", "--output", help="Output file (default: stream only)")
    parser.add_argument("--voice", help="Explicit voice ID (overrides presets)")
    parser.add_argument("--lang", help="Explicit language code (overrides presets)")
    parser.add_argument("--en_f", action="store_true", help="Preset: English female")
    parser.add_argument("--en_m", action="store_true", help="Preset: English male")
    parser.add_argument("--zh_f", action="store_true", help="Preset: Chinese female")
    parser.add_argument("--zh_m", action="store_true", help="Preset: Chinese male")
    args = parser.parse_args()

    # Determine preset
    voice, lang = PRESETS["en_f"]  # default
    for key in ("en_f", "en_m", "zh_f", "zh_m"):
        if getattr(args, key):
            voice, lang = PRESETS[key]

    # Override with explicit args
    if args.voice:
        voice = args.voice
    if args.lang:
        lang = args.lang

    # Read input (string or file)
    input_path = Path(args.input)
    if input_path.exists():
        text = input_path.read_text(encoding="utf-8")
    else:
        text = args.input

    run_tts(text, args.output, voice, lang)

if __name__ == "__main__":
    main()
