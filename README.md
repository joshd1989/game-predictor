# Game Predictor

Public slate for Joshua's NHL, NBA, and NFL model.

**Site:** https://joshd1989.github.io/game-predictor/

The page shows win probabilities for the next seven days, value picks against the no-vig market, per-game detail, adjustable weights, daily parlays, and a walk-forward track record. The browser loads `site/data.json`. It does not bake the numbers into the page. A missing stat stays unavailable.

These are model estimates, not guarantees. The track record on the site is computed by the pipeline on each refresh. It shows the NHL results model and the xG-plus-goalie model on the same out-of-sample games, and it prints each league's Brier score next to the market. A lower Brier score is the more accurate set of probabilities. Value badges appear only for a league with at least 250 out-of-sample flat bets and a positive unit total. The NBA toggle can show those edges anyway.

## What a probability is

The NBA and NFL models build a home-minus-away margin from season results, recent form, home/road splits, rest, injuries, and a league efficiency stat (Basketball-Reference ratings or nflverse EPA). The NHL model, when MoneyPuck loads, uses a recency-weighted all-situations expected-goal rating shrunk toward last season, plus the starting goalie's GSAx per start shrunk toward last season. Coefficients are fit on 2025-26. Other NHL factors stay on the page at weight 0. If MoneyPuck fails, the NHL page falls back to the older results model. A normal curve turns the margin into a win probability.

The betting line is not an input to that probability. Lines are attached afterwards so the page can show where the model and the market disagree. Moving the weight sliders recomputes every probability, edge, stake, and parlay in the browser.

## Daily parlays

All three tickets take one side per game (the side the model favours) at the best posted moneyline, and only when a real line is up.

- **Small.** The 2 or 3 highest model probabilities. Three legs only if they still combine to at least 35%. Needs 3 priced games that day.
- **Big.** Four to six legs, as many as keep the combined model probability at 15% or more. Needs 5 priced games.
- **Wild.** A long shot aimed at +1000 or longer (decimal odds of 11, which pays $110 on a $10 stake including the stake). Among 5–8 leg tickets that clear that payout, the page keeps the one with the highest model expected value. If two tickets tie, the one with more edges over the no-vig price wins. On a very large slate the search keeps the 18 legs most likely to help (positive edge first, then model probability times the decimal price). The model probability printed on the ticket is the real combined chance, and it will be low. A day with no such ticket, or with fewer than 5 priced games, gets no Wild.

Legs are treated as independent. Parlay prices multiply the book's margin, so the house edge grows with every leg. Kelly stakes use the bankroll and fraction you set; a negative-EV ticket gets no stake.

`python3 parlays.py` prints the same three tickets at the default weights.

## Data sources

| Source | Used for |
| --- | --- |
| [ESPN public JSON](https://site.api.espn.com/apis/site/v2/sports) | Schedules, scores, injuries, and a DraftKings line when the other books have not posted |
| [Action Network v2 scoreboard](https://api.actionnetwork.com/web/v2/scoreboard/nhl) | Moneylines from up to eight books, plus the consensus close used in the backtest. A book whose no-vig price is far from the median is dropped |
| [MoneyPuck](https://moneypuck.com/data.htm) | NHL expected goals, Corsi, PDO, goalie GSAx |
| [NHL stats API](https://api.nhle.com/stats/rest/en/team/summary) | Power play, penalty kill, shots |
| [DailyFaceoff](https://www.dailyfaceoff.com/starting-goalies) | Projected starting goalies. Cloudflare often blocks GitHub runners; the site then keeps the last good projections and says so |
| [Basketball-Reference](https://www.basketball-reference.com/leagues/NBA_2027.html) | NBA offensive/defensive rating, pace, SRS, player BPM |
| [nflverse](https://github.com/nflverse/nflverse-data) | NFL play-by-play EPA and quarterback dropback EPA |

The pipeline fetches all of this itself. Do not commit `cache/`, a Basketball-Reference HTML dump, or the nflverse `.csv.gz`. Those files are large and go stale. `state/snapshots.json` keeps the last good *processed* tables so a blocked source does not blank the site.

`data/data.json` is the full model file. `site/data.json` is the compact file the page fetches. Both include `asOf` (Eastern) and a `sources` list. Each source is `fresh`, `partial`, `stale` (last good pull), or `unavailable`. The page prints that status under the clock. If a whole league cannot be rebuilt, that league stays on its last good slate and is labeled with the older time.

## Refresh the site

GitHub Actions (`.github/workflows/refresh.yml`) does three things:

1. Runs `gather.py` and `compact.py` on a schedule, and whenever you start the workflow by hand.
2. Commits `data/data.json`, `state/snapshots.json`, and `site/data.json` when they changed.
3. Deploys the `site/` folder to GitHub Pages with the official Pages actions.

The schedule is `0 13,14,20,21 * * *` UTC. That is the pair of hours that lands on **9:00 and 4:00 America/Toronto** in both standard time and daylight time. A small gate ignores the extra slot, and it ignores a run that is outside those local windows. **Run workflow** (`workflow_dispatch`) always refreshes.

To refresh by hand: open the **Actions** tab, choose **Refresh data and deploy site**, and click **Run workflow**.

### One setting the repo owner has to flip

GitHub will not serve the Pages site until the repository is pointed at Actions:

**Settings → Pages → Build and deployment → Source: GitHub Actions**

Nothing in this repo can flip that for you if the token cannot change repository settings. After it is set, a push to `main` or a manual run deploys https://joshd1989.github.io/game-predictor/.

## Run it locally

```bash
python3 -m pip install -r requirements.txt
python3 gather.py      # writes data/data.json and state/snapshots.json
python3 compact.py     # writes site/data.json
python3 parlays.py     # prints Small, Big, and Wild at the default weights
python3 -m http.server -d site 8000
```

Open http://127.0.0.1:8000/. Fetching `data.json` needs a local server; opening the HTML file directly will not load it.

```bash
node --test tests/model.test.mjs
```

checks the Eastern-time parser, the data guards, and the Wild picker.
