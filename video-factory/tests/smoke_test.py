#!/usr/bin/env python3
"""End-to-end smoke test: synthetic clips -> analyze -> validate -> render -> review.

Needs only Python and ffmpeg. Runs in a temporary folder and uses its own
cache, so it leaves nothing behind.

Usage:
  python3 tests/smoke_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "video-factory" / "scripts"


def sh(*args: str, env: dict) -> str:
    proc = subprocess.run(list(args), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"FAILED: {' '.join(args[:3])}")
    return proc.stdout


def make_clip(path: Path, video: str, seconds: float, with_audio: bool, env: dict) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{video}:d={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    sh(*cmd, env=env)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vf-test-") as tmp_name:
        tmp = Path(tmp_name)
        env = dict(os.environ, VIDEO_FACTORY_CACHE=str(tmp / "cache"))
        clips = tmp / "clips"
        clips.mkdir()
        make_clip(clips / "clip1.mp4", "testsrc2=s=1280x720:r=30", 6, True, env)
        make_clip(clips / "clip2.mp4", "smptebars=s=720x1280:r=25", 4, True, env)
        make_clip(clips / "clip10.mp4", "testsrc=s=640x640:r=24", 3, False, env)
        run_dir = tmp / "run"
        py = sys.executable

        print("analyze...")
        out = sh(py, str(SCRIPTS / "analyze.py"), str(clips), "--out", str(run_dir), "--no-transcribe", env=env)
        assert f"RUN_DIR={run_dir}" in out, out
        index = json.loads((run_dir / "clip-index.json").read_text())
        assert [c["name"] for c in index["clips"]] == ["clip1.mp4", "clip2.mp4", "clip10.mp4"], "natural sort order"
        assert index["clips"][1]["orientation"] == "portrait"
        assert index["clips"][2]["has_audio"] is False
        for clip in index["clips"]:
            assert (run_dir / clip["sheet"]).is_file(), clip["sheet"]

        print("validate...")
        edl = {
            "aspect": "9:16",
            "segments": [
                {"clip": "c01", "in": 0.5, "out": 2.5, "focus_x": 0.3},
                {"clip": "c02", "in": 0.0, "out": 2.0},
                {"clip": "c03", "in": 0.0, "out": 1.5, "fit": "blur"},
            ],
            "text": [{"text": "Smoke test", "start": 0, "end": 1.5}],
        }
        (run_dir / "edl.json").write_text(json.dumps(edl))
        out = sh(py, str(SCRIPTS / "validate_edl.py"), str(run_dir / "edl.json"), env=env)
        assert "OK: ready to render" in out, out

        bad = dict(edl, segments=[{"clip": "c09", "in": 0, "out": 1}])
        (run_dir / "bad.json").write_text(json.dumps(bad))
        proc = subprocess.run([py, str(SCRIPTS / "validate_edl.py"), str(run_dir / "bad.json")],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 1 and "not in the clip index" in proc.stdout, proc.stdout

        print("render...")
        sh(py, str(SCRIPTS / "render.py"), str(run_dir / "edl.json"), env=env)
        draft = run_dir / "draft.mp4"
        assert draft.is_file()
        probe = json.loads(sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                              "-of", "json", str(draft), env=env))
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (480, 854), video
        assert any(s["codec_type"] == "audio" for s in probe["streams"]), "audio track"
        assert abs(float(probe["format"]["duration"]) - 5.5) < 0.15, probe["format"]["duration"]

        print("review...")
        out = sh(py, str(SCRIPTS / "review.py"), str(draft), "--edl", str(run_dir / "edl.json"), env=env)
        assert (run_dir / "review" / "draft-sheet.jpg").is_file()
        assert "length is" not in out, out

    print("PASS")


if __name__ == "__main__":
    main()
