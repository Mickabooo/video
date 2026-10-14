#!/usr/bin/env python3
"""Analyze a set of clips so an editor (human or Claude) can plan a cut.

For every clip this finds shot changes, quiet stretches, overall loudness
and (if faster-whisper is installed) a word-timed transcript. It also saves
one contact sheet image per clip so the footage can be judged by eye
without opening every file.

Outputs, inside the run folder:
  clip-index.json   full data, including word timings (used by render.py)
  index.md          compact summary to read when planning the edit
  sheets/<id>.jpg   contact sheet for each clip

Original clips are only read, never changed.

Usage:
  python3 analyze.py CLIPS... [--out RUN_DIR] [--recursive] [--no-transcribe]
                     [--model small] [--language en] [--frames 12]
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CACHE_VERSION, VIDEO_EXTENSIONS, FactoryError, cache_dir, ffmpeg_base,
    fingerprint, fmt_time, info, load_json, natural_key, orientation, probe,
    run, save_json, warn,
)

MIN_SHOT = 0.5          # shots shorter than this are merged into the previous one
SILENCE_DB = -35        # quieter than this counts as silence
SILENCE_MIN = 0.6       # seconds of quiet before it is reported


# --------------------------------------------------------------------------- #
# Finding clips
# --------------------------------------------------------------------------- #

def collect_clips(inputs: List[str], recursive: bool, out_dir: Path) -> List[Path]:
    found: List[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_file():
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                found.append(path)
            else:
                warn(f"skipping {path.name}: not a recognized video file")
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            for child in path.glob(pattern):
                if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if any(part.startswith(".") for part in child.relative_to(path).parts):
                    continue
                if out_dir in child.parents:
                    continue
                found.append(child.resolve())
        else:
            warn(f"skipping {raw}: no such file or folder")
    unique = sorted(set(found), key=lambda p: natural_key(p.name))
    return unique


# --------------------------------------------------------------------------- #
# Shots, silences, loudness (one ffmpeg pass per clip)
# --------------------------------------------------------------------------- #

def scan_clip(path: Path, meta: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    cmd = ffmpeg_base() + ["-i", str(path), "-sn", "-dn",
                           "-vf", f"scale=256:-2,select='gt(scene,{threshold})',showinfo"]
    if meta["has_audio"]:
        cmd += ["-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN},volumedetect"]
    else:
        cmd += ["-an"]
    cmd += ["-f", "null", "-"]
    stderr = run(cmd).stderr or ""

    duration = meta["duration"]
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", stderr)})
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    shots: List[Dict[str, float]] = []
    for start, end in zip(bounds, bounds[1:]):
        if shots and end - start < MIN_SHOT:
            shots[-1]["end"] = round(end, 3)
        elif shots and shots[-1]["end"] - shots[-1]["start"] < MIN_SHOT:
            shots[-1]["end"] = round(end, 3)
        else:
            shots.append({"start": round(start, 3), "end": round(end, 3)})

    silences: List[Dict[str, float]] = []
    pending: Optional[float] = None
    for kind, value in re.findall(r"silence_(start|end): (-?[0-9.]+)", stderr):
        number = max(float(value), 0.0)
        if kind == "start":
            pending = number
        elif pending is not None:
            silences.append({"start": round(pending, 3), "end": round(min(number, duration), 3)})
            pending = None
    if pending is not None and duration - pending >= SILENCE_MIN:
        silences.append({"start": round(pending, 3), "end": round(duration, 3)})

    mean = re.search(r"mean_volume: (-?[0-9.]+) dB", stderr)
    peak = re.search(r"max_volume: (-?[0-9.]+) dB", stderr)
    return {
        "shots": shots,
        "silences": silences,
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "max_volume_db": float(peak.group(1)) if peak else None,
    }


# --------------------------------------------------------------------------- #
# Contact sheets
# --------------------------------------------------------------------------- #

_DRAWTEXT_OK: Optional[bool] = None


def sample_times(shots: List[Dict[str, float]], duration: float, limit: int) -> List[float]:
    """One frame from the middle of each shot, topped up or thinned to `limit`."""
    times = [(s["start"] + s["end"]) / 2 for s in shots]
    if len(times) > limit:
        step = len(times) / limit
        times = [times[int(i * step)] for i in range(limit)]
    elif len(times) < limit:
        wanted = min(limit, max(1, int(duration // 2)))
        if wanted > len(times):
            even = [duration * (i + 0.5) / wanted for i in range(wanted)]
            times = sorted(set(round(t, 2) for t in times + even))
            while len(times) > wanted:
                gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
                del times[gaps.index(min(gaps)) + 1]
    safe_end = max(duration - 0.1, 0.0)
    return [round(min(max(t, 0.0), safe_end), 2) for t in times]


def make_sheet(path: Path, meta: Dict[str, Any], times: List[float], sheet_path: Path) -> None:
    """Save a grid of frames from `path` at `times`, each labeled with its number and time."""
    global _DRAWTEXT_OK
    landscape = meta.get("width", 16) >= meta.get("height", 9)
    cell_w, cell_h = (320, 180) if landscape else (180, 320)
    if meta.get("width") and meta.get("height") and landscape:
        cell_h = max(2, int(round(320 * meta["height"] / meta["width"] / 2)) * 2)
    elif meta.get("width") and meta.get("height"):
        cell_w = max(2, int(round(320 * meta["width"] / meta["height"] / 2)) * 2)
    columns = 4 if landscape else 6
    columns = min(columns, len(times))
    rows = math.ceil(len(times) / columns)

    with tempfile.TemporaryDirectory(prefix="vf-sheet-") as tmp_name:
        tmp = Path(tmp_name)
        for number, moment in enumerate(times, start=1):
            base = (f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                    f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color=black")
            label = (f",drawtext=text='#{number}  {moment:.1f}s':x=6:y=h-th-6:"
                     "fontsize=16:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4")
            target = tmp / f"cell_{number:03d}.jpg"
            attempts = [base + label, base] if _DRAWTEXT_OK is not False else [base]
            for index, vf in enumerate(attempts):
                try:
                    run(ffmpeg_base() + ["-ss", f"{moment:.3f}", "-i", str(path),
                                         "-frames:v", "1", "-vf", vf, "-q:v", "3", str(target)])
                    if vf != base:
                        _DRAWTEXT_OK = True
                    break
                except FactoryError:
                    if vf != base:
                        _DRAWTEXT_OK = False
                    if index == len(attempts) - 1:
                        raise
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        run(ffmpeg_base() + ["-framerate", "1", "-i", str(tmp / "cell_%03d.jpg"),
                             "-vf", f"tile={columns}x{rows}:padding=4:margin=4:color=0x202020",
                             "-frames:v", "1", "-q:v", "3", str(sheet_path)])


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #

class Transcriber:
    def __init__(self, model_name: str, language: Optional[str]):
        from faster_whisper import WhisperModel  # imported lazily: optional dependency
        info(f"loading Whisper model '{model_name}' (the first run downloads it)...")
        self.model = WhisperModel(model_name, device="auto", compute_type="int8")
        self.language = language

    def transcribe(self, path: Path) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="vf-audio-") as tmp_name:
            wav = Path(tmp_name) / "audio.wav"
            run(ffmpeg_base() + ["-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                                 "-c:a", "pcm_s16le", str(wav)])
            segments, details = self.model.transcribe(
                str(wav),
                language=self.language,
                word_timestamps=True,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            speech = []
            for seg in segments:
                if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                    continue  # almost certainly a hallucination over noise
                words = [
                    {"start": round(w.start, 3), "end": round(w.end, 3),
                     "text": w.word.strip(), "p": round(w.probability, 2)}
                    for w in (seg.words or []) if w.word.strip()
                ]
                text = seg.text.strip()
                if not text:
                    continue
                speech.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                               "text": text, "words": words})
        return {"language": details.language, "speech": speech}


# --------------------------------------------------------------------------- #
# Writing the summary
# --------------------------------------------------------------------------- #

def write_markdown(index: Dict[str, Any], target: Path) -> None:
    clips = index["clips"]
    total = sum(c["duration"] for c in clips)
    lines = [
        "# Clip index",
        "",
        f"{len(clips)} clips, {fmt_time(total)} of footage in total.",
    ]
    if index["transcribed"]:
        lines.append(f"Speech transcribed with Whisper model '{index['model']}'. "
                     "Times are seconds from the start of each clip.")
    else:
        lines.append("Speech was not transcribed, so there are no transcripts below.")
    lines.append("")

    for clip in clips:
        lines.append(f"## {clip['id']}: {clip['name']}")
        facts = [fmt_time(clip["duration"]),
                 f"{clip['width']}x{clip['height']} {clip['orientation']}",
                 f"{clip['fps']:g} fps"]
        if clip["has_audio"]:
            mean = clip.get("mean_volume_db")
            facts.append(f"audio, mean {mean:.0f} dB" if mean is not None else "audio")
        else:
            facts.append("no audio")
        if clip.get("created"):
            facts.append(f"recorded {clip['created'][:16].replace('T', ' ')}")
        lines.append("- " + ", ".join(facts))
        cells = ", ".join(f"#{f['cell']} {f['time']:.1f}s" for f in clip["sheet_frames"])
        lines.append(f"- Contact sheet `{clip['sheet']}`: {cells}")
        shots = ", ".join(f"{s['start']:.1f}-{s['end']:.1f}" for s in clip["shots"])
        lines.append(f"- Shots: {shots}")
        if clip["silences"]:
            quiet = ", ".join(f"{s['start']:.1f}-{s['end']:.1f}" for s in clip["silences"])
            lines.append(f"- Quiet stretches: {quiet}")
        if clip.get("speech"):
            lines.append("- Speech:")
            for seg in clip["speech"]:
                lines.append(f"  - [{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}")
        elif index["transcribed"] and clip["has_audio"]:
            lines.append("- Speech: none detected")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clips", nargs="+", help="clip files and/or folders of clips")
    parser.add_argument("--out", help="run folder to create (default: ./factory-output/<date-time>)")
    parser.add_argument("--recursive", action="store_true", help="also look inside subfolders")
    parser.add_argument("--no-transcribe", action="store_true", help="skip speech transcription")
    parser.add_argument("--model", default="small",
                        help="Whisper model: tiny, base, small, medium, large-v3 (default: small)")
    parser.add_argument("--language", help="spoken language code such as en or es (default: detect)")
    parser.add_argument("--frames", type=int, default=12, help="contact sheet frames per clip (default: 12)")
    parser.add_argument("--scene-threshold", type=float, default=0.3,
                        help="shot change sensitivity, 0-1, lower finds more (default: 0.3)")
    args = parser.parse_args()

    if args.out:
        run_dir = Path(args.out).expanduser().resolve()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = (Path.cwd() / "factory-output" / stamp).resolve()

    clips = collect_clips(args.clips, args.recursive, run_dir)
    if not clips:
        raise FactoryError("no video files found in the paths given")
    run_dir.mkdir(parents=True, exist_ok=True)
    info(f"found {len(clips)} clip(s); writing to {run_dir}")

    transcriber: Optional[Transcriber] = None
    transcribe = not args.no_transcribe
    if transcribe:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            warn("faster-whisper is not installed, so speech will not be transcribed. "
                 "Install it with: python3 -m pip install faster-whisper")
            transcribe = False

    cache = cache_dir()
    width = max(2, len(str(len(clips))))
    entries: List[Dict[str, Any]] = []

    for number, path in enumerate(clips, start=1):
        clip_id = f"c{number:0{width}d}"
        info(f"[{number}/{len(clips)}] {path.name}")
        meta = probe(path)
        if not meta["has_video"] or not meta["duration"]:
            warn(f"skipping {path.name}: no readable video stream")
            continue

        key = fingerprint(path)
        scan_file = cache / f"{key}-scan{CACHE_VERSION}-{args.scene_threshold}.json"
        if scan_file.exists():
            scan = load_json(scan_file)
        else:
            scan = scan_clip(path, meta, args.scene_threshold)
            save_json(scan_file, scan)

        times = sample_times(scan["shots"], meta["duration"], max(1, args.frames))
        sheet_rel = f"sheets/{clip_id}.jpg"
        make_sheet(path, meta, times, run_dir / sheet_rel)

        entry: Dict[str, Any] = {
            "id": clip_id,
            "name": path.name,
            "file": str(path),
            "duration": meta["duration"],
            "width": meta["width"],
            "height": meta["height"],
            "orientation": orientation(meta["width"], meta["height"]),
            "fps": meta["fps"],
            "rotation": meta["rotation"],
            "has_audio": meta["has_audio"],
            "created": meta["created"],
            "mean_volume_db": scan["mean_volume_db"],
            "shots": scan["shots"],
            "silences": scan["silences"],
            "sheet": sheet_rel,
            "sheet_frames": [{"cell": i, "time": t} for i, t in enumerate(times, start=1)],
            "speech": [],
            "language": None,
        }

        if transcribe and meta["has_audio"]:
            lang_key = args.language or "auto"
            words_file = cache / f"{key}-speech{CACHE_VERSION}-{args.model}-{lang_key}.json"
            result = load_json(words_file) if words_file.exists() else None
            if result is None:
                if transcriber is None:
                    try:
                        transcriber = Transcriber(args.model, args.language)
                    except Exception as exc:  # download blocked, bad model name, etc.
                        warn(f"could not load the Whisper model '{args.model}' ({str(exc)[:200]}). "
                             "Continuing without transcripts.")
                        transcribe = False
                if transcriber is not None:
                    result = transcriber.transcribe(path)
                    save_json(words_file, result)
            if result is not None:
                entry["speech"] = result["speech"]
                entry["language"] = result["language"]

        entries.append(entry)

    if not entries:
        raise FactoryError("none of the files could be read as video")

    transcribed = transcribe or any(c["language"] for c in entries)
    index = {
        "version": 1,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "transcribed": transcribed,
        "model": args.model if transcribed else None,
        "clips": entries,
    }
    save_json(run_dir / "clip-index.json", index)
    write_markdown(index, run_dir / "index.md")

    total = sum(c["duration"] for c in entries)
    print(f"Analyzed {len(entries)} clip(s), {fmt_time(total)} of footage.")
    print(f"Read next: {run_dir / 'index.md'}")
    print(f"Contact sheets: {run_dir / 'sheets'}")
    print(f"RUN_DIR={run_dir}")


if __name__ == "__main__":
    try:
        main()
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
