# EDL format (version 1)

The EDL (edit decision list) is a JSON file that describes the whole edit. `render.py` follows it exactly and makes no creative decisions of its own.

All times are in seconds. Segment `in` and `out` times are measured from the start of the **source clip**. Text overlay `start` and `end` times are measured from the start of the **finished video**.

## Example

```json
{
  "version": 1,
  "title": "Lisbon weekend",
  "aspect": "9:16",
  "fps": 30,
  "fit": "crop",
  "target_length": 30,
  "captions": true,
  "segments": [
    { "clip": "c04", "in": 12.30, "out": 15.10, "focus_x": 0.35, "note": "hook: tram passing" },
    { "clip": "c01", "in": 2.05,  "out": 7.80,  "note": "intro line" },
    { "clip": "c07", "in": 0.00,  "out": 2.40,  "mute": true, "fit": "blur" }
  ],
  "text": [
    { "text": "Day 1", "start": 0.0, "end": 1.8, "position": "top" }
  ],
  "music": { "file": "/Users/me/Music/track.mp3", "volume": 0.2, "duck": true }
}
```

## Top-level fields

| Field | Default | Meaning |
|---|---|---|
| `version` | `1` | Format version. Always `1`. |
| `title` | none | For your reference only. It is not drawn on the video. |
| `aspect` | `"16:9"` | `"9:16"`, `"16:9"`, `"1:1"` or `"4:5"`. Final renders are 1080 pixels on the short side. |
| `fps` | `30` | Output frame rate. Use `30` unless most of the footage is 24, 25 or 60. |
| `fit` | `"crop"` | How footage of a different shape fills the frame. See "Fit" below. |
| `target_length` | none | Intended length in seconds. The validator warns if the cut is more than 15% off. |
| `captions` | `true` if transcribed | Burn the transcript into the video as captions. |
| `caption_position` | `"bottom"` | `"bottom"` or `"middle"`. Bottom captions sit above the area where social apps put their buttons. |
| `font` | `"Arial"` | Font for captions and text. Any installed font name works. If the font isn't installed, another font is used instead. |
| `fade` | `true` | Fade in from black and out to black, with the sound fading too. |
| `segments` | required | The shots, in the order they play. |
| `text` | `[]` | Titles and labels drawn over the video. |
| `music` | none | A music track mixed under everything. |

## Segments

| Field | Default | Meaning |
|---|---|---|
| `clip` | required | Clip id from the index, such as `"c03"`. |
| `in` | required | Start time in the source clip. |
| `out` | required | End time in the source clip. Must be at least 0.2 s after `in`. |
| `fit` | top-level `fit` | Overrides `fit` for this segment only. |
| `focus_x` | `0.5` | With `crop`: which part of the frame to keep, horizontally. `0` keeps the left edge, `1` keeps the right edge. |
| `focus_y` | `0.5` | The same, vertically. `0` keeps the top edge, `1` keeps the bottom edge. |
| `volume` | `1.0` | Volume of this segment's own sound, from `0` to `4`. |
| `mute` | `false` | Drop this segment's sound. A muted segment also gets no captions. |
| `captions` | `true` | Set to `false` to hide captions on this segment only. |
| `note` | none | Why this shot is here. It appears in the validator's timeline and nowhere else. |

Each segment plays its own picture with its own sound. Version 1 can't lay one clip's picture over another clip's sound.

## Fit

What happens when a clip's shape doesn't match `aspect`, for example a landscape clip in a `9:16` video:

- `crop`: fills the frame and cuts off the sides (or the top and bottom). Use `focus_x` and `focus_y` to keep the subject in view. This is the best choice for most shots.
- `blur`: shows the whole shot, with a blurred, darkened copy of it filling the empty space behind. Use this when cropping would lose something that matters, such as a wide view, a group of people, or text on screen.
- `pad`: shows the whole shot with black bars around it.

When a clip already matches the aspect ratio, all three look the same.

## Text overlays

| Field | Default | Meaning |
|---|---|---|
| `text` | required | The words to show. Keep it to five words or fewer. |
| `start` | required | When the text appears, in seconds from the start of the video. |
| `end` | required | When it disappears. Keep text on screen for at least 1.5 s so it can be read. |
| `position` | `"top"` | `"top"`, `"middle"` or `"bottom"`. Avoid `"bottom"` when captions are on, because the two may overlap. |

## Music

| Field | Default | Meaning |
|---|---|---|
| `file` | required | Path to an audio file. A relative path is resolved from the folder that holds `edl.json`. |
| `volume` | `0.25` | Music volume, from `0` to `2`. |
| `start` | `0` | Where to start playing in the track, in seconds. Use it to skip a slow intro. |
| `duck` | `true` | Lower the music automatically whenever the clips have sound, such as speech. |

The music loops if it is shorter than the video, and it fades out over the last two seconds. After mixing, the whole video is brought to about -14 LUFS, the loudness commonly recommended for online video.

## Captions

Captions come from the word timings in `clip-index.json`. A word is included when its midpoint falls inside the segment. Words are grouped into short captions of even length: up to 20 characters for vertical video, 26 for square and 36 for landscape, and never more than 2.5 seconds long. A new caption always starts after a punctuation mark and after a pause of more than half a second.

Because captions are generated from the transcript, they can only be as accurate as the transcript is. If a transcribed word is wrong, you can't edit the caption itself in version 1. Tell the user, or hide captions on that segment with `"captions": false`.
