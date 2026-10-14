#!/usr/bin/env python3
"""Render an edit decision list (edl.json) into a video.

The EDL is validated first; if it has errors, nothing is rendered.

How it works:
  1. Each segment is cut from its source clip and converted to the target
     size, frame rate and audio format, with a tiny audio fade at each end
     so cuts never click.
  2. The segments are joined end to end.
  3. A finishing pass adds captions and text overlays, mixes in music
     (lowered automatically under speech), fades in and out, and evens out
     loudness to about -14 LUFS, the level commonly recommended for online video.

Usage:
  python3 render.py RUN_DIR/edl.json [--quality draft|final] [--out FILE]
                    [--index RUN_DIR/clip-index.json] [--keep-work]

Draft renders are small (480p) and fast, for checking the edit. Final
renders are full size (1080p) and slower.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DRAFT_SIZES, FINAL_SIZES, FactoryError, ffmpeg_base, find_index, fmt_time,
    info, load_json, orientation, probe, run, warn,
)
from validate_edl import validate  # noqa: E402

EDGE_FADE = 0.02        # seconds of audio fade at every cut
WEAK_ENDINGS = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "and",
                "or", "but", "my", "your", "our", "their", "is", "are", "was"}
LOUDNESS = "I=-14:TP=-1.5:LRA=11"

QUALITY = {
    "draft": {"sizes": DRAFT_SIZES, "seg_preset": "ultrafast", "seg_crf": "23",
              "preset": "veryfast", "crf": "28", "audio_bitrate": "128k"},
    "final": {"sizes": FINAL_SIZES, "seg_preset": "veryfast", "seg_crf": "14",
              "preset": "medium", "crf": "18", "audio_bitrate": "192k"},
}


# --------------------------------------------------------------------------- #
# Step 1: segments
# --------------------------------------------------------------------------- #

def video_chain(fit: str, width: int, height: int, focus_x: float, focus_y: float,
                fps: float, length: float) -> str:
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase"
    contain = f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    tail = (f"fps={fps:g},setsar=1,format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration=1,trim=end={length:.3f},setpts=PTS-STARTPTS")
    if fit == "pad":
        return (f"[0:v]setpts=PTS-STARTPTS,{contain},"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,{tail}[v]")
    if fit == "blur":
        small_w, small_h = max(2, width // 8 // 2 * 2), max(2, height // 8 // 2 * 2)
        return (f"[0:v]setpts=PTS-STARTPTS,split[bgsrc][fgsrc];"
                f"[bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
                f"crop={small_w}:{small_h},boxblur=8:2,scale={width}:{height},eq=brightness=-0.08[bg];"
                f"[fgsrc]{contain}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]")
    return (f"[0:v]setpts=PTS-STARTPTS,{cover},"
            f"crop={width}:{height}:(iw-{width})*{focus_x:.3f}:(ih-{height})*{focus_y:.3f},{tail}[v]")


def render_segment(seg: Dict[str, Any], clip: Dict[str, Any], edl: Dict[str, Any],
                   size: Tuple[int, int], quality: Dict[str, str], target: Path) -> None:
    width, height = size
    length = seg["out"] - seg["in"]
    cmd = ffmpeg_base() + ["-ss", f"{seg['in']:.3f}", "-t", f"{length:.3f}", "-i", clip["file"]]
    graph = [video_chain(seg["fit"], width, height, seg["focus_x"], seg["focus_y"], edl["fps"], length)]

    if clip["has_audio"] and not seg["mute"]:
        fade_out = max(length - EDGE_FADE, 0)
        graph.append(
            "[0:a]asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={seg['volume']:.3f},"
            f"afade=t=in:d={EDGE_FADE},afade=t=out:st={fade_out:.3f}:d={EDGE_FADE},"
            f"apad,atrim=end={length:.3f}[a]"
        )
    else:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph.append(f"[1:a]atrim=end={length:.3f}[a]")

    cmd += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", quality["seg_preset"], "-crf", quality["seg_crf"],
        "-pix_fmt", "yuv420p", "-r", f"{edl['fps']:g}",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(target),
    ]
    run(cmd)


# --------------------------------------------------------------------------- #
# Captions and text overlays (one .ass subtitle file)
# --------------------------------------------------------------------------- #

def caption_limits(width: int, height: int) -> Tuple[int, int]:
    """(max characters, max words) per caption for this frame shape."""
    shape = orientation(width, height)
    if shape == "portrait":
        return 20, 4
    if shape == "square":
        return 26, 5
    return 36, 7


def build_captions(edl: Dict[str, Any], clips: Dict[str, Dict[str, Any]],
                   size: Tuple[int, int]) -> List[Dict[str, Any]]:
    """Group transcript words into short captions placed on the output timeline."""
    max_chars, max_words = caption_limits(*size)
    events: List[Dict[str, Any]] = []
    for seg in edl["segments"]:
        if not seg["captions"] or seg["mute"]:
            continue
        clip = clips[seg["clip"]]
        offset = seg["timeline_start"] - seg["in"]
        seg_end = seg["timeline_start"] + (seg["out"] - seg["in"])
        words = []
        for part in clip.get("speech", []):
            for word in part.get("words", []):
                middle = (word["start"] + word["end"]) / 2
                if seg["in"] <= middle <= seg["out"]:
                    words.append({
                        "text": word["text"],
                        "start": max(word["start"] + offset, seg["timeline_start"]),
                        "end": min(word["end"] + offset, seg_end),
                    })
        for phrase in split_phrases(words):
            for chunk in break_phrase(phrase, max_chars, max_words):
                events.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                               "text": " ".join(w["text"] for w in chunk), "limit": seg_end})

    # Hold each caption a little longer so it can be read, without overlapping the next.
    for current, following in zip(events, events[1:] + [None]):
        limit = current["limit"]
        if following is not None:
            limit = min(limit, following["start"])
        current["end"] = max(current["end"], min(current["end"] + 0.35, limit))
        if current["end"] - current["start"] < 0.3:
            current["end"] = max(current["end"], min(current["start"] + 0.3, limit))
    return [e for e in events if e["end"] > e["start"]]


def split_phrases(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Split at punctuation and at pauses of more than half a second."""
    phrases: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for word in words:
        if current and word["start"] - current[-1]["end"] > 0.5:
            phrases.append(current)
            current = []
        current.append(word)
        if re.search(r"[.?!,;:]$", word["text"]):
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    return phrases


def break_phrase(phrase: List[Dict[str, Any]], max_chars: int, max_words: int) -> List[List[Dict[str, Any]]]:
    """Break a phrase into captions of even length that fit the limits.

    Works like line breaking in a word processor: it tries every set of
    break points and keeps the one with the most even captions, avoiding a
    caption that ends on a word like "the" or "to".
    """
    count = len(phrase)
    best: List[Optional[Tuple[float, int]]] = [None] * (count + 1)
    best[0] = (0.0, 0)
    for end in range(1, count + 1):
        for start in range(end - 1, -1, -1):
            chunk = phrase[start:end]
            chars = len(" ".join(w["text"] for w in chunk))
            too_big = (chars > max_chars or len(chunk) > max_words
                       or chunk[-1]["end"] - chunk[0]["start"] > 2.5)
            if too_big and len(chunk) > 1:
                break  # longer chunks starting earlier only get bigger
            if best[start] is None:
                continue
            cost = best[start][0] + (max_chars - min(chars, max_chars)) ** 2
            if end < count and chunk[-1]["text"].lower() in WEAK_ENDINGS:
                cost += 40
            if best[end] is None or cost < best[end][0]:
                best[end] = (cost, start)
    chunks: List[List[Dict[str, Any]]] = []
    end = count
    while end > 0:
        start = best[end][1]
        chunks.insert(0, phrase[start:end])
        end = start
    return chunks


def ass_time(seconds: float) -> str:
    centis = int(round(max(seconds, 0) * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def srt_time(seconds: float) -> str:
    millis = int(round(max(seconds, 0) * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def ass_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def write_ass(path: Path, captions: List[Dict[str, Any]], texts: List[Dict[str, Any]],
              edl: Dict[str, Any], size: Tuple[int, int]) -> None:
    width, height = size
    shape = orientation(width, height)
    short = min(width, height)
    scale = {"portrait": 0.075, "square": 0.065}.get(shape, 0.055)
    caption_size = max(12, int(round(short * scale)))
    title_size = int(round(caption_size * 1.25))
    side = int(round(width * 0.08))
    if edl["caption_position"] == "middle":
        caption_align, caption_margin = 5, 0
    else:
        caption_align = 2
        caption_margin = int(round(height * (0.2 if shape == "portrait" else 0.07)))
    title_margin = int(round(height * 0.1))
    font = edl["font"].replace(",", " ")

    def style(name: str, size_px: int, align: int, margin_v: int) -> str:
        outline = max(1, round(size_px * 0.08))
        shadow = max(0, round(size_px * 0.04))
        return (f"Style: {name},{font},{size_px},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},{align},{side},{side},{margin_v},1")

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style("Caption", caption_size, caption_align, caption_margin),
        style("TextTop", title_size, 8, title_margin),
        style("TextMiddle", title_size, 5, 0),
        style("TextBottom", title_size, 2, title_margin),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for event in captions:
        lines.append(f"Dialogue: 0,{ass_time(event['start'])},{ass_time(event['end'])},"
                     f"Caption,,0,0,0,,{ass_text(event['text'])}")
    for item in texts:
        style_name = {"top": "TextTop", "middle": "TextMiddle", "bottom": "TextBottom"}[item["position"]]
        lines.append(f"Dialogue: 1,{ass_time(item['start'])},{ass_time(item['end'])},"
                     f"{style_name},,0,0,0,,{ass_text(item['text'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(path: Path, captions: List[Dict[str, Any]]) -> None:
    blocks = []
    for number, event in enumerate(captions, start=1):
        blocks.append(f"{number}\n{srt_time(event['start'])} --> {srt_time(event['end'])}\n{event['text']}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Step 3: finishing pass
# --------------------------------------------------------------------------- #

def audio_graph(edl: Dict[str, Any], total: float, loudnorm: str) -> str:
    """Filter graph text that ends in [a]. Input 0 is the joined cut, input 1 is music."""
    parts: List[str] = []
    music = edl.get("music")
    if music:
        fade_start = max(total - 2.0, 0)
        parts.append(
            f"[1:a]atrim=end={total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={music['volume']:.3f},afade=t=in:d=0.3,"
            f"afade=t=out:st={fade_start:.3f}:d={min(2.0, total):.3f}[music]"
        )
        if music["duck"]:
            parts.append("[0:a]asplit=2[speech][key]")
            parts.append("[music][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[ducked]")
            parts.append("[speech][ducked]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            parts.append("[0:a][music]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        parts.append("[0:a]anull[mix]")

    chain = f"[mix]{loudnorm},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if edl["fade"]:
        chain += f",afade=t=in:d=0.3,afade=t=out:st={max(total - 0.5, 0):.3f}:d=0.5"
    parts.append(chain + "[a]")
    return ";".join(parts)


def measure_loudness(cmd_inputs: List[str], edl: Dict[str, Any], total: float, cwd: Path) -> Optional[str]:
    """First loudnorm pass. Returns the filter for a precise second pass, or None."""
    graph = audio_graph(edl, total, f"loudnorm={LOUDNESS}:print_format=json")
    proc = run(ffmpeg_base() + cmd_inputs + ["-filter_complex", graph, "-map", "[a]", "-f", "null", "-"], cwd=cwd)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "", re.S)
    if not match:
        return None
    stats = json.loads(match.group(0))
    try:
        if float(stats["input_i"]) < -70:  # silence: nothing to normalize
            return None
    except (KeyError, ValueError):
        return None
    return (f"loudnorm={LOUDNESS}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true")


def finish(joined: Path, ass_path: Optional[Path], edl: Dict[str, Any], total: float,
           quality_name: str, quality: Dict[str, str], output: Path, work: Path) -> None:
    inputs = ["-i", str(joined)]
    if edl.get("music"):
        inputs += ["-stream_loop", "-1", "-ss", f"{edl['music']['start']:.3f}", "-i", edl["music"]["file"]]

    video = "[0:v]"
    filters = []
    if edl["fade"]:
        filters.append(f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(total - 0.5, 0):.3f}:d=0.5")
    if ass_path is not None:
        filters.append(f"ass={ass_path.name}")  # relative path, run from the work folder: no escaping needed
    video += (",".join(filters) if filters else "null") + "[v]"

    loudnorm = f"loudnorm={LOUDNESS}"
    if quality_name == "final":
        info("measuring loudness...")
        loudnorm = measure_loudness(inputs, edl, total, work) or "anull"
    graph = video + ";" + audio_graph(edl, total, loudnorm)

    run(ffmpeg_base() + inputs + [
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", quality["preset"], "-crf", quality["crf"],
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", f"{edl['fps']:g}",
        "-c:a", "aac", "-b:a", quality["audio_bitrate"], "-ar", "48000",
        "-movflags", "+faststart", "-t", f"{total:.3f}",
        str(output),
    ], cwd=work)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("edl", help="path to edl.json")
    parser.add_argument("--quality", choices=("draft", "final"), default="draft",
                        help="draft: 480p, fast (default). final: 1080p, slower")
    parser.add_argument("--out", help="output file (default: draft.mp4 or final.mp4 next to the EDL)")
    parser.add_argument("--index", help="path to clip-index.json (default: next to the EDL)")
    parser.add_argument("--keep-work", action="store_true", help="keep the intermediate files")
    args = parser.parse_args()

    edl_path = Path(args.edl).expanduser().resolve()
    run_dir = edl_path.parent
    index = load_json(find_index(edl_path, args.index))
    errors, warnings, edl = validate(load_json(edl_path), index, run_dir)
    for message in warnings:
        warn(message)
    if errors:
        for message in errors:
            print(f"error: {message}", file=sys.stderr)
        raise FactoryError("the EDL has errors, so nothing was rendered. Run validate_edl.py for details.")

    clips = {c["id"]: c for c in index["clips"]}
    for clip_id in {s["clip"] for s in edl["segments"]}:
        if not Path(clips[clip_id]["file"]).is_file():
            raise FactoryError(f"source clip for {clip_id} is missing: {clips[clip_id]['file']}")

    quality = QUALITY[args.quality]
    size = quality["sizes"][edl["aspect"]]
    total = edl["duration"]
    output = Path(args.out).expanduser().resolve() if args.out else run_dir / f"{args.quality}.mp4"
    work = run_dir / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    try:
        info(f"rendering {len(edl['segments'])} segment(s) at {size[0]}x{size[1]}, {fmt_time(total)} total")
        listing = []
        for number, seg in enumerate(edl["segments"], start=1):
            info(f"  segment {number}/{len(edl['segments'])}: {seg['clip']} {seg['in']:.2f}-{seg['out']:.2f}")
            target = work / f"seg_{number:04d}.mkv"
            render_segment(seg, clips[seg["clip"]], edl, size, quality, target)
            listing.append(f"file '{target.name}'")
        (work / "segments.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")

        info("joining segments...")
        joined = work / "joined.mkv"
        run(ffmpeg_base() + ["-f", "concat", "-safe", "0", "-i", "segments.txt", "-c", "copy", joined.name], cwd=work)

        captions = build_captions(edl, clips, size) if edl["captions"] else []
        ass_path: Optional[Path] = None
        if captions or edl["text"]:
            ass_path = work / "captions.ass"
            write_ass(ass_path, captions, edl["text"], edl, size)
        if captions:
            write_srt(output.with_suffix(".srt"), captions)

        info("finishing: captions, music, loudness...")
        output.parent.mkdir(parents=True, exist_ok=True)
        finish(joined, ass_path, edl, total, args.quality, quality, output, work)
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)

    result = probe(output)
    print(f"Rendered {output} ({result['width']}x{result['height']}, {fmt_time(result['duration'])}).")
    if captions:
        print(f"Captions: {output.with_suffix('.srt')}")
    print(f"Check it next: python3 review.py {output}")


if __name__ == "__main__":
    try:
        main()
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
