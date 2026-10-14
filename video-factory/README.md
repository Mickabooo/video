# Video Factory

A skill for Claude (and other AI coding agents) that turns a folder of your own video clips into an edited video.

You give it clips and a short brief, such as "a 30-second vertical recap of our trip". Claude studies the footage through contact sheets and transcripts, plans the edit, renders a draft, checks it for problems, and revises. Then it hands you the result, with captions and music.

The video processing all happens on your computer, and the clips themselves are never uploaded. Claude sees only what it reads while editing: the contact sheets, the stills and the transcripts.

## How it works

The work is split the same way a real edit is: Claude makes the creative decisions, and ordinary scripts do the mechanical work.

```
your clips
    │  analyze.py
    ▼
index.md + contact sheets
    │  Claude plans the edit
    ▼
edl.json
    │  render.py
    ▼
draft.mp4 ──▶ review.py ──▶ Claude revises edl.json (up to twice)
    │  render.py --quality final
    ▼
final.mp4
```

1. **Analyze** (`analyze.py`) finds shot changes, quiet stretches and loudness in every clip, and saves a contact sheet for each one. If faster-whisper is installed, it also transcribes speech with the timing of every word.
2. **Plan.** Claude reads the summary, looks at the contact sheets, and writes `plan.md` (the angle, the hook and the structure) and `edl.json` (the exact cuts).
3. **Validate** (`validate_edl.py`) rejects impossible cuts and flags sloppy ones, such as a cut in the middle of a word.
4. **Render** (`render.py`) cuts and reframes each shot and adds captions and text. It mixes in music that drops under speech, and evens out the loudness to the level commonly recommended for online video.
5. **Review** (`review.py`) checks the render for black frames, silence, loudness and length, and makes a contact sheet so Claude can look at the result before you do.

## Requirements

- **Python 3.9 or newer**
- **ffmpeg** with libass and libx264 (most packaged builds have both)
  - macOS: `brew install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg`
  - Linux: `sudo apt install ffmpeg`
- **faster-whisper** (optional, for transcripts and captions): `python3 -m pip install faster-whisper`
  The first run downloads a speech model from Hugging Face (about 500 MB for the default model). After that, transcription works offline.

Check your setup with:

```
python3 skills/video-factory/scripts/doctor.py
```

## Install

### Claude Code

```
/plugin marketplace add YOUR-GITHUB-USERNAME/video-factory
/plugin install video-factory@video-factory
```

Replace `YOUR-GITHUB-USERNAME` with the account this repository lives under.

### Other agents

Copy the `skills/video-factory` folder into your agent's skills folder. For Claude Code without the plugin system, that is `~/.claude/skills/`. For other agents that support the Agent Skills format, check their documentation for where skills go.

The skill runs scripts on your computer, so it needs an agent that can run commands locally.

To use the skill in Claude Code while working inside this repository, link it into the project's skills folder from the repository root:

```
mkdir -p .claude/skills && ln -s ../../skills/video-factory .claude/skills/video-factory
```

## Use it

Ask in plain words:

- "Make a 30-second vertical recap from the clips in ~/Movies/lisbon."
- "Cut these interview clips into a 60-second YouTube Short with captions."
- "Make a montage from this folder with ~/Music/track.mp3, no talking."

Claude asks about anything it needs to know, shows you a draft, and renders the full-quality version once you're happy.

Results go into `factory-output/<name>/` in your working folder:

| File | What it is |
|---|---|
| `final.mp4` | The finished video, 1080 pixels on the short side |
| `final.srt` | Captions as a separate file, for platforms that accept caption uploads |
| `draft.mp4` | The quick 480p preview |
| `plan.md` | What the edit is going for, and what was left out |
| `edl.json` | The edit itself. Edit it by hand and run `render.py` again if you like |

## Run the scripts yourself

The scripts work without an agent too:

```
S=skills/video-factory/scripts
python3 $S/analyze.py ~/Movies/lisbon --out factory-output/lisbon
# write factory-output/lisbon/edl.json (see skills/video-factory/references/edl-format.md)
python3 $S/validate_edl.py factory-output/lisbon/edl.json
python3 $S/render.py factory-output/lisbon/edl.json
python3 $S/review.py factory-output/lisbon/draft.mp4 --edl factory-output/lisbon/edl.json
python3 $S/render.py factory-output/lisbon/edl.json --quality final
```

Run any script with `--help` to see all its options.

## What version 1 can't do yet

- Lay one clip's picture over another clip's sound (B-roll over narration)
- Transitions other than straight cuts and the opening and closing fades
- Cut to the beat of the music
- Track a moving subject when reframing (the crop position is fixed for each shot)
- Use photos as well as video
- Generate new footage, voiceover or music

These are next on the roadmap.

## Tests

```
python3 tests/smoke_test.py
```

This makes a few synthetic clips and runs the whole pipeline on them. It needs only ffmpeg.

## Music and licensing

Video Factory doesn't come with any music. Use tracks you have the rights to. This repository's code is released under the MIT license (see `LICENSE`).
