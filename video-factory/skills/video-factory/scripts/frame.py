#!/usr/bin/env python3
"""Save one full-size still from a clip, for a closer look than a contact sheet gives.

Useful for choosing a crop position (focus_x / focus_y) or checking focus,
faces and on-screen text.

Usage:
  python3 frame.py RUN_DIR CLIP_ID SECONDS     e.g. frame.py factory-output/run1 c03 12.5
  python3 frame.py VIDEO_FILE SECONDS          e.g. frame.py draft.mp4 8

Stills are saved to RUN_DIR/stills/ (or a stills/ folder next to the video).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FactoryError, ffmpeg_base, load_json, probe, run  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 3:
        run_dir = Path(args[0]).expanduser().resolve()
        index = load_json(run_dir / "clip-index.json")
        clip = next((c for c in index["clips"] if c["id"] == args[1]), None)
        if clip is None:
            raise FactoryError(f"clip {args[1]!r} is not in {run_dir / 'clip-index.json'}")
        source, name, out_dir = Path(clip["file"]), clip["id"], run_dir / "stills"
    elif len(args) == 2:
        source = Path(args[0]).expanduser().resolve()
        name, out_dir = source.stem, source.parent / "stills"
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    try:
        moment = float(args[-1])
    except ValueError:
        raise FactoryError(f"{args[-1]!r} is not a number of seconds")
    if not source.is_file():
        raise FactoryError(f"file not found: {source}")
    duration = probe(source)["duration"]
    moment = min(max(moment, 0.0), max(duration - 0.05, 0.0))

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}_{moment:.2f}s.jpg"
    run(ffmpeg_base() + ["-ss", f"{moment:.3f}", "-i", str(source), "-frames:v", "1",
                         "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease",
                         "-q:v", "2", str(target)])
    print(target)


if __name__ == "__main__":
    try:
        main()
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
