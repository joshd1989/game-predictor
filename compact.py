#!/usr/bin/env python3
"""Turn data/data.json into site/data.json, the compact file the site loads."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ST = {"Out": "O", "Injured Reserve": "IR", "Suspension": "S", "Doubtful": "D", "Questionable": "Q", "Day-To-Day": "DTD", "Physically Unable to Perform": "PUP"}
BK = {"DraftKings": "DK", "FanDuel": "FD", "BetMGM": "MGM", "BetRivers": "BR", "bet365": "365", "Caesars": "CZR", "Unibet": "UNI", "BetRivers NY": "BRNY", "Fanatics": "FAN", "DraftKings (ESPN)": "DK", "Consensus": "CONS"}
CK = {"NHL": ["season", "form", "split", "xg", "corsi", "h2h"], "NBA": ["season", "form", "split", "srs", "net", "h2h"], "NFL": ["season", "form", "split", "epa", "h2h"]}
TK = {"NHL": ["xgf", "xga", "xgp", "cf", "pdo", "pp", "pk"], "NBA": ["ortg", "drtg", "nrtg", "pace", "srs"], "NFL": ["oepa", "depa", "osr", "dsr"]}


def r(x, n=2):
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return None
    v = round(float(x), n)
    return int(v) if v == int(v) else v


def num(t, k, n=2):
    return r(t.get(k), n)


def main():
    src = os.path.join(HERE, "data", "data.json")
    d = json.load(open(src, encoding="utf-8"))
    sources = d.get("sources") or [{
        "id": "bundle",
        "label": "Saved data file",
        "state": "stale",
        "at": d.get("asOf"),
        "detail": "This file has no per-source refresh log.",
    }]
    out = {"asOf": d.get("asOf"), "sources": sources, "L": {}}
    for L, v in (d.get("leagues") or {}).items():
        if L not in CK:
            continue
        teams = {}
        for a, t in (v.get("teams") or {}).items():
            if not isinstance(t, dict):
                continue
            inj = []
            for p in t.get("inj") or []:
                if not isinstance(p, dict):
                    continue
                st = ST.get(p.get("st"))
                imp = p.get("imp")
                if not st or not isinstance(imp, (int, float)):
                    continue
                if imp > 0 or st in ("O", "IR", "S", "D"):
                    inj.append([p.get("n"), st, r(imp)])
            inj = inj[:3]
            news = []
            for n in (t.get("news") or [])[:2]:
                if not isinstance(n, dict) or not n.get("tx"):
                    continue
                tx = n["tx"] if len(n["tx"]) <= 130 else n["tx"][:127].rsplit(" ", 1)[0] + "..."
                news.append([tx, n.get("src")])
            extra = [r(t.get(k), 3 if L == "NFL" and k in ("oepa", "depa") else 1) for k in TK[L]]
            row = [t.get("n"), t.get("rec"), t.get("hr"), t.get("rr"), t.get("gp"), num(t, "md"), num(t, "sos"), num(t, "form"), t.get("formRec"), num(t, "hm"), num(t, "rm"), num(t, "pf"), num(t, "pa"), t.get("injN") if isinstance(t.get("injN"), int) else None, num(t, "injImp"), inj, news, extra]
            if L == "NFL":
                qb = t.get("qb") if isinstance(t.get("qb"), dict) else None
                row.append([qb.get("n"), r(qb.get("epa"), 3)] if qb and qb.get("n") else None)
            teams[a] = row
        games = []
        for g in v.get("games") or []:
            if not isinstance(g, dict):
                continue
            o = g.get("odds") if isinstance(g.get("odds"), dict) else None
            oc = None
            if o and isinstance(o.get("pH"), (int, float)) and isinstance(o.get("bestH"), dict) and isinstance(o.get("bestA"), dict):
                books = []
                for b in o.get("books") or []:
                    if isinstance(b, (list, tuple)) and len(b) >= 3:
                        books.append([BK.get(b[0], b[0]), b[1], b[2]])
                oc = [r(o.get("pH"), 3), o["bestH"].get("ml"), BK.get(o["bestH"].get("book"), o["bestH"].get("book")), o["bestA"].get("ml"), BK.get(o["bestA"].get("book"), o["bestA"].get("book")), o.get("total") if isinstance(o.get("total"), (int, float)) else None, o.get("spreadH") if isinstance(o.get("spreadH"), (int, float)) else None, books]
            def gl(G):
                if not isinstance(G, dict) or not G.get("n"):
                    return None
                return [G.get("n"), r(G.get("gsax"), 2) if isinstance(G.get("gsax"), (int, float)) else None, G.get("st")]
            C = g.get("C") if isinstance(g.get("C"), dict) else {}
            adj = g.get("adj") if isinstance(g.get("adj"), dict) else {}
            games.append([g.get("t"), g.get("h"), g.get("a"), g.get("hRest"), g.get("aRest"), [r(C.get(k)) for k in CK[L]], [r(adj.get("rest")), r(adj.get("inj")), r(adj.get("goalie"))], r((g.get("proj") or {}).get("total"), 1), oc, gl(g.get("hG")), gl(g.get("aG")), g.get("h2h") if isinstance(g.get("h2h"), list) else []] + ([1] if g.get("neutral") else []))
        bt = v.get("backtest") if isinstance(v.get("backtest"), dict) else {}
        market = bt.get("market") if isinstance(bt.get("market"), dict) else None
        bets = bt.get("bets") if isinstance(bt.get("bets"), dict) else None
        entry = dict(
            hfa=r(v.get("hfa")),
            hfaN=v.get("hfaN"),
            sigma=v.get("sigma"),
            shrink=v.get("shrink") if isinstance(v.get("shrink"), (int, float)) else None,
            played=v.get("played"),
            teams=teams,
            games=games,
            bt=dict(
                n=bt.get("n") if isinstance(bt.get("n"), int) else 0,
                hit=r(bt.get("hit"), 3),
                brier=r(bt.get("brier"), 3),
                calib=[[c.get("lo"), c.get("n"), r(c.get("pred"), 3), r(c.get("actual"), 3)] for c in bt.get("calib") or [] if isinstance(c, dict)],
                mkt=[market.get("n"), r(market.get("marketHit"), 3), r(market.get("marketBrier"), 3)] if market and isinstance(market.get("marketHit"), (int, float)) and isinstance(market.get("marketBrier"), (int, float)) else None,
                bets=[bets.get("n"), bets.get("wins"), bets.get("units")] if bets and isinstance(bets.get("units"), (int, float)) else None,
            ),
        )
        if v.get("keptFrom"):
            entry["keptFrom"] = v["keptFrom"]
        if "pbpWeeks" in v:
            entry["pbpWeeks"] = v["pbpWeeks"]
        entry["model"] = v.get("model") or "results"
        gate = v.get("valueGate") if isinstance(v.get("valueGate"), dict) else None
        if gate:
            entry["valueGate"] = {"eligible": bool(gate.get("eligible")), "n": gate.get("n") or 0, "wins": gate.get("wins"), "units": r(gate.get("units"), 2), "minBets": gate.get("minBets") or 250}
        entry["marketBetter"] = bool(v.get("marketBetter"))
        if v.get("sample"):
            entry["sample"] = v.get("sample")
        if v.get("coef"):
            entry["coef"] = v.get("coef")

        def brief(bt0):
            if not isinstance(bt0, dict) or not bt0.get("n"):
                return None
            m = bt0.get("market") if isinstance(bt0.get("market"), dict) else {}
            b0 = bt0.get("bets") if isinstance(bt0.get("bets"), dict) else {}
            return [bt0.get("n"), r(bt0.get("hit"), 3), r(bt0.get("brier"), 3), r(m.get("marketHit"), 3) if m else None, r(m.get("marketBrier"), 3) if m else None, b0.get("n"), b0.get("wins"), r(b0.get("units"), 2) if isinstance(b0.get("units"), (int, float)) else None]

        if isinstance(bt.get("before"), dict):
            entry["bt"]["before"] = brief(bt.get("before"))
        entry["bt"]["after"] = brief(bt)
        out["L"][L] = entry
    dest = os.path.join(HERE, "site", "data.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    text = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")
    print(len(text))


if __name__ == "__main__":
    main()
