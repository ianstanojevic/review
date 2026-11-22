<div align="center">

# R E V I E W

**See how you really play ranked — and how far you are from the next rank.**

**[⬇ Download for Windows](../../releases/latest)** · free · no install

<img src="docs/github/01-overview.png" width="640" alt="Review turns your ranked games into a skill radar, strengths and weaknesses">

</div>

Type a Riot ID and Review reads your last ranked games straight from Riot. It scores eight skills against players in your role, finds what you do better and worse than them, and gives one concrete thing to work on. Pick **Gold, Plat, Emerald, Diamond or Master+** and every chart shows where that rank sits — or add your friends and see who's better at what. Every number comes from real games and says how sure it is. *(Click any picture to enlarge.)*

<table>
<tr>
<td width="50%" valign="top"><b>Performance radar</b><br>Eight skills, each scored against players in your role. 50 is the average of the rank you compare with.<br><br><img src="docs/github/02-radar.png" alt="Performance radar"></td>
<td width="50%" valign="top"><b>Click any skill</b><br>What it is made of, how it changed, your best and worst games — and what to work on.<br><br><img src="docs/github/03-drill-down.png" alt="Skill drill-down"></td>
</tr>
<tr>
<td width="50%" valign="top"><b>Compare with your friends</b> <sub>NEW</sub><br>Add up to five Riot IDs: radars on top of each other and who's first, all on one rank's scale.<br><br><img src="docs/github/11-compare.png" alt="Compare players"></td>
<td width="50%" valign="top"><b>Head to head</b> <sub>NEW</sub><br>Every skill and stat side by side. Teal is the best in the group, red the lowest.<br><br><img src="docs/github/12-head-to-head.png" alt="Head to head"></td>
</tr>
<tr>
<td width="50%" valign="top"><b>Compare with any rank</b><br>A dashed line shows where the rank you picked is. Verdicts only appear when a change is real.<br><br><img src="docs/github/04-trends-vs-rank.png" alt="Trends against Master+"></td>
<td width="50%" valign="top"><b>Your champions</b><br>Win rate, KDA, CS and gold vs your opponent for every champion. ◆ marks your best picks.<br><br><img src="docs/github/05-champions.png" alt="Champion pool"></td>
</tr>
<tr>
<td width="50%" valign="top"><b>What decides your games</b><br>The stats that differ most between your wins and your losses.<br><br><img src="docs/github/06-wins-vs-losses.png" alt="Wins vs losses"></td>
<td width="50%" valign="top"><b>Where you die</b><br>Every death on the map, by game phase, and what it cost your team.<br><br><img src="docs/github/07-deaths.png" alt="Death map"></td>
</tr>
<tr>
<td width="50%" valign="top"><b>Lane phase</b><br>Gold, CS and XP against your direct opponent, minute by minute, in wins and losses.<br><br><img src="docs/github/08-lane.png" alt="Lane phase"></td>
<td width="50%" valign="top"><b>Tilt</b><br>Your win rate after one, two and three losses in a row.<br><br><img src="docs/github/09-tilt.png" alt="Win rate after losses"></td>
</tr>
<tr>
<td colspan="2" valign="top"><b>Every game</b><br>Open any match: gold curve against your laner, your deaths on the map and that game's radar.<br><br><img src="docs/github/10-single-game.png" alt="Single game"></td>
</tr>
</table>

## Install

1. Download `Review-v1.1-windows.zip` from the [latest release](../../releases/latest) and unzip it.
2. Run `Review.exe`. If Windows says *"protected your PC"*, click **More info → Run anyway** (the file isn't code-signed).
3. Type a Riot ID (`Name#TAG`), pick the region and press **Analyze**. The first time takes a few minutes while your games download — after that it's instant.

## How sure is it?

Every score is a percentile against real players in your role, game by game, so a support is never compared with an ADC. Small samples are pulled towards 50, so six games on a champion can't fake a 98, and every score carries a 90 % range. A strength or weakness only appears when it is clear in at least 20 games — no horoscopes.

## Good to know

All data comes from Riot's official API. The first time you pick a rank to compare with, Review downloads a sample of real games at that rank (10–15 minutes, once). Riot allows 100 requests every two minutes, so a first download is quicker when friends don't all start at the same time. Review runs on your computer and sends nothing to anyone but Riot.

<sub>Review isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.</sub>
