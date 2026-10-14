#!/usr/bin/env python3
"""Check an edit decision list (edl.json) against the clip index.

Errors stop the render. Warnings are worth reading: most are signs of a cut
that will look or sound sloppy, such as a cut in the middle of a word.

Usage:
  python3 validate_edl.py RUN_DIR/edl.json [--index RUN_DIR/clip-index.json]

Exit code 0 means the EDL can be rendered (there may still be warnings).
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FINAL_SIZES, FactoryError, find_index, fmt_time, load_json  # noqa: E402

FITS = ("crop", "pad", "blur")
TEXT_POSITIONS = ("top", "middle", "bottom")
CAPTION_POSITIONS = ("bottom", "middle")
END_TOLERANCE = 0.05     # an out point this close past the end is clamped, not rejected
MIN_SEGMENT = 0.2        # shorter than this cannot be rendered reliably
SHORT_SEGMENT = 0.6      # shorter than this usually reads as a glitch


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _word_cut(words: List[Dict[str, Any]], moment: float) -> Optional[Dict[str, Any]]:
    """Return the word that `moment` falls inside, if any (with a small margin)."""
    for word in words:
        if word["start"] + 0.03 < moment < word["end"] - 0.03:
            return word
    return None


def validate(edl: Any, index: Dict[str, Any], edl_dir: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Return (errors, warnings, normalized_edl). The normalized EDL has every default filled in."""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(edl, dict):
        return ["the EDL must be a JSON object"], warnings, {}
    out = copy.deepcopy(edl)
    clips = {c["id"]: c for c in index.get("clips", [])}

    version = out.get("version", 1)
    if version != 1:
        errors.append(f"version {version!r} is not supported; use 1")
    out["version"] = 1

    aspect = out.get("aspect", "16:9")
    if aspect not in FINAL_SIZES:
        errors.append(f"aspect {aspect!r} is not supported; use one of {', '.join(FINAL_SIZES)}")
    out["aspect"] = aspect

    fps = _number(out.get("fps", 30))
    if fps is None or not 1 <= fps <= 120:
        errors.append("fps must be a number from 1 to 120")
    out["fps"] = fps or 30

    fit = out.get("fit", "crop")
    if fit not in FITS:
        errors.append(f"fit {fit!r} is not supported; use crop, pad or blur")
    out["fit"] = fit

    transcribed = bool(index.get("transcribed"))
    captions = out.get("captions", transcribed)
    if not isinstance(captions, bool):
        errors.append("captions must be true or false")
        captions = False
    if captions and not transcribed:
        warnings.append("captions are on, but the clips were not transcribed, so no captions will appear")
    out["captions"] = captions

    position = out.get("caption_position", "bottom")
    if position not in CAPTION_POSITIONS:
        errors.append(f"caption_position {position!r} is not supported; use bottom or middle")
    out["caption_position"] = position

    font = out.get("font", "Arial")
    if not isinstance(font, str) or not font.strip():
        errors.append("font must be a font name, such as \"Arial\"")
        font = "Arial"
    out["font"] = font

    fade = out.get("fade", True)
    if not isinstance(fade, bool):
        errors.append("fade must be true or false")
        fade = True
    out["fade"] = fade

    # ---- segments ----------------------------------------------------------
    segments = out.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("segments must be a non-empty list")
        segments = []
    timeline = 0.0
    used: Dict[str, List[Tuple[float, float, int]]] = {}
    normalized_segments: List[Dict[str, Any]] = []

    for number, seg in enumerate(segments, start=1):
        label = f"segment {number}"
        if not isinstance(seg, dict):
            errors.append(f"{label} must be an object")
            continue
        seg = dict(seg)
        clip = clips.get(seg.get("clip"))
        if clip is None:
            errors.append(f"{label}: clip {seg.get('clip')!r} is not in the clip index")
            continue
        label = f"segment {number} ({clip['id']})"
        start, end = _number(seg.get("in")), _number(seg.get("out"))
        if start is None or end is None:
            errors.append(f"{label}: 'in' and 'out' must both be numbers of seconds")
            continue
        if start < 0:
            errors.append(f"{label}: 'in' is negative")
            continue
        if end > clip["duration"]:
            if end - clip["duration"] <= END_TOLERANCE:
                end = clip["duration"]
            else:
                errors.append(f"{label}: 'out' {end:.2f} is past the end of the clip ({clip['duration']:.2f})")
                continue
        length = end - start
        if length < MIN_SEGMENT:
            errors.append(f"{label}: {length:.2f}s is too short to render; the minimum is {MIN_SEGMENT}s")
            continue
        if length < SHORT_SEGMENT:
            warnings.append(f"{label}: {length:.2f}s is very short and may read as a glitch")

        seg_fit = seg.get("fit", fit)
        if seg_fit not in FITS:
            errors.append(f"{label}: fit {seg_fit!r} is not supported; use crop, pad or blur")
        for axis in ("focus_x", "focus_y"):
            value = _number(seg.get(axis, 0.5))
            if value is None or not 0 <= value <= 1:
                errors.append(f"{label}: {axis} must be a number from 0 to 1")
                value = 0.5
            seg[axis] = value
        volume = _number(seg.get("volume", 1.0))
        if volume is None or not 0 <= volume <= 4:
            errors.append(f"{label}: volume must be a number from 0 to 4")
            volume = 1.0
        for flag, default in (("mute", False), ("captions", True)):
            if not isinstance(seg.get(flag, default), bool):
                errors.append(f"{label}: {flag} must be true or false")
                seg[flag] = default

        words = [w for s in clip.get("speech", []) for w in s.get("words", [])]
        for point_name, moment in (("in", start), ("out", end)):
            word = _word_cut(words, moment)
            if word:
                warnings.append(
                    f"{label}: '{point_name}' at {moment:.2f} cuts into the word \"{word['text']}\" "
                    f"({word['start']:.2f}-{word['end']:.2f}); "
                    f"try {word['start']:.2f} or {word['end']:.2f}"
                )

        for other_start, other_end, other_number in used.get(clip["id"], []):
            overlap = min(end, other_end) - max(start, other_start)
            if overlap > 0.5:
                warnings.append(f"{label}: repeats {overlap:.1f}s of footage already used in segment {other_number}")
        used.setdefault(clip["id"], []).append((start, end, number))

        seg.update({
            "clip": clip["id"], "in": round(start, 3), "out": round(end, 3),
            "fit": seg_fit, "volume": volume,
            "mute": seg.get("mute", False), "captions": seg.get("captions", True),
            "timeline_start": round(timeline, 3),
        })
        normalized_segments.append(seg)
        timeline += length

    out["segments"] = normalized_segments
    out["duration"] = round(timeline, 3)

    target = _number(out.get("target_length"))
    if out.get("target_length") is not None and target is None:
        errors.append("target_length must be a number of seconds")
    elif target and timeline and abs(timeline - target) / target > 0.15:
        warnings.append(f"the cut is {timeline:.1f}s long but target_length is {target:.0f}s")

    # ---- text overlays -----------------------------------------------------
    texts = out.get("text", [])
    if not isinstance(texts, list):
        errors.append("text must be a list")
        texts = []
    normalized_texts = []
    for number, item in enumerate(texts, start=1):
        label = f"text {number}"
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            errors.append(f"{label}: needs a non-empty 'text' string")
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            errors.append(f"{label}: 'start' and 'end' must be seconds on the timeline, with end after start")
            continue
        if start >= timeline:
            warnings.append(f"{label}: starts at {start:.1f}s, after the video ends ({timeline:.1f}s)")
        item_position = item.get("position", "top")
        if item_position not in TEXT_POSITIONS:
            errors.append(f"{label}: position {item_position!r} is not supported; use top, middle or bottom")
        normalized_texts.append({"text": item["text"].strip(), "start": start,
                                 "end": min(end, timeline) if timeline else end,
                                 "position": item_position})
    out["text"] = normalized_texts

    # ---- music -------------------------------------------------------------
    music = out.get("music")
    if music is not None:
        if not isinstance(music, dict) or not isinstance(music.get("file"), str):
            errors.append("music must be an object with a 'file' path, or be left out")
            out["music"] = None
        else:
            music = dict(music)
            path = Path(music["file"]).expanduser()
            if not path.is_absolute():
                path = (edl_dir / path).resolve()
            if not path.is_file():
                errors.append(f"music file not found: {path}")
            music["file"] = str(path)
            volume = _number(music.get("volume", 0.25))
            if volume is None or not 0 <= volume <= 2:
                errors.append("music volume must be a number from 0 to 2")
                volume = 0.25
            music["volume"] = volume
            start = _number(music.get("start", 0))
            if start is None or start < 0:
                errors.append("music start must be zero or more seconds")
                start = 0.0
            music["start"] = start
            if not isinstance(music.get("duck", True), bool):
                errors.append("music duck must be true or false")
            music["duck"] = music.get("duck", True) is not False
            out["music"] = music

    return errors, warnings, out


def report(normalized: Dict[str, Any], index: Dict[str, Any]) -> str:
    names = {c["id"]: c["name"] for c in index.get("clips", [])}
    lines = [f"Timeline ({normalized.get('aspect')}, {fmt_time(normalized.get('duration', 0))}):"]
    for number, seg in enumerate(normalized.get("segments", []), start=1):
        length = seg["out"] - seg["in"]
        note = f"  {seg['note']}" if seg.get("note") else ""
        lines.append(f"  {number:>2}. {fmt_time(seg['timeline_start'])}  {seg['clip']} "
                     f"{seg['in']:.2f}-{seg['out']:.2f} ({length:.2f}s) {names.get(seg['clip'], '')}{note}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("edl", help="path to edl.json")
    parser.add_argument("--index", help="path to clip-index.json (default: next to the EDL)")
    args = parser.parse_args()

    edl_path = Path(args.edl).expanduser().resolve()
    index = load_json(find_index(edl_path, args.index))
    errors, warnings, normalized = validate(load_json(edl_path), index, edl_path.parent)

    if normalized.get("segments"):
        print(report(normalized, index))
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors:
        print(f"\n{len(errors)} error(s). Fix them before rendering.")
        sys.exit(1)
    print(f"\nOK: ready to render ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    try:
        main()
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
