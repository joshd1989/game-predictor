#!/usr/bin/env python3
"""Print the site's daily parlays from site/data.json using default weights.

Small: top 2-3 legs by model win probability (3 only if the combined model
probability stays at or above 35%). Needs at least 3 priced games that day.
Big: 4-6 legs, as many as keep the combined model probability at or above 15%.
Needs at least 5 priced games.
Wild: a long shot aimed at +1000 or longer (decimal odds 11, $110 back on $10
including stake). Among 5-8 leg tickets that clear that payout, pick the set
with the highest model expected value. Positive edge (model probability above
the no-vig book probability) wins ties. One leg per game, the side the model
favours, at the best posted price. The printed probability is the model's
combined number.

The probability curve matches site/model.js, including the Eastern Time offset.
"""
import datetime as dt
import json
import math
import os
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/Toronto")
W = {"NHL": [1, 0.3, 0.2, 1, 0.4, 0.1], "NBA": [0.5, 0.3, 0.2, 1, 1, 0.1], "NFL": [0.6, 0.3, 0.15, 1, 0.1]}
SPLIT = 2
BOOK = {"DK": "DraftKings", "FD": "FanDuel", "MGM": "BetMGM", "BR": "BetRivers", "365": "bet365", "CZR": "Caesars", "BRNY": "BetRivers NY", "UNI": "Unibet", "FAN": "Fanatics", "CONS": "Consensus line"}
WILD_MIN_LEGS, WILD_MAX_LEGS, WILD_MIN_DECIMAL, WILD_POOL = 5, 8, 11.0, 18


def erf(x):
    s = -1 if x < 0 else 1
    a = abs(x)
    t = 1 / (1 + 0.3275911 * a)
    y = 1 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * math.exp(-a * a))
    return s * y


def phi(x):
    return 0.5 * (1 + erf(x / math.sqrt(2)))


def dec(ml):
    if not isinstance(ml, (int, float)) or ml == 0:
        return 1
    return 1 + ml / 100 if ml > 0 else 1 + 100 / -ml


def to_et(iso):
    utc = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=dt.timezone.utc)
    return utc.astimezone(ET)


def legs(data):
    out = []
    for L, v in data["L"].items():
        weights = [0, 0, 0, 1, 0, 0] if L == "NHL" and v.get("model") == "xg" else W[L]
        xg_model = L == "NHL" and v.get("model") == "xg"
        for g in v["games"]:
            o = g[8] if len(g) > 8 else None
            if not o:
                continue
            neutral = len(g) > 12 and g[12] == 1
            num = den = 0
            comps = g[5] if isinstance(g[5], list) else []
            for i, w in enumerate(weights):
                c = comps[i] if i < len(comps) else None
                if c is None or (neutral and i == SPLIT) or w <= 0:
                    continue
                num += w * c
                den += w
            shrink = 1 if xg_model else (v.get("shrink") or 0)
            hfa = 0 if neutral else (v.get("hfa") or 0)
            adj = g[6] if isinstance(g[6], list) else [0, 0, 0]
            rest = 0 if xg_model else (adj[0] or 0)
            inj = 0 if xg_model else (adj[1] or 0)
            margin = (shrink * num / den if den else 0) + hfa + rest + inj + ((adj[2] or 0) if L == "NHL" else 0)
            p_home = phi(margin / v["sigma"]) if v.get("sigma") else 0.5
            side_home = p_home >= 0.5
            p = p_home if side_home else 1 - p_home
            q = o[0] if side_home else 1 - o[0]
            when = to_et(g[0])
            out.append(dict(
                L=L, day=when.date().isoformat(), iso=g[0],
                t=when.strftime("%a %b %d %I:%M %p ET").replace(" 0", " "),
                game=f"{g[2]} @ {g[1]}", pick=g[1] if side_home else g[2],
                p=p, ml=o[1] if side_home else o[3],
                book=BOOK.get(o[2] if side_home else o[4], o[2] if side_home else o[4]),
                edge=p - q,
            ))
    return out


def prod(xs):
    p = 1.0
    for x in xs:
        p *= x
    return p


def bundle(combo):
    ordered = sorted(combo, key=lambda x: x["pick"])
    p = prod(x["p"] for x in ordered)
    d = prod(dec(x["ml"]) for x in ordered)
    edges = sum(1 for x in ordered if isinstance(x.get("edge"), (int, float)) and x["edge"] > 0)
    picks = "|".join(x["pick"] for x in ordered)
    # Same half-up rounding as JavaScript Math.round, so a tie breaks the same way.
    def q6(x):
        return math.floor(x * 1e6 + 0.5) / 1e6
    return {"legs": ordered, "p": p, "d": d, "ev": p * d - 1, "edges": edges, "key": (q6(p * d - 1), edges, q6(d), picks)}


def trim_pool(legs, cap=WILD_POOL):
    if len(legs) <= cap:
        return list(legs)

    def score(x):
        return x["p"] * dec(x["ml"])

    value = [x for x in legs if isinstance(x.get("edge"), (int, float)) and x["edge"] > 0]
    rest = [x for x in legs if x not in value]
    value.sort(key=lambda x: (-score(x), x["pick"]))
    rest.sort(key=lambda x: (-score(x), x["pick"]))
    return (value + rest)[:cap]


def combinations(pool, k):
    idx = list(range(k))
    n = len(pool)
    while True:
        yield [pool[i] for i in idx]
        i = k - 1
        while i >= 0 and idx[i] == n - k + i:
            i -= 1
        if i < 0:
            return
        idx[i] += 1
        for j in range(i + 1, k):
            idx[j] = idx[j - 1] + 1


def pick_wild(day_legs):
    if len(day_legs) < WILD_MIN_LEGS:
        return None
    pool = trim_pool(day_legs)
    best = None
    max_k = min(WILD_MAX_LEGS, len(pool))
    for k in range(WILD_MIN_LEGS, max_k + 1):
        for combo in combinations(pool, k):
            b = bundle(combo)
            if b["d"] < WILD_MIN_DECIMAL:
                continue
            if best is None or b["key"] > best["key"]:
                best = b
    if not best:
        return None
    return sorted(best["legs"], key=lambda x: (x["iso"], x["pick"]))


def build(day_legs):
    s = sorted(day_legs, key=lambda x: -x["p"])
    res = {}
    if len(s) >= 3:
        res["small"] = s[:3] if prod(x["p"] for x in s[:3]) >= 0.35 else s[:2]
    if len(s) >= 5:
        n = min(6, len(s))
        while n > 4 and prod(x["p"] for x in s[:n]) < 0.15:
            n -= 1
        res["big"] = s[:n]
        wild = pick_wild(s)
        if wild:
            res["wild"] = wild
    return res


def money_line(ml):
    return f"{ml:+d}" if isinstance(ml, int) or (isinstance(ml, float) and ml == int(ml)) else f"{ml:+g}"


def main():
    data = json.load(open(os.path.join(HERE, "site", "data.json"), encoding="utf-8"))
    by = {}
    for x in legs(data):
        by.setdefault(x["day"], []).append(x)
    for day in sorted(by):
        r = build(by[day])
        print(f"== {day}: {len(by[day])} priced games", "" if r else "(too few for a parlay)")
        for k, ls in r.items():
            p = prod(x["p"] for x in ls)
            d = prod(dec(x["ml"]) for x in ls)
            tag = " long shot" if k == "wild" else ""
            print(f"  {k}{tag}: {len(ls)} legs, model {p:.1%}, pays ${10 * d:.2f} on $10 (dec {d:.2f}), book-implied {1 / d:.1%}, model EV {p * d - 1:+.1%}")
            for x in ls:
                print(f"     {x['L']} {x['game']} -> {x['pick']} {money_line(x['ml'])} ({x['book']}), model {x['p']:.1%}, edge {x['edge'] * 100:+.1f} pts")


if __name__ == "__main__":
    main()
