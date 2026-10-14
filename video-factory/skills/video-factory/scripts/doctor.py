#!/usr/bin/env python3
"""Check that everything video-factory needs is installed, and say how to fix what isn't.

Usage:
  python3 doctor.py

Exit code 0 means the required tools are present. Optional tools are
reported but do not cause a failure.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys

REQUIRED_FILTERS = ["ass", "loudnorm", "sidechaincompress", "silencedetect", "tile", "blackdetect", "ebur128"]

INSTALL_FFMPEG = {
    "Darwin": "brew install ffmpeg",
    "Linux": "sudo apt install ffmpeg   (or your distribution's package manager)",
    "Windows": "winget install Gyan.FFmpeg",
}


def line(ok: bool, label: str, detail: str = "") -> None:
    mark = "ok  " if ok else "FAIL"
    print(f"[{mark}] {label}" + (f": {detail}" if detail else ""))


def main() -> int:
    system = platform.system()
    failures = 0

    version = sys.version_info
    python_ok = version >= (3, 9)
    line(python_ok, "Python 3.9 or newer", f"found {platform.python_version()}")
    failures += not python_ok

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    line(bool(ffmpeg), "ffmpeg", ffmpeg or f"not found. Install it with: {INSTALL_FFMPEG.get(system, 'see ffmpeg.org')}")
    line(bool(ffprobe), "ffprobe", ffprobe or "not found. It ships with ffmpeg.")
    failures += (not ffmpeg) + (not ffprobe)

    if ffmpeg:
        listing = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        available = {parts[1] for parts in (row.split() for row in listing.splitlines()) if len(parts) > 2}
        missing = [name for name in REQUIRED_FILTERS if name not in available]
        line(not missing, "ffmpeg filters", "all present" if not missing else
             f"missing {', '.join(missing)}. Install a full ffmpeg build (with libass), e.g. {INSTALL_FFMPEG.get(system, '')}")
        failures += bool(missing)
        encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        has_x264 = "libx264" in encoders
        line(has_x264, "H.264 encoder (libx264)", "" if has_x264 else "missing. Install a full ffmpeg build.")
        failures += not has_x264
        drawtext = "drawtext" in available
        print(f"[{'ok  ' if drawtext else 'note'}] text labels on contact sheets"
              + ("" if drawtext else ": ffmpeg has no drawtext filter, so sheets will be unlabeled (optional)"))

    try:
        import faster_whisper  # noqa: F401
        line(True, "faster-whisper (speech transcription)")
    except ImportError:
        print("[note] faster-whisper is not installed. Transcripts and captions need it (optional).\n"
              "       Install it with: python3 -m pip install faster-whisper")

    print()
    if failures:
        print(f"{failures} required item(s) missing. Fix them, then run doctor.py again.")
        return 1
    print("Ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
