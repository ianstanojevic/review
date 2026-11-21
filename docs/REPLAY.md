# REPLAY

Third app in the DRAFT / REVIEW family: a remote for League's own replay
player plus a notebook for your deaths, missed chances and matchups.

* **Before the game:** DRAFT (later: shows your matchup notes in champ select)
* **After the game:** REPLAY — watch, jump, tag, write
* **Every week:** REVIEW — totals and trends (later: reads REPLAY's tags)

## Run

```bash
python -m replay            # the app
python -m replay --demo     # throwaway data folder with one test game
python -m pytest tests/test_replay.py
pyinstaller --noconfirm packaging/replay.spec   # dist/Replay/Replay.exe
```

Uses Review's API key (Settings key, `.env`, `REVIEW_RIOT_API_KEY` or
`DRAFT_RIOT_API_KEY`) and Review's cache, so any game Review has fetched is
already here. "Fetch new games" runs Review's own `SyncJob`.

## How it works

| Piece | What | Where |
|---|---|---|
| Matches + timelines | Riot API via Review's `SyncJob` | `%LOCALAPPDATA%\REVIEW\review.sqlite` (shared) |
| Your notes | deaths, missed chances, bookmarks, matchup notes | `%LOCALAPPDATA%\REVIEW\replay.sqlite` (own file: the cache can be wiped, notes can't) |
| Download / start a replay | League client API (LCU), password from `lockfile` | `replay/local.py · LeagueClient` |
| Seek / pause / speed / camera | Replay API on `https://127.0.0.1:2999` | `replay/local.py · ReplayAPI` |
| Replays on disk | `.rofl` header (version) + JSON metadata at the end | `replay/rofl.py` |

`EnableReplayApi=1` must be in `C:\Riot Games\League of Legends\Config\game.cfg`.
Replay switches it on the first time you press **Watch** (backup:
`game.cfg.bak-before-replayapi`). Already done on Ian's PC.

### Files

* `replay/app.py` — the window: Review / Insights / Matchups views
* `replay/widgets.py` — `TimelineBar`, `Chips`, `MiniMap`
* `replay/events.py` — a game from your side: lane opponent, supports,
  deaths with context (killer, assists, jungler, gold vs opponent, level,
  enemy objective within 60 s), kills, objectives, towers
* `replay/notes.py` — `replay.sqlite`: `marks` + `matchups`
* `replay/model.py` — fixed tag lists (fixed on purpose: they get counted)
* `replay/local.py` — LCU + Replay API clients, `game.cfg` switch
* `replay/rofl.py` — replays folder scan

Threads: all disk/network work runs in worker threads and comes back via
`App.post()`, a queue the UI thread drains every 30 ms (Tk's `after()` from
a worker raises before `mainloop` starts and the call is lost).

## Verified here vs. to verify on Windows

Built and tested in a Linux sandbox (112 tests green, UI rendered headless
under Xvfb). Endpoint names come from Riot's Replay API docs and Riot's
League Director source. Not testable there — **check these first in Claude
Code with the League client open:**

1. **LCU replay endpoints** — `GET /lol-replays/v1/metadata/{gameId}`,
   `POST /lol-replays/v1/rofls/{gameId}/download/graceful`,
   `POST /lol-replays/v1/rofls/{gameId}/watch` (body
   `{"componentType": "replay-button_match-history"}`),
   `POST /lol-replays/v2/metadata/{gameId}/create`. Compare with the
   client's own endpoint list (`GET /help` on the LCU) and fix paths/bodies
   in `LeagueClient` if they differ.
2. **Clock offset** — jump to a death and check that the replay lands
   ~15 s before it. If it is consistently off, set **Clock offset** in ⚙ (or
   compute it once from `/replay/playback` vs the timeline).
3. **Camera lock** — `ReplayAPI.follow()` uses `selectionName` when
   `/replay/render` exposes it, else only `cameraAttached=True` (then you
   click your champion once). Check the generated OpenAPI spec of the Replay
   API on Ian's client to see whether there is a proper field for picking
   the unit, and use it.
4. **Look** — ✅ checked on Windows at 200 % (2026-11-21): banner, tab row,
   panels and status line are pixel-identical to REVIEW; tab row and first
   panel sit on the same pixel rows as DRAFT. `--snapshot` captures with
   PrintWindow off-screen on Windows, like REVIEW's.
5. **Build** — ✅ `packaging/replay.spec` → `dist/Replay/Replay.exe` builds
   and runs (`Replay.exe --demo --snapshot out.png`).

## Next

* DRAFT: show `notes.matchup_history(None, opponent)` in champ select.
* REVIEW: a "Why you die" card from `notes.category_counts(puuid, "death")`.
* OBS clips of tagged deaths (obs-websocket), so they survive patches.
