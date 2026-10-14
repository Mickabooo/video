# Editing principles

These are defaults, not laws. Break one when you have a reason you could explain to the user.

## The opening

- The first one to two seconds decide whether anyone keeps watching. Open on the strongest thing you have: movement, a face, a surprising image, or the best line.
- Never open with setup ("So, um, today we're going to..."). Start at the point where it gets interesting, and let the viewer catch up.
- A good test: if the first shot were the only shot, would it make someone curious?

## Cutting speech

- Cut between words, never inside one. The validator flags cuts that land inside a word and suggests exact word edges.
- Leave a little air around each line. Start about 0.1 s before the first word and end about 0.15–0.25 s after the last one, without running into a neighboring word. A cut placed exactly on a word edge sounds clipped.
- End each segment on a complete thought. Check `draft.srt`: every segment's speech should make sense on its own.
- Remove filler and dead air. The quiet stretches listed in `index.md` inside a stretch of speech are usually pauses you can cut. Keep a pause only when it does something, such as building suspense before a reveal.
- Cut false starts and repeated takes. When the same line was said twice, keep the better delivery.

## Rhythm

- Vary shot length. Equal-length shots feel mechanical.
- For vertical shorts, shots without speech usually work at 1–3 s each. A shot with speech lasts as long as the line, but rarely more than 6–8 s before something changes.
- Landscape and longer videos can hold shots longer.
- If music has a clear beat, cutting near the beat feels deliberate. Version 1 doesn't detect beats, so don't claim the cuts are on the beat.
- Shorter is almost always better. If you need to argue for keeping a shot, cut it.

## Structure

Pick the shape that suits the footage:

- **Recap (a trip, an event, a day):** hook, then mostly in the order things happened (use `created` from the index when it's there), then the best moment near the end, then a closing image.
- **Story:** setup, a turn or problem, then the payoff. Find the payoff first and build backward from it.
- **Talking-head short:** the strongest line as the hook, then two or three points, then a closing line or call to action.
- **Montage:** music carries it, with no speech. Mute the clips, keep shots short, build in intensity, and end on the strongest image.

## Choosing shots

- Leave out anything shaky, out of focus, badly exposed or accidental, unless that roughness is the point.
- When there are near-duplicates, keep the best one only.
- Prefer shots with clear subjects and movement. A static wide shot works as a breather between busier shots, not as the opening.
- Don't put two very similar shots back to back. The cut will look like a mistake.

## Framing for vertical video

- When a landscape shot goes into a `9:16` video with `crop`, only the middle third or so of the frame survives. Check the contact sheet and set `focus_x` toward the subject: about `0.3` keeps a subject on the left, about `0.7` keeps one on the right.
- If the subject moves across the frame, or the shot is a wide view or a group, use `blur` instead of losing half the picture.
- Use `frame.py` to check a crop decision when the contact sheet is too small to judge.

## Captions and text

- Turn captions on whenever there is speech. Many people watch with the sound off.
- Turn captions off for montages and for segments where the speech isn't the point.
- Keep text overlays to five words or fewer, and on screen for at least 1.5 s. Use them for place names, days or a title, not to repeat what someone is saying.
- Never cover a face. Check the review contact sheet.

## Music

- Under speech: `volume` 0.15–0.25, with `duck` on.
- Montage with the clips muted: `volume` 0.6–1.0.
- If the track has a slow intro, skip it with `start`.
- No music is better than the wrong music. If the user didn't supply a track, use the clips' own sound and say so.

## The ending

- End on a strong image or a finished sentence, never partway through a word or a movement.
- Leave about half a second after the last word before the end, so the fade-out doesn't swallow it.
