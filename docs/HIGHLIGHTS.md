# HIGHLIGHTS

Fourth app in the DRAFT / REVIEW / REPLAY family: finds your best moments
in a game and records them as video clips with League's own replay
recorder.

* **Before the game:** DRAFT
* **After the game:** REPLAY (watch, tag, write) · HIGHLIGHTS (clips)
* **Every week:** REVIEW

## Run

```bash
python -m highlights            # the app
python -m highlights --demo     # throwaway data folder, two test games, simulated recording
python -m pytest tests/test_highlights.py
python -m PyInstaller --noconfirm packaging/highlights.spec   # dist/Highlights/Highlights.exe
python packaging/build.py --app highlights                    # release/Highlights-v1.0-windows.zip
```

`Desktop\Highlights.lnk` → `dist\Highlights\Highlights.exe`.

Uses Review's API key and cache, like REPLAY ("Fetch new games" runs
Review's `SyncJob`). Clips go to `Videos\Highlights\<date>_<champ>_vs_<opp>\
<min>m_<type>.webm` (Settings: folder, 720p/1080p/1440p, 30/60 fps).

## How it works

| Piece | What | Where |
|---|---|---|
| Moments | `replay.events.build_game` (kills/assists now carry victim, assists, shutdown, gold vs victim; objectives/towers carry who took part) | `replay/events.py` |
| Scoring + clips | pure logic, no I/O | `highlights/score.py` |
| Open the replay | LCU: metadata → create → download → watch, then wait for the Replay API | `highlights/recorder.py · open_replay` |
| Record | Replay API `/replay/recording` | `highlights/recorder.py · Recorder` |
| Window | Record / Clips views; REPLAY's account dialog, fetch and UI queue reused as-is | `highlights/app.py` |

### Scoring (`score.py`)

Per thing you did ("beat"): kill 10 · solo +8 · on your lane opponent +5
(solo: +5 more) · shutdown +4 and +2 per 100 g (max +14) · behind the
victim in gold +5 (≥ 1000 g) / +10 (≥ 2500 g) · multikill on top: double
+15, triple +35, quadra +60, penta +100 (10 s between kills, 30 s quadra →
penta) · assist 3 · objectives you last-hit or assisted: Baron 18, Elder 22,
Atakhan 14, Dragon 8, Herald 8, Grubs 5 (+4 for the last hit), tower 4,
inhibitor 6 · 3+ takedowns in one clip = teamfight +8.

Beats ≤ 15 s apart become one clip (teamfight + Baron), max 75 s. A clip
starts 8 s before the first beat and ends 4 s after the last. The best
clips (≤ 5, score ≥ 15 and ≥ 30 % of the best) are ticked from the start.
Estimated time = clip lengths + 12 s per clip (+ 45 s if the replay isn't
open yet): recording is real time.

### Recording flow (`recorder.py`)

Per clip: seek to 7 s before the clip (playing, speed 1) →
`follow_after_seek` (the camera pans to you in those seconds) → POST
`/replay/recording` with `startTime`/`endTime` of the clip, codec `webm`,
fps, size and `path` → poll GET until `recording` is false (or stop it
6 s past `endTime`) → wait until the file stops growing; if it isn't at
`path`, take the newest video the game wrote since the clip started
(reported `path`, `Videos\League of Legends`,
`Documents\League of Legends\Highlights`) and move it into place. **No
seeks or camera calls while a clip records** (tests check this). Cancel
stops the recorder; the replay is paused when all clips are done.

Riot keeps replays for the current patch only: games on another patch are
marked "old patch" in the list and Record is disabled with the reason.

## Verified on Windows

* ✅ Scoring, merging and the recording flow against a fake Replay API +
  LCU (`tests/test_highlights.py`, 18 tests; full suite 134 green).
* ✅ Look: same banner, tab row, panels and status line as REPLAY at 200 %
  (`--demo --snapshot`, PrintWindow).
* ✅ Build: `dist/Highlights/Highlights.exe --demo --snapshot out.png` runs.
* ⏳ **`/replay/recording` live — not checked yet** (the client wasn't
  running on 2026-11-22). Built from Riot's documented `Recording` schema.
  To check with the client up and a replay open:
  1. GET `/swagger/v3/openapi.json` → `Recording` fields (recording,
     startTime, endTime, currentTime, codec, framesPerSecond, width,
     height, lossless, enforceFrameRate, replaySpeed, path).
  2. Is `path` a file or a folder? Where does the file land if it's left
     out? Does `recording` flip to false by itself at `endTime`?
  3. Record a 5 s test clip and play it.
  4. Fix `Recorder.record` / `_collect` if any of that differs.
