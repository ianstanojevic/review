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

## Verified on Windows (2026-11-21, client 16.23.840.6595)

Built in a Linux sandbox from Riot's Replay API docs and League Director's
source, then checked on Ian's PC with the client and a real replay open:

1. **LCU replay endpoints** — ✅ all four paths exist in the client's `/help`
   (`GetLolReplaysV1MetadataByGameId`, `PostLolReplaysV1RoflsByGameIdDownloadGraceful`,
   `PostLolReplaysV1RoflsByGameIdWatch`, `PostLolReplaysV2MetadataByGameIdCreate`);
   download/watch take `LolReplaysReplayContextData` = `{"componentType"}`.
   Fixed after checking:
   * metadata answers **HTTP 404** for a game the client hasn't loaded
     (not an empty state) → `metadata()` returns `{}` so the app calls
     `create` instead of showing "Client: … HTTP 404";
   * `create` body is exactly `gameVersion, gameType, queueId, gameEnd`
     (no `gameId`) — tested live: state goes 404 → `download`;
   * the failed-download state is `retryDownload` (not `retry`); `found` and
     `maxLimitReached` added;
   * `downloadProgress` is junk outside `downloading` (e.g. 2349731080) →
     dropped there, clamped to 0–100 while downloading.
2. **Clock offset** — ✅ none needed. Replay `ChampionKill` times
   (`/liveclientdata/eventdata`) vs the Riot timeline's `CHAMPION_KILL`:
   0.01–0.77 s apart over 17 kills (median 0.09 s). A jump to a death at
   1203.1 s asked for 1188.1 and the replay sat at 1188.6 half a second
   later. Default **Clock offset** stays 0. The Replay API answers 404 for
   ~20 s while the game loads; the app treats that as "no replay open".
3. **Camera lock** — ✅ the OpenAPI spec (`/swagger/v3/openapi.json`) has
   `Render.selectionName`: "Sets the selection to the given name, case
   insensitive". The game accepts the champion name in either form
   (`MissFortune` from Riot's API or `Miss Fortune`), reads back the display
   form, and rejects Riot IDs (reads back `""`). `follow()` now tries the
   champion name first and compares without spaces/punctuation; before, a
   champion like Miss Fortune only locked via a later fallback.
4. **Look** — ✅ at 200 %: banner, tab row, panels and status line are
   pixel-identical to REVIEW; tab row and first panel sit on the same pixel
   rows as DRAFT. `--snapshot` captures with PrintWindow off-screen on
   Windows, like REVIEW's.
5. **Build** — ✅ `packaging/replay.spec` → `dist/Replay/Replay.exe` builds
   and runs (`Replay.exe --demo --snapshot out.png`).

Note: Riot API PUUIDs are encrypted per API key, so the client's
`/lol-summoner/v1/current-summoner` PUUID never matches the cache's. Match
players by Riot ID if you ever need to join the two.

## Next

* DRAFT: show `notes.matchup_history(None, opponent)` in champ select.
* REVIEW: a "Why you die" card from `notes.category_counts(puuid, "death")`.
* OBS clips of tagged deaths (obs-websocket), so they survive patches.
