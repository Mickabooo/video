#!/usr/bin/env python3
"""Check a rendered video for problems before anyone else sees it.

Makes a contact sheet of evenly spaced frames (to look at) and measures:
  - length, compared with the EDL if one is given
  - loudness and peak level
  - unexpected black frames (fades at the very start and end are ignored)
  - long silences

Usage:
  python3 review.py RUN_DIR/draft.mp4 [--edl RUN_DIR/edl.json] [--frames 12]

Writes review/<name>-sheet.jpg and review/<name>.json next to the video.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import make_sheet  # noqa: E402
from common import FactoryError, ffmpeg_base, fmt_time, load_json, probe, run, save_json  # noqa: E402

EDGE = 0.8  # black frames within this many seconds of the start or end are expected fades


def measure(video: Path, has_audio: bool) -> Dict[str, Any]:
    cmd = ffmpeg_base() + ["-i", str(video), "-vf", "blackdetect=d=0.25:pix_th=0.10"]
    if has_audio:
        cmd += ["-af", "ebur128=peak=true:framelog=verbose,silencedetect=noise=-45dB:d=1.0"]
    else:
        cmd += ["-an"]
    cmd += ["-f", "null", "-"]
    stderr = run(cmd).stderr or ""

    black = [{"start": float(a), "end": float(b)}
             for a, b in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", stderr)]
    silences: List[Dict[str, float]] = []
    pending = None
    for kind, value in re.findall(r"silence_(start|end): (-?[0-9.]+)", stderr):
        if kind == "start":
            pending = max(float(value), 0.0)
        elif pending is not None:
            silences.append({"start": pending, "end": float(value)})
            pending = None
    if pending is not None:
        silences.append({"start": pending, "end": None})

    summary = stderr.split("Summary:")[-1] if "Summary:" in stderr else ""
    loud = re.search(r"I:\s+(-?[0-9.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[0-9.]+|-inf) dBFS", summary)
    return {
        "loudness_lufs": float(loud.group(1)) if loud else None,
        "true_peak_dbfs": None if not peak or peak.group(1) == "-inf" else float(peak.group(1)),
        "black": black,
        "silences": silences,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", help="rendered video to check")
    parser.add_argument("--edl", help="the edl.json it was rendered from, to compare length")
    parser.add_argument("--frames", type=int, default=12, help="frames on the contact sheet (default: 12)")
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        raise FactoryError(f"file not found: {video}")
    meta = probe(video)
    duration = meta["duration"]
    out_dir = video.parent / "review"
    out_dir.mkdir(exist_ok=True)

    count = max(1, args.frames)
    times = [round(duration * (i + 0.5) / count, 2) for i in range(count)]
    sheet = out_dir / f"{video.stem}-sheet.jpg"
    make_sheet(video, meta, times, sheet)

    stats = measure(video, meta["has_audio"])
    problems: List[str] = []

    if args.edl:
        expected = None
        edl = load_json(Path(args.edl).expanduser().resolve())
        segments = edl.get("segments", []) if isinstance(edl, dict) else []
        try:
            expected = sum(float(s["out"]) - float(s["in"]) for s in segments)
        except (KeyError, TypeError, ValueError):
            pass
        if expected and abs(expected - duration) > 0.25:
            problems.append(f"length is {duration:.2f}s but the EDL adds up to {expected:.2f}s")

    if not meta["has_audio"]:
        problems.append("the video has no audio track")
    loud = stats["loudness_lufs"]
    if loud is not None and not -16.5 <= loud <= -11.5:
        problems.append(f"loudness is {loud:.1f} LUFS; expected about -14")
    peak = stats["true_peak_dbfs"]
    if peak is not None and peak > -0.5:
        problems.append(f"true peak is {peak:.1f} dBFS, which may distort on some players")
    for span in stats["black"]:
        if span["start"] > EDGE and span["end"] < duration - EDGE:
            problems.append(f"black frames from {fmt_time(span['start'])} to {fmt_time(span['end'])}")
    for span in stats["silences"]:
        end = span["end"] if span["end"] is not None else duration
        if end - span["start"] >= 1.0:
            problems.append(f"silence from {fmt_time(span['start'])} to {fmt_time(end)}")

    report = {
        "video": str(video),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "duration": duration,
        "sheet": str(sheet),
        "sheet_frames": [{"cell": i, "time": t} for i, t in enumerate(times, start=1)],
        **stats,
        "problems": problems,
    }
    save_json(out_dir / f"{video.stem}.json", report)

    print(f"{video.name}: {meta.get('width')}x{meta.get('height')}, {fmt_time(duration)}")
    if loud is not None:
        print(f"Loudness: {loud:.1f} LUFS, true peak {peak if peak is not None else 'n/a'} dBFS")
    print(f"Contact sheet: {sheet}")
    print("Frames: " + ", ".join(f"#{i} {t:.1f}s" for i, t in enumerate(times, start=1)))
    if problems:
        print("Problems found:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("No technical problems found. Now look at the contact sheet.")


if __name__ == "__main__":
    try:
        main()
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
