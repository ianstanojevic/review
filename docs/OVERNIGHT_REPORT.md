# Overnight report — branch `overnight-polish`

## ⚠ First: one thing I broke on your PC (2 minutes to fix)

A new test I wrote stubbed out `config.setting`. That's the function `data_dir()` uses to read `REVIEW_DATA_DIR`, so the test saved a **fake key, `RGAPI-mine`**, into your real `%LOCALAPPDATA%\REVIEW\api_key.txt`.

- A key saved in Settings beats the built-in one, so **your own Review will probably say "Riot rejected the API key"** until it's gone.
- The evidence says the file didn't exist before: the first test run found no saved key.
- I wasn't allowed to read or delete the file, so I left it.

**Fix it in either of these ways:**

- Open Review, then ⚙ Settings, then clear the RIOT API KEY field and save.
- Or delete `%LOCALAPPDATA%\REVIEW\api_key.txt` yourself, if it only contains `RGAPI-mine`.

**So it can't happen again:** the test is fixed, and every test now runs with LOCALAPPDATA, USERPROFILE and HOME pointed at a temp folder (`tests/conftest.py`).

The copy-to-clipboard test also replaced whatever was on your clipboard with a small test picture.

## Try it

- **Branch:** `overnight-polish`, pushed to GitHub. main, the v1.1 release, the README and the zip are untouched.
- **Desktop copy:** `dist\Review`, the folder your Desktop shortcut opens, is rebuilt from this branch. Just open Review.
- **Tests:** 154 pass (up from 140), including UI tests that open every tab with normal, empty and very thin results.

## What changed

### Your reported bugs
- **Two-line names ("Ryuk", then "#adc" on the next line).** The cause was a pasted line break: op.gg and similar sites copy a Riot ID as name, line break, #tag. Riot IDs are now cleaned wherever you type or paste them, and saved lists are cleaned at startup. This fixed the two-line chips, the wrapped radar legend and the half-hidden Head to head header.
- **Compare:**
  - shows Riot's own spelling of a name;
  - keeps a friend's "NA" tag after a restart;
  - stops capitalising player names in headers ("XxSlayer" stayed "XxSlayer");
  - puts Head to head beside the radar when there are 3 or fewer players (it used to leave a big empty space);
  - lets you click a friend's card to open their full analysis;
  - says "only N games — rough" when a player has too few games.
- **Cut-off text:** verdicts and summaries now wrap at the panel's real width.

### Charts — nothing overlaps any more (see the pictures in `docs/overnight/`)
- **Bar values** sit centred above their bars; they used to run into the next bar. The "50%" line label moves to an end of the line that's free.
- **Champion scatter:** names try four spots around the bubble and are skipped if all are taken. "Aphelios" and "Xayah" no longer print on top of each other.
- **Stats lab histograms:** the legend no longer sits on the axis title, and it has coloured keys.
- **Rank chart:** labels like "Emerald III" are no longer clipped; any chart widens its margin when a label would stick out.
- **Header notes** hide when they'd print over the chart title, in narrow windows or at 150% scaling.

### Correctness
- **Games with nothing to score** (e.g. Riot gave no role) now show "no radar" instead of a radar full of NaN. New tests open every tab with that and with only 3 games.
- **An empty rank benchmark** (not built for this region yet) now scores you against your own games instead of showing NaN everywhere.
- **Surrenders before 10:00** happen only when a teammate leaves. They're now left out like remakes: a 5-minute game gave 1.9 CS/min. Overview says "2 remakes / AFK games left out". **This needs your decision, see below.**

### Speed
- **Tables draw only the rows on screen**, inside their own scroll or the page's.
- **Cell text is measured from cached letter widths** instead of asking Tk about each cell (about 2 ms each, thousands per tab).
- Opening **Matches** and **Stats lab** the first time went from **5–6 s to about 1 s**.

### Robustness
- **A damaged cache file** used to stop the app from starting at all. It's now set aside (as `review.sqlite.damaged-<time>`), a fresh one is created, and the status bar says so.
- **If the built-in key stops working**, friends see "ask whoever sent you Review for an updated version" with an **Open settings** button. They used to get the 24-hour dev-key advice, which makes no sense to them.
- **Lost internet or Riot down:** the status shows "Can't reach Riot right now — retrying · N s" instead of a frozen counter.
- **A garbled reply from Riot** (for example a hotel Wi-Fi login page) is retried instead of crashing.

### Quality of life
- **Keyboard:**
  - Ctrl+1…0 jumps to a tab.
  - Ctrl+Tab or Ctrl+PageDown goes to the next tab; Ctrl+Shift+Tab or Ctrl+PageUp to the previous one.
  - F5 or Ctrl+R refreshes.
  - Ctrl+Shift+C copies the view.
  - Esc closes dialogs and backs out of an opened game.
- **↻ Refresh** fetches only new games; the status bar shows **"updated N min ago"**.
- **Copy view** puts the window on the clipboard as a picture, ready to paste into Discord.
- **The last tab you used** opens next time.
- **Hover any stat** in Trends, Wins vs losses, Radar, Compare or the Overview cards for a one-sentence explanation. Radar axes say what they're made of.

### Wording
- No more "90% CI", "Wilson", "Cohen's d", "percentile" or "rolling" anywhere. They're now "likely range" (shaded band or whiskers), "gap", "score" and "average of the last 20 games".
- Lane-difference averages read "even" instead of "+0".
- One spelling throughout: "Analyzing".

## Found but not done
- **Opening a tab the first time still takes about 0.6–1.5 s**, behind the "Preparing…" curtain. Building a tab is one block of Tk work. Spreading it out would mean rebuilding how every screen is built, the big rewrite you said not to do. Changing a filter freezes the window for 200–270 ms on a 400-game account.
- **Ctrl+Tab can't be tested automatically**, because Tk on Windows drops simulated Tab keys. It's wired up the same way as the working shortcuts, and Ctrl+PageDown and Ctrl+PageUp do the same job and are tested. Please try Ctrl+Tab yourself.
- **Remembering role, champion and range between runs: I chose not to.** They belong to one player: a saved "Mid" would show "no games" for a friend who never plays mid. The last tab, VS rank, queue and game count are remembered.
- **Region picker fallback in Compare:** not needed. Compare already asks Riot which server each player is on.
- **Lane tables** show "±" on every cell, which is dense. A cleaner layout needs a design decision.
- **Code clean-up** was limited to what I touched. pyflakes isn't installed, so I didn't sweep the whole codebase for unused imports.

## Needs your decision
1. **Delete the fake `api_key.txt`**, as described at the top.
2. **AFK surrenders under 10 minutes.** They're now excluded from all stats, win rate included, like remakes. Riot does take LP for them. If you'd rather count them in win rate only, say so.
3. **Merge `overnight-polish` into main**, and whether to cut a v1.2 release. I haven't touched the release.

## Before / after
`docs/overnight/` holds pairs, before on the left and after on the right:
- `compare-before-after.png`
- `trends-before-after.png`
- `champions-scatter-before-after.png`
- `winloss-before-after.png`
- `statslab-before-after.png`
- `overview-before-after.png`
