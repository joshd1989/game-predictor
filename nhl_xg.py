#!/usr/bin/env python3
"""NHL win model from MoneyPuck game logs.

The published coefficients are fit on the 2025-26 season (MoneyPuck season
2025). Each game's features use only earlier games: a recency-weighted
all-situations venue-adjusted xG differential, shrunk toward the previous
season, plus the starting goalie's GSAx per start, shrunk toward that
goalie's previous season (rookies shrink toward zero).

The out-of-sample record refits those coefficients on the first 60% of
2025-26 and grades the rest of that season, then grades 2026-27 to date
with the full 2025-26 fit. If this module cannot read MoneyPuck, the caller
keeps the older results model.
"""
import csv
import io
import math
import os
import re
import time
import urllib.request
import zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
MP_TO_ESPN = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
FULL = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CAR": "Carolina Hurricanes",
    "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LA": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NJ": "New Jersey Devils",
    "NSH": "Nashville Predators", "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJ": "San Jose Sharks",
    "STL": "St. Louis Blues", "TB": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}
SIGMA = 2.4
TRAIN_SEASON = 2025  # 2025-26
LIVE_SEASON = 2026   # 2026-27, the slate on the site
PRIOR_SEASON = {2025: 2024, 2026: 2025}


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def espn_abbr(code):
    return MP_TO_ESPN.get(code, code)


def gkey(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def fetch(url, dest, timeout=180):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 1000 and time.time() - os.path.getmtime(dest) < 18 * 3600:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    part = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    os.replace(part, dest)
    return dest


def load_team_games():
    path = fetch("https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv", os.path.join(CACHE, "mp_all_teams.csv"))
    rows = []
    want = {"2024", "2025", "2026"}
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("situation") != "all" or row.get("season") not in want:
                continue
            try:
                xgf = float(row["scoreVenueAdjustedxGoalsFor"])
                xga = float(row["scoreVenueAdjustedxGoalsAgainst"])
                gf = float(row["goalsFor"])
                ga = float(row["goalsAgainst"])
                season = int(row["season"])
                date = int(row["gameDate"])
            except (TypeError, ValueError, KeyError):
                continue
            rows.append((row["gameId"], season, date, row["name"], row.get("home_or_away") == "HOME", xgf - xga, gf, ga))
    by = defaultdict(list)
    for item in rows:
        by[item[0]].append(item)
    games = []
    for gid, sides in by.items():
        home = next((s for s in sides if s[4]), None)
        away = next((s for s in sides if not s[4]), None)
        if not home or not away:
            continue
        season, date = home[1], home[2]
        games.append({
            "id": gid, "season": season, "date": date,
            "home": espn_abbr(home[3]), "away": espn_abbr(away[3]),
            "hx": home[5], "ax": away[5], "hy": 1 if home[6] > away[6] else 0,
            "hgf": home[6], "hga": home[7], "agf": away[6], "aga": away[7],
        })
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def load_goalie_starts():
    starts = []
    for season in (2024, 2025, 2026):
        zpath = fetch(
            f"https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary/goalies/{season}.zip",
            os.path.join(CACHE, f"mp_goalies_{season}.zip"),
            timeout=90,
        )
        with zipfile.ZipFile(zpath) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            text = io.TextIOWrapper(zf.open(name), encoding="utf-8", errors="ignore")
            reader = csv.DictReader(text)
            by_team = defaultdict(list)
            for row in reader:
                if row.get("situation") != "all":
                    continue
                try:
                    ice = float(row["icetime"])
                    xg = float(row["xGoals"])
                    goals = float(row["goals"])
                    date = int(row["gameDate"])
                except (TypeError, ValueError, KeyError):
                    continue
                team = espn_abbr(row.get("playerTeam") or "")
                by_team[(row["gameId"], team)].append((ice, row["name"], team, date, xg - goals, int(row.get("season") or season)))
            for (gid, team), opts in by_team.items():
                ice, name, team, date, gsax, sea = max(opts, key=lambda x: x[0])
                if ice < 1500:
                    continue
                starts.append({"id": gid, "season": sea, "date": date, "name": name, "team": team, "gsax": gsax})
    starts.sort(key=lambda s: (s["date"], s["id"]))
    return starts


def _weights(n, half_life):
    # most recent game is index 0
    return [0.5 ** (i / half_life) for i in range(n)]


def shrunk_rate(values_newest_first, prior, half_life, k):
    if not values_newest_first:
        return prior, 0
    w = _weights(len(values_newest_first), half_life)
    sw = sum(w)
    avg = sum(wi * v for wi, v in zip(w, values_newest_first)) / sw
    return (sw * avg + k * prior) / (sw + k), len(values_newest_first)


def build_indexes(games, starts):
    team_x = defaultdict(lambda: defaultdict(list))  # season -> team -> [(date, xgd)] chronological
    for g in games:
        team_x[g["season"]][g["home"]].append((g["date"], g["hx"]))
        team_x[g["season"]][g["away"]].append((g["date"], g["ax"]))
    for sea in team_x:
        for team in team_x[sea]:
            team_x[sea][team].sort()
    prior_x = {}
    for sea, teams in team_x.items():
        prior_x[sea] = {team: (sum(v for _, v in rows) / len(rows) if rows else 0.0) for team, rows in teams.items()}
    goal = defaultdict(list)  # (season, name) -> [(date, gsax)] chronological
    starts_by = defaultdict(list)  # (season, team) -> [(date, name)]
    for s in starts:
        goal[(s["season"], s["name"])].append((s["date"], s["gsax"]))
        starts_by[(s["season"], s["team"])].append((s["date"], s["name"]))
    for key in goal:
        goal[key].sort()
    for key in starts_by:
        starts_by[key].sort()
    prior_g = {}
    for (sea, name), rows in goal.items():
        prior_g[(sea, name)] = sum(v for _, v in rows) / len(rows)
    return team_x, prior_x, goal, prior_g, starts_by


def _before(rows, date):
    # rows chronological (date, value). Return values newest first, strictly before date.
    i = 0
    n = len(rows)
    while i < n and rows[i][0] < date:
        i += 1
    return [rows[j][1] for j in range(i - 1, -1, -1)]


def team_rating(team_x, prior_x, season, team, date, half_life, k):
    prior_season = PRIOR_SEASON.get(season)
    prior = prior_x.get(prior_season, {}).get(team, 0.0)
    hist = _before(team_x.get(season, {}).get(team, []), date)
    return shrunk_rate(hist, prior, half_life, k)


def goalie_rating(goal, prior_g, season, name, date, half_life, k):
    prior_season = PRIOR_SEASON.get(season)
    prior = prior_g.get((prior_season, name), 0.0)
    hist = _before(goal.get((season, name), []), date)
    return shrunk_rate(hist, prior, half_life, k)


def feature_rows(games, indexes, season, half_life, k_team, k_goalie):
    team_x, prior_x, goal, prior_g, starts_by = indexes
    # starter on the night is known for completed games (the goalie who played)
    starter = {}
    for s_season, team_rows in ((season, None),):
        pass
    by_game_goalie = defaultdict(dict)
    # rebuild from starts list via indexes' goal dates is awkward; pass starts separately
    return _feature_rows(games, indexes, season, half_life, k_team, k_goalie)


def _feature_rows(games, starts, indexes, season, half_life, k_team, k_goalie):
    team_x, prior_x, goal, prior_g, starts_by = indexes
    played = defaultdict(dict)  # game id -> team -> (name)
    for s in starts:
        if s["season"] != season:
            continue
        played[s["id"]][s["team"]] = s["name"]
    out = []
    for g in games:
        if g["season"] != season:
            continue
        hx, _ = team_rating(team_x, prior_x, season, g["home"], g["date"], half_life, k_team)
        ax, _ = team_rating(team_x, prior_x, season, g["away"], g["date"], half_life, k_team)
        hn = played[g["id"]].get(g["home"])
        an = played[g["id"]].get(g["away"])
        hg = goalie_rating(goal, prior_g, season, hn, g["date"], half_life, k_goalie)[0] if hn else 0.0
        ag = goalie_rating(goal, prior_g, season, an, g["date"], half_life, k_goalie)[0] if an else 0.0
        out.append({**g, "x": hx - ax, "gs": hg - ag})
    return out


def fit_coefs(rows):
    best = None
    for b_x in (0.4, 0.8, 1.2, 1.6, 2.2, 3.0):
        for b_g in (0.4, 0.8, 1.2, 1.8, 2.6):
            for hfa in (0.05, 0.15, 0.25, 0.35):
                ll = 0.0
                for row in rows:
                    p = phi((hfa + b_x * row["x"] + b_g * row["gs"]) / SIGMA)
                    p = min(max(p, 1e-4), 1 - 1e-4)
                    ll += math.log(p if row["hy"] else 1 - p)
                if best is None or ll > best[0]:
                    best = (ll, b_x, b_g, hfa)
    return {"ll": best[0], "bXg": best[1], "bGoalie": best[2], "hfa": best[3], "n": len(rows)}


def choose_and_fit(games, starts):
    indexes = build_indexes(games, starts)
    season_games = [g for g in games if g["season"] == TRAIN_SEASON]
    if len(season_games) < 400:
        raise RuntimeError(f"only {len(season_games)} 2025-26 games, not enough to fit")
    cut = season_games[int(len(season_games) * 0.6)]["date"]
    best = None
    cache = {}
    for half in (15, 25, 40):
        for kt in (15, 30):
            for kg in (15, 30):
                rows = _feature_rows(games, starts, indexes, TRAIN_SEASON, half, kt, kg)
                train = [r for r in rows if r["date"] < cut]
                coef = fit_coefs(train)
                key = (coef["ll"], half, kt, kg)
                cache[(half, kt, kg)] = rows
                if best is None or key > (best[0]["ll"], best[1], best[2], best[3]):
                    best = (coef, half, kt, kg)
    coef, half, kt, kg = best
    train_rows = [r for r in cache[(half, kt, kg)] if r["date"] < cut]
    full_rows = _feature_rows(games, starts, indexes, TRAIN_SEASON, half, kt, kg)
    live_coef = fit_coefs(full_rows)
    live_coef.update({"halfLife": half, "kTeam": kt, "kGoalie": kg, "season": "2025-26", "holdoutDate": cut})
    # OOS probabilities
    oos = []
    frozen = coef
    frozen.update({"halfLife": half, "kTeam": kt, "kGoalie": kg})
    for row in full_rows:
        if row["date"] < cut:
            continue
        p = phi((frozen["hfa"] + frozen["bXg"] * row["x"] + frozen["bGoalie"] * row["gs"]) / SIGMA)
        oos.append(_oos_row(row, p, "2025-26 holdout"))
    live_rows = _feature_rows(games, starts, indexes, LIVE_SEASON, half, kt, kg)
    for row in live_rows:
        p = phi((live_coef["hfa"] + live_coef["bXg"] * row["x"] + live_coef["bGoalie"] * row["gs"]) / SIGMA)
        oos.append(_oos_row(row, p, "2026-27"))
    return {
        "ok": True,
        "indexes": indexes,
        "games": games,
        "starts": starts,
        "coef": live_coef,
        "oosCoef": frozen,
        "oos": oos,
        "halfLife": half,
        "kTeam": kt,
        "kGoalie": kg,
    }


def _oos_row(row, p, sample):
    return {
        "date": row["date"], "sample": sample, "y": row["hy"], "p": p,
        "home": row["home"], "away": row["away"],
        "homeName": FULL.get(row["home"], row["home"]),
        "awayName": FULL.get(row["away"], row["away"]),
        "iso": f"{str(row['date'])[:4]}-{str(row['date'])[4:6]}-{str(row['date'])[6:]}T18:00:00Z",
    }


def live_ratings(pack, asof_date):
    """Current shrunk ratings and goalie table for the slate season."""
    indexes = pack["indexes"]
    half, kt, kg = pack["halfLife"], pack["kTeam"], pack["kGoalie"]
    team_x, prior_x, goal, prior_g, starts_by = indexes
    ratings = {}
    for team in FULL:
        val, n = team_rating(team_x, prior_x, LIVE_SEASON, team, asof_date, half, kt)
        ratings[team] = {"xgd": val, "gp": n, "prior": prior_x.get(PRIOR_SEASON[LIVE_SEASON], {}).get(team, 0.0)}
    goalies = {}
    most = {}
    counts = defaultdict(lambda: defaultdict(int))
    for (season, team), rows in starts_by.items():
        if season != LIVE_SEASON:
            continue
        for date, name in rows:
            if date < asof_date:
                counts[team][name] += 1
    for team, cmap in counts.items():
        if cmap:
            most[team] = max(cmap.items(), key=lambda kv: (kv[1], kv[0]))[0]
    names = {name for (sea, name) in goal if sea in (LIVE_SEASON, PRIOR_SEASON[LIVE_SEASON])}
    for name in names:
        val, n = goalie_rating(goal, prior_g, LIVE_SEASON, name, asof_date, half, kg)
        raw_rows = _before(goal.get((LIVE_SEASON, name), []), asof_date)
        raw = sum(raw_rows) / len(raw_rows) if raw_rows else None
        goalies[gkey(name)] = {
            "name": name, "shrunk": val, "gp": n, "raw": raw,
            "prior": prior_g.get((PRIOR_SEASON[LIVE_SEASON], name), 0.0),
        }
    return ratings, goalies, most


def as_results_games(games):
    """ESPN-shaped completed games so the old results model can be scored on the same set."""
    out = []
    for g in games:
        if g["season"] not in (TRAIN_SEASON, LIVE_SEASON):
            continue
        iso = f"{str(g['date'])[:4]}-{str(g['date'])[4:6]}-{str(g['date'])[6:]}T18:00:00Z"
        out.append({
            "id": g["id"], "date": iso, "completed": True, "neutral": False,
            "home": {"name": FULL.get(g["home"], g["home"]), "abbr": g["home"], "score": g["hgf"]},
            "away": {"name": FULL.get(g["away"], g["away"]), "abbr": g["away"], "score": g["agf"]},
        })
    return out


def build():
    games = load_team_games()
    starts = load_goalie_starts()
    if len([g for g in games if g["season"] == TRAIN_SEASON]) < 400:
        raise RuntimeError("MoneyPuck 2025-26 game log is too small")
    return choose_and_fit(games, starts)
