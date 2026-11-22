# HIGHLIGHTS

Fourth app in the DRAFT / REVIEW / REPLAY family: a small editor for your
replays. You watch the replay in League; Highlights shows the whole game as
a timeline (kills, highlight blocks, objectives) with a playhead that
follows the replay. Click a block to jump there, drag its edges to trim it,
and save just that part as a video with League's own replay recorder.

```
GAME      Sona vs Pantheon · Victory 8/6/32       ⟲10 ❚❚ 10⟳ ◎ Follow me 0.5× 1× 2×
TIMELINE  0      5:00     10:00     15:00 …
  KILLS        ○  ✕ ●   ●●     ●    ✕
  HIGHL.        ▬▬   [██76██]    ▬▬   ███51██        <- drag the edges to trim
  OBJ.       ◆         ◆           ◆
CLIP      Double kill · IN −1 10:04 +1 · OUT −1 10:20 +1 · 15 s   ▷ Preview  ● Save clip
SAVED     ▶ 10m_double.webm   4.1 MB
```

Keys: Space play/pause · ←/→ 10 s · N/P next/previous clip · I/O set in/out
at the playhead · Enter preview · S save · F follow me.

Run League **borderless** (Video → Window mode) so the replay and this
window are on screen together; in full screen the game hides as soon as you
click Highlights (and can't be recorded then — Highlights brings it back to
the front while it records).

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
<min>m_<type>.webm` (Settings: folder). The video is as big as League's
window and League must be on screen while it records: Highlights brings the
replay to the front for each clip and itself back when done.

## How it works

| Piece | What | Where |
|---|---|---|
| Moments | `replay.events.build_game` (kills/assists now carry victim, assists, shutdown, gold vs victim; objectives/towers carry who took part) | `replay/events.py` |
| Scoring + clips | pure logic, no I/O | `highlights/score.py` |
| Open the replay | LCU: metadata → create → download → watch, then wait for the Replay API | `highlights/recorder.py · open_replay` |
| Record | Replay API `/replay/recording` | `highlights/recorder.py · Recorder` |
| Timeline | kills · highlight blocks (trim handles) · objectives, playhead; `Pen` drawing + canvas items for selection/playhead | `highlights/timeline.py` |
| League's window | find it, is it visible, bring it to the front | `highlights/gamewindow.py` |
| Window | Editor (remote, timeline, clip, saved clips) / Clips; game picker dialog; REPLAY's account dialog, fetch, UI queue and camera keep-on-me reused as-is | `highlights/app.py` |

### Scoring (`score.py`)

Per thing you did ("beat"): kill 10 · solo +8 · on your lane opponent +5
(solo: +5 more) · shutdown +4 and +2 per 100 g (max +14) · behind the
victim in gold +5 (≥ 1000 g) / +10 (≥ 2500 g) · multikill on top: double
+15, triple +35, quadra +60, penta +100 (10 s between kills, 30 s quadra →
penta) · assist 3 · objectives you last-hit or assisted: Baron 18, Elder 22,
Atakhan 14, Dragon 8, Herald 8, Grubs 5 (+4 for the last hit), tower 4,
inhibitor 6 · 3+ takedowns in one clip = teamfight +8.

Beats ≤ 15 s apart become one clip (teamfight + Baron), max 75 s. A clip
starts 8 s before the first beat and ends 4 s after the last; trim it on
the timeline. The editor opens on the best clip. Saving takes the clip's
length + ~12 s (seek, camera, encoder): recording is real time.

### Recording flow (`recorder.py`)

Per clip: make sure League's window is on screen (`gamewindow.py`) → seek
to 7 s before the clip (playing, speed 1) → `follow_after_seek` (the camera
pans to you in those seconds) → let it play up to the clip's start → POST
`/replay/recording` with `startTime: -1` (now), the clip's `endTime`,
codec `webm`, 60 fps, `enforceFrameRate: false` and `path` → the game
stops by itself at `endTime` → the file is at `path` (if not, the newest
video the game wrote is moved there). **No seeks or camera calls while a
clip records** (tests check this). If Windows won't let us bring League
up, the progress line asks you to click it and waits 60 s. Cancel stops
the recorder; the replay is paused when all clips are done.

Riot keeps replays for the current patch only: games on another patch are
marked "old patch" in the list and Record is disabled with the reason.

## Verified on Windows (2026-11-22, client 16.23.840.6595)

Checked with the client and a real replay open (game 8053666923, 1440×900
window, full screen):

1. **Opening a replay** — ✅ `open_replay` on a game the client hadn't
   loaded: metadata 404 → `create` → download → Watch → the Replay API
   answers after ~28 s.
2. **`Recording` schema** — ✅ `/swagger/v3/openapi.json` has exactly
   recording, startTime, endTime, currentTime, codec (`webm` | `png` |
   `pngAndDepth`), framesPerSecond, width, height, lossless,
   enforceFrameRate, replaySpeed, path ("file path or directory").
   GET before anything: `startTime`/`endTime` −1.
3. **The window must be on screen** — ❗ full-screen League in the
   background shrinks its window to 1×1: the POST is accepted, reads back
   width 16 × height 0, `recording` stays false and no file is written
   (also happens as soon as you click another window). Brought to the
   front (`SetForegroundWindow` + `AttachThreadInput`), it records at the
   window's size. `width`/`height` sent in the POST change nothing.
   → `gamewindow.bring_to_front` before each clip, and a clip fails with
   the reason if the recorder doesn't start.
4. **`startTime`** — ❗ any value ≥ 0 makes the game seek there itself
   (which drops the camera lock). `startTime: -1` = start now: no seek,
   the lock stays. → we seek + lock ourselves, then start with −1.
5. **Stops by itself** — ✅ at `endTime`, `recording` turns false and
   `endTime` goes back to −1. The file is written exactly at `path`.
6. **Speed** — ❗ `enforceFrameRate: true` gave clips that run too fast:
   every rendered frame is stamped 1/60 s apart, but this PC renders ~45
   fps while encoding at 1440×900 (10 s of game → 8.0 s of video at 60
   fps, 4.5 s at 30 fps; slowing the replay to 0.7× overshot to 11.5 s).
   `enforceFrameRate: false`: 10 s → 10.09 s of video, real time, ~45 fps.
   → `false`, 60 fps, speed 1; no fps/resolution settings in the app.
7. **End to end** — ✅ `Recorder` on the real replay: seek → camera →
   record → save, a 12 s clip took 22.7 s and gave a 12.12 s, 13.6 MB VP9
   WebM (Opus audio) with the camera centred on your champion.
   `Videos\Highlights\_verify\app_flow.webm` is that clip.

Also: the game's WebM has no seek index (Cues), so some players can't
jump around in a clip; it plays fine start to end (browser, VLC).

Tests: scoring, merging and the recording flow against a fake that
behaves like the points above (`tests/test_highlights.py`, 20 tests; full
suite 136 green). Look: same banner, tab row, panels and status line as
REPLAY at 200 % (`--demo --snapshot`, PrintWindow).
