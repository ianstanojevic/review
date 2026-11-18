# REVIEW

A League of Legends ranked-account analyzer, companion to **DRAFT**. Enter a
Riot ID and get an honest read on your ranked play: strengths, weaknesses,
champion/role performance and whether you're improving.

> Status: milestones 1–2 (data layer + analysis engine, CLI). The desktop UI
> in DRAFT's style is next.

## Setup

Requires Python 3.11+ (developed on 3.14, Windows). The data layer uses only
the standard library; the analysis engine needs numpy.

1. Get a key at <https://developer.riotgames.com>.
2. `copy .env.example .env` and set `REVIEW_RIOT_API_KEY=RGAPI-…`.
   (If it isn't set, REVIEW falls back to DRAFT's `DRAFT_RIOT_API_KEY`.)
   `.env` is gitignored. Never commit a key.

```bash
python -m pip install numpy pytest
```

## Run

Fetch a player's last 150 Solo/Duo games and print raw stats:

```bash
python -m review fetch "flintgnuggaren#ian" --region EUW --count 150
```

Options: `--queue solo|flex|both`, `--count N`, `--offline` (cache only,
no API calls), `--workers N`. Press Ctrl+C to cancel; everything fetched so
far is kept. Show where the cache is and how big it is:

```bash
python -m review cache
```

Analyze the cached games (no API calls):

```bash
python -m review analyze "flintgnuggaren#ian" --region EUW
```

Filters: `--role bot`, `--champion Aphelios`, `--last 50`, `--queue flex`.

Record anonymised test fixtures from your cache (all IDs are replaced):

```bash
python scripts/record_fixtures.py "flintgnuggaren#ian" EUW
```

Run the tests:

```bash
python -m pytest
```

## How data is fetched

* **Routing:** account-v1 and match-v5 go to the regional host (`europe`,
  `americas`, `asia`). league-v4 goes to the platform host (`euw1`, `eun1`,
  `na1`, `kr`, …). Supported: EUW, EUNE, NA, KR, plus TR, RU, ME, BR, LAN,
  LAS, JP, OCE, SG, TW, VN.
* **Rate limits:** a sliding-window limiter per host enforces every window
  at once (default `20:1,100:120`, the personal/dev key limit). It honours
  `Retry-After` on 429s, backs off on 5xx and network errors, and adopts the
  limits Riot reports in `X-App-Rate-Limit`. To pin limits yourself, set
  `REVIEW_RATE_LIMITS`.
* **Cache:** SQLite at `%LOCALAPPDATA%\REVIEW\review.sqlite` (override with
  `REVIEW_DATA_DIR`). It stores raw match and timeline JSON (zlib) keyed by
  match ID, so no match is ever downloaded twice, and matches are shared
  between players. A re-run only fetches games that aren't cached yet. Each
  sync also stores a rank/LP snapshot, because the Riot API keeps no LP
  history.
* **Cost:** each game needs 2 requests (match + timeline). On a personal key
  the first 150 games take about 6 minutes.

## How the analysis works

`review/analysis/` is a pure module: in go cached matches and a PUUID, out
comes a structured result. It does no I/O.

* **Peer baseline:** every other player in the same role in *your* games,
  from both teams. Riot's matchmaker matched them to your MMR, so no external
  benchmark numbers are needed. For zero-sum stats (lane diffs vs your
  opponent, team share of epic monsters), your own direct opponents are
  excluded and the pool is mirrored, giving an exact 0 / 50 % baseline.
  Otherwise your lead would count twice.
* **Uncertainty everywhere:** win rates use 90 % Wilson intervals, means
  show ± 90 % CIs, and every number carries its n.
* **Radar:** 8 axes. Each sub-metric of each game becomes a percentile
  against same-role peers. Axis = weighted mean
  (`review/analysis/weights.toml`, with role overrides), shrunk toward 50
  for small samples, with a bootstrap 90 % CI (1000 resamples, seeded,
  cached).
* **Trends** are role-adjusted (you minus your role's peer average), so a
  change in role mix can't fake a trend. A trend is called improving or
  declining only when the 95 % CI of the slope excludes 0.
* **Findings** need ≥ 20 games, |z| ≥ 1.96 and an effect of d ≥ 0.15.
  Each one comes with a concrete tip.
* **Wins vs losses** ranks Cohen's d and marks which stats are measured
  *before* the game is decided ("early"), since those are better evidence
  of cause.
* Remakes (< 5 min or early surrender), other queues and non-Summoner's
  Rift games are excluded. Games without a timeline still count for
  match-level stats.

## Benchmarks: one scale for every account

By default "50" means *a typical same-role player in this account's own
games*. That is fair and Elo-matched, but it differs per account: 54 on a
Silver account and 50 on a Diamond account don't mean the same thing.

The **VS** tab row (My games · Diamond · Master+) switches the comparison
group to a **benchmark**: real recent ranked Solo/Duo games of a random
sample of Diamond I–IV, or Master/Grandmaster/Challenger, players in the
same region. All ten players in each benchmark game feed the role pools
(300 games ≈ 600 players per role). The scoring method is unchanged —
per-game percentiles vs same-role players, shrinkage, bootstrap CIs — only
the pool is shared, so a score now means the same on every account and
Compare puts two players on one scale.

* Built on demand in the background (≈ 12 min for 300 games on a personal
  key; the rate limiter is shared with other fetches). Games are cached.
* Settings → Benchmarks shows each benchmark's size and fetches more games.
* Lane diffs (gold/CS/XP vs your opponent) stay relative to your own
  opponents; against a benchmark they rank how big your leads are compared
  with leads between players of that level.
* Benchmark players play a different game (e.g. more vision), so compare
  the axes, not just the overall score.

## Public release

A personal/development key is for you and a few friends only. Hosting
REVIEW for other people requires a Riot **production key** (apply through
the developer portal). With a production key, REVIEW picks up the higher
limits automatically.

## Legal

REVIEW isn't endorsed by Riot Games and doesn't reflect the views or
opinions of Riot Games or anyone officially involved in producing or
managing Riot Games properties. Riot Games, and all associated properties
are trademarks or registered trademarks of Riot Games, Inc.

## Windows executable

```
python -m PyInstaller --noconfirm packaging/review.spec
```

builds `dist/Review/Review.exe` (a folder build: it starts as fast as
`python -m review.ui`; the old one-file build unpacked ~32 MB to %TEMP% on
every launch and started ~5 s slower). Make a shortcut to it anywhere.
The API key is **not** bundled: the exe reads `REVIEW_RIOT_API_KEY` /
`DRAFT_RIOT_API_KEY` from the environment, or a `.env` next to the exe or in
the project folder.

## Visual check against DRAFT (M4)

`m4/` holds side-by-side captures of DRAFT and REVIEW at the same DPI.
They are made with `scripts/capture_draft.py` (DRAFT's own window only, via
PrintWindow, never the screen) and `python -m review.ui --snapshot`;
`scripts/state_shots.py` captures the search, loading and error states.
