---
name: video-factory
description: Turn the user's own video clips into an edited video with captions and music. Analyzes the footage (shots, speech, loudness), plans the edit, renders a draft, checks it, and revises. Use when the user wants to edit, cut, trim or assemble clips into a video, short, reel, recap, montage or highlight reel.
---

# Video Factory

You are the editor. The scripts handle the mechanical work: analysis, cutting, captions, mixing and rendering. You make the creative decisions: what to keep, in what order, and why.

In the commands below, `SKILL_DIR` means the folder that contains this file. Write out the real path when you run them.

## 1. Check the tools

```
python3 "SKILL_DIR/scripts/doctor.py"
```

If anything required is missing, stop and give the user the install command that doctor printed. A missing `faster-whisper` is not a blocker: everything works without it except transcripts and captions.

## 2. Get the brief

Work out four things. Ask only about what the user hasn't already told you, in a single message of no more than three questions:

- **Where it will be posted.** This sets the aspect ratio and a sensible length.
- **How long it should be.**
- **What it's about**, and any moments that must be in it or must be left out.
- **Music.** The path to a track they have the rights to, or none.

If the user says to just make something, use these defaults and say which ones you used:

| Destination | `aspect` | Length |
|---|---|---|
| TikTok, Reels, Shorts | `9:16` | 20–45 s |
| YouTube | `16:9` | whatever the footage supports |
| Instagram feed | `4:5` | 15–30 s |
| Not stated | `9:16` if most clips are vertical, otherwise `16:9` | 30–60 s |

## 3. Analyze the footage

```
python3 "SKILL_DIR/scripts/analyze.py" CLIPS --out RUN_DIR
```

`CLIPS` is one or more clip files or folders. Add `--recursive` to include subfolders. Choose `RUN_DIR` as `factory-output/<short-name>` inside the user's working folder. The script prints `RUN_DIR=...` when it finishes. Analysis is cached, so running it again on the same clips is fast.

## 4. Look at the footage

1. Read `RUN_DIR/index.md` in full.
2. Open every contact sheet in `RUN_DIR/sheets/`. Each frame is labeled with its number and its time in seconds.
3. For a closer look at a moment (to judge a crop, a face, or text on screen), save a full-size still:
   `python3 "SKILL_DIR/scripts/frame.py" RUN_DIR c03 12.5`

For each clip, note what it shows, its best moments, and anything unusable: shaky, out of focus, too dark, accidental, or a weaker take of something you have elsewhere.

## 5. Plan the edit

Read `references/editing-principles.md`. Then write `RUN_DIR/plan.md` with:

- **Angle:** one sentence on what the video is about.
- **Hook:** what happens in the first two seconds.
- **Structure:** the beats in order, with the clips and times for each.
- **Left out:** what you cut and why, so the user can ask for it back.

## 6. Write the EDL

Read `references/edl-format.md`, then write `RUN_DIR/edl.json` and validate it:

```
python3 "SKILL_DIR/scripts/validate_edl.py" RUN_DIR/edl.json
```

Fix every error. Fix every "cuts into the word" warning with the times it suggests. Treat other warnings as prompts to reconsider, not orders.

## 7. Render a draft and review it

```
python3 "SKILL_DIR/scripts/render.py" RUN_DIR/edl.json
python3 "SKILL_DIR/scripts/review.py" RUN_DIR/draft.mp4 --edl RUN_DIR/edl.json
```

Then check the result yourself:

- Fix everything listed under "Problems found".
- Open the contact sheet from `RUN_DIR/review/`. Check the framing, check that captions and text don't cover faces, and check that the opening frame works as a hook.
- If captions are on, read `RUN_DIR/draft.srt` from start to finish. It should read as complete thoughts, with no sentence cut off partway through.

Revise `edl.json` and render again, at most two more times. Stop when the review is clean and any remaining changes would be a matter of taste.

## 8. Show the user

Give them the path to `draft.mp4`, its length, two or three sentences on the edit you made, and the choices they might want to change (hook, order, length, music level). Ask before rendering the final version unless they told you to go straight through.

When they ask for changes, edit `edl.json` and render the draft again. Don't start over unless they ask you to.

## 9. Render the final version

```
python3 "SKILL_DIR/scripts/render.py" RUN_DIR/edl.json --quality final
python3 "SKILL_DIR/scripts/review.py" RUN_DIR/final.mp4 --edl RUN_DIR/edl.json
```

Report the paths to `final.mp4` and, if there are captions, `final.srt`. Most platforms accept the `.srt` as an uploaded caption track.

## Rules

- Never modify, move, rename or delete the user's clips. Everything you create goes in `RUN_DIR`.
- Base every cut point on the index (speech, shots or quiet stretches) or on a frame you have looked at. Never guess a time.
- Don't use a clip whose contact sheet you haven't looked at, and don't describe footage you haven't seen.
- Only use music the user supplied or pointed you to. Never download music.
- If the footage can't support the requested length or story, say so. Don't pad the video with weak shots to reach a target.
- If a script fails, read its error message: it says what to fix. Run `doctor.py` if a tool seems to be missing.

## Without a transcript

If the clips weren't transcribed (faster-whisper isn't installed, or the model couldn't be downloaded), `index.md` says so. You can still edit using the shots, quiet stretches and contact sheets, but there will be no captions, and you can't be sure a cut won't fall mid-sentence. Tell the user, and mention that `python3 -m pip install faster-whisper` followed by running `analyze.py` again adds transcripts.

## Files in a run folder

| File | Written by | What it is |
|---|---|---|
| `clip-index.json` | analyze.py | Full analysis, including word timings |
| `index.md` | analyze.py | The summary to read when planning |
| `sheets/<id>.jpg` | analyze.py | One contact sheet per clip |
| `plan.md` | you | The editorial plan |
| `edl.json` | you | The edit itself |
| `draft.mp4`, `final.mp4` | render.py | Rendered videos |
| `draft.srt`, `final.srt` | render.py | Captions as a separate file |
| `review/` | review.py | Contact sheets and checks for each render |
| `stills/` | frame.py | Full-size stills |
