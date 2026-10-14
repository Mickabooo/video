"""Shared helpers for the video-factory scripts.

Everything here is plain Python 3.9+ standard library plus the ffmpeg and
ffprobe command-line tools. Nothing in this module talks to the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
    ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".wmv",
}

# Output sizes for each supported aspect ratio, as (width, height).
FINAL_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
DRAFT_SIZES = {
    "9:16": (480, 854),
    "16:9": (854, 480),
    "1:1": (480, 480),
    "4:5": (480, 600),
}

CACHE_VERSION = 1


class FactoryError(Exception):
    """An error with a message that is safe to show the user as-is."""


def fail(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def info(message: str) -> None:
    print(message, file=sys.stderr)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FactoryError(
            f"'{name}' was not found on your PATH. "
            "Run scripts/doctor.py for install instructions."
        )
    return path


def run(cmd: List[str], cwd: Optional[Path] = None, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and raise FactoryError with the useful part of stderr if it fails."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise FactoryError(f"command failed: {' '.join(cmd[:3])} ...\n{tail}")
    return proc


def ffmpeg_base() -> List[str]:
    return [require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y"]


def parse_rate(value: Optional[str]) -> float:
    """Turn an ffprobe rate such as '30000/1001' into a float. Returns 0 if unknown."""
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _rotation(stream: Dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            return int(tag) % 360
        except ValueError:
            pass
    return 0


def probe(path: Path) -> Dict[str, Any]:
    """Return the facts about a media file that the pipeline needs.

    Width and height are the *display* size: rotation metadata and
    non-square pixels are already applied, because ffmpeg applies both
    when it decodes.
    """
    proc = run([
        require_tool("ffprobe"), "-v", "error",
        "-show_format", "-show_streams", "-of", "json", str(path),
    ])
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video"
         and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    duration = parse_rate(fmt.get("duration"))
    if not duration and video:
        duration = parse_rate(video.get("duration"))

    result: Dict[str, Any] = {
        "duration": round(duration, 3),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "created": (fmt.get("tags") or {}).get("creation_time"),
    }
    if video:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        sar = parse_rate(video.get("sample_aspect_ratio"))
        if sar and abs(sar - 1.0) > 0.01:
            width = int(round(width * sar))
        rotation = _rotation(video)
        if rotation in (90, 270):
            width, height = height, width
        fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
        result.update({
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "rotation": rotation,
            "video_codec": video.get("codec_name"),
        })
    return result


def orientation(width: int, height: int) -> str:
    if not width or not height:
        return "unknown"
    ratio = width / height
    if ratio > 1.05:
        return "landscape"
    if ratio < 0.95:
        return "portrait"
    return "square"


def fingerprint(path: Path) -> str:
    """A fast content fingerprint: file size plus the first and last megabyte.

    Good enough to tell whether a clip changed between runs without reading
    gigabytes of footage.
    """
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode())
    chunk = 1024 * 1024
    with path.open("rb") as handle:
        digest.update(handle.read(chunk))
        if size > chunk:
            handle.seek(max(size - chunk, 0))
            digest.update(handle.read(chunk))
    return digest.hexdigest()[:16]


def cache_dir() -> Path:
    base = os.environ.get("VIDEO_FACTORY_CACHE")
    if base:
        path = Path(base)
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / "video-factory"
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "video-factory" / "cache"
    else:
        path = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "video-factory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise FactoryError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise FactoryError(f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}")


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp.replace(path)


def natural_key(text: str) -> List[Any]:
    """Sort 'clip2' before 'clip10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def fmt_time(seconds: float) -> str:
    """12.5 -> '0:12.5', 75.25 -> '1:15.2'."""
    seconds = max(seconds, 0.0)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:04.1f}"


def find_index(edl_path: Path, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidate = edl_path.parent / "clip-index.json"
    if candidate.exists():
        return candidate
    raise FactoryError(
        f"no clip-index.json next to {edl_path.name}. Pass --index with its path."
    )
