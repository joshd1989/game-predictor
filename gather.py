#!/usr/bin/env python3
"""Build data/data.json for the game predictor.

Sources, all fetched on each run (nothing is read from a checked-in HTML or
play-by-play dump): ESPN public JSON, Action Network public odds, MoneyPuck,
the NHL stats API, DailyFaceoff, Basketball-Reference, and nflverse.

If a source fails or blocks the runner, the last good pull of that piece is
kept and the failure is recorded under "sources". Raw downloads stay in
cache/ and are not committed. Win probabilities are built from the stats,
never from the betting lines. Lines are attached only so the site can measure
disagreement.
"""
import csv
import datetime as dt
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import nhl_xg

import pandas as pd

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
DATA_PATH = os.path.join(HERE, "data", "data.json")
SNAP_PATH = os.path.join(HERE, "state", "snapshots.json")
ET = ZoneInfo("America/Toronto")
os.makedirs(CACHE, exist_ok=True)

TODAY = dt.datetime.now(ET).date()
DAYS = 7

GROUPS = [
    ("espn", "ESPN schedules, scores, and injuries", "Schedules, scores, and injury reports refreshed."),
    ("action", "Action Network odds", "Moneylines refreshed. Final games keep their closing line from the last pull."),
    ("moneypuck", "MoneyPuck expected goals, Corsi, and goalie GSAx", "Team and goalie tables refreshed."),
    ("nhl", "NHL official team stats", "Special teams and shots refreshed."),
    ("goalies", "DailyFaceoff projected goalies", "Starting goalies refreshed."),
    ("bref", "Basketball-Reference ratings and player BPM", "Team ratings and player BPM refreshed."),
    ("nflverse", "nflverse play-by-play EPA", "EPA and quarterback tables refreshed."),
]


def now_et():
    return dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M")


def clean(obj):
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (int, str)) or obj is None:
        return obj
    if hasattr(obj, "item"):
        try:
            return clean(obj.item())
        except Exception:
            return str(obj)
    return obj


def atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean(obj), f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


SNAPS = load_json(SNAP_PATH)
if not isinstance(SNAPS.get("sources"), dict):
    SNAPS = {"sources": {}}
PREV = load_json(DATA_PATH)
PARTS = defaultdict(list)


def flush_snaps():
    SNAPS["updated"] = now_et()
    atomic_write(SNAP_PATH, SNAPS)


def load_snap(key):
    rec = SNAPS["sources"].get(key)
    if not isinstance(rec, dict) or "data" not in rec:
        return None
    return rec


def save_snap(key, data, at=None):
    SNAPS["sources"][key] = {"at": at or now_et(), "data": clean(data)}
    flush_snaps()


def note(group, name, state, at, detail):
    PARTS[group].append({"name": name, "state": state, "at": at, "detail": detail})


def resolve(group, name, key, producer, is_ok=None):
    """Run producer(). On failure, return the last saved copy of this piece."""
    data = None
    try:
        data = producer()
    except Exception:
        traceback.print_exc()
        data = None
    if is_ok is None:
        good = data is not None and data != {} and data != []
    else:
        try:
            good = data is not None and bool(is_ok(data))
        except Exception:
            traceback.print_exc()
            good = False
    if good:
        save_snap(key, data)
        note(group, name, "fresh", now_et(), f"{name} refreshed.")
        return data
    old = load_snap(key)
    if old is not None:
        note(group, name, "stale", old.get("at"), f"{name} failed this run. Using the last good pull from {old.get('at') or 'an earlier run'}.")
        return old.get("data")
    note(group, name, "unavailable", None, f"{name} failed and there is no saved copy.")
    return None


def finalize_sources():
    by_id = {gid: (label, ok_detail) for gid, label, ok_detail in GROUPS}
    out = []
    for gid, label, ok_detail in GROUPS:
        parts = PARTS.get(gid) or []
        if not parts:
            out.append({"id": gid, "label": label, "state": "unavailable", "at": None, "detail": "This source was not requested."})
            continue
        states = [p["state"] for p in parts]
        ats = [p["at"] for p in parts if p.get("at")]
        if all(s == "fresh" for s in states):
            state, at, detail = "fresh", (max(ats) if ats else now_et()), ok_detail
        elif all(s == "unavailable" for s in states):
            state, at, detail = "unavailable", None, " ".join(p["detail"] for p in parts)
        elif all(s == "stale" for s in states):
            state, at = "stale", (min(ats) if ats else None)
            detail = " ".join(p["detail"] for p in parts)
        else:
            state, at = "partial", (min(ats) if ats else None)
            detail = " ".join(p["detail"] for p in parts)
        out.append({"id": gid, "label": by_id[gid][0], "state": state, "at": at, "detail": detail})
    return out


def valid_body(path, kind):
    try:
        if kind == "gzip":
            with open(path, "rb") as f:
                return f.read(2) == b"\x1f\x8b"
        with open(path, encoding="utf-8", errors="ignore") as f:
            txt = f.read(5000)
    except OSError:
        return False
    low = txt.lower()
    if any(s in low for s in ("access denied", "just a moment", "cf-browser-verification", "captcha", "/cdn-cgi/")):
        return False
    if kind == "json":
        t = txt.lstrip()
        return t.startswith("{") or t.startswith("[")
    if kind == "csv":
        return "<html" not in low and "," in txt
    if kind == "html":
        return ("<html" in low or "<table" in low) and "access denied" not in low
    return len(txt) > 0


def curl(url, dest, timeout, referer=None):
    cmd = [
        "curl", "-sL", "--compressed", "--max-time", str(timeout), "--connect-timeout", "15",
        "-A", UA,
        "-H", "Accept: text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
        "-o", dest, "-w", "%{http_code}", url,
    ]
    if referer:
        cmd[1:1] = ["-e", referer]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:
        print("CURL", exc, file=sys.stderr)
        return ""
    return (r.stdout or "").strip()


def get(url, name=None, binary=False, maxage=3 * 3600, kind="text", timeout=45, referer=None):
    path = os.path.join(CACHE, name or re.sub(r"[^A-Za-z0-9]+", "_", url)[-150:])
    if os.path.exists(path) and os.path.getsize(path) > 0 and time.time() - os.path.getmtime(path) < maxage and valid_body(path, kind):
        return path if binary else open(path, encoding="utf-8", errors="ignore").read()
    tmp = path + ".part"
    last = ""
    for i in range(3):
        last = curl(url, tmp, timeout, referer)
        if last == "200" and os.path.exists(tmp) and os.path.getsize(tmp) > 0 and valid_body(tmp, kind):
            os.replace(tmp, path)
            return path if binary else open(path, encoding="utf-8", errors="ignore").read()
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        time.sleep(1.2 * (i + 1))
    print("FAIL", last, url, file=sys.stderr)
    return None


def getj(url, **kwargs):
    kwargs.setdefault("kind", "json")
    text = get(url, **kwargs)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        print("BADJSON", url, file=sys.stderr)
        return None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def nick(name):
    last = norm((name or "").split()[-1])
    # Utah was the Hockey Club in 2025-26 and the Mammoth after. Both nick to utah.
    if "utah" in (name or "").lower():
        return "utah"
    return last


LEAGUES = {
    "NHL": dict(path="hockey/nhl", an="nhl", start=dt.date(2026, 9, 29), sigma=2.4, hfa_prior=0.15, prior_n=400, b2b=0.12, unit="goals"),
    "NBA": dict(path="basketball/nba", an="nba", start=dt.date(2026, 10, 15), sigma=12.5, hfa_prior=2.3, prior_n=300, b2b=1.6, unit="pts"),
    "NFL": dict(path="football/nfl", an="nfl", start=dt.date(2026, 9, 9), sigma=13.5, hfa_prior=1.6, prior_n=100, b2b=0.0, unit="pts"),
}
BOOKS = {68: "DraftKings", 69: "FanDuel", 75: "BetMGM", 71: "BetRivers", 79: "bet365", 123: "Caesars", 247: "Unibet", 972: "BetRivers NY", 76: "Fanatics"}
BOOKIDS = "15,30,68,69,75,71,79,123,247,972,76"


def ml_prob(ml):
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def ml_dec(ml):
    return 1 + ml / 100 if ml > 0 else 1 + 100 / (-ml)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def parse_event(e):
    c = e["competitions"][0]
    if e.get("season", {}).get("type") not in (2, None):
        return None
    t = {x["homeAway"]: x for x in c["competitors"]}
    if "home" not in t:
        return None
    st = e["status"]["type"]
    g = dict(id=e["id"], date=e["date"], name=e["name"], state=st["state"], completed=st.get("completed", False),
             neutral=c.get("neutralSite", False), venue=c.get("venue", {}).get("fullName"))
    for side in ("home", "away"):
        x = t[side]
        tm = x["team"]
        g[side] = dict(id=tm["id"], name=tm["displayName"], short=tm.get("name") or tm.get("shortDisplayName"), abbr=tm["abbreviation"],
                       score=float(x["score"]) if x.get("score") not in (None, "") else None,
                       records={r.get("type"): r.get("summary") for r in x.get("records", [])})
    g["espn_odds"] = None
    for o in c.get("odds", []) or []:
        try:
            ml = o.get("moneyline", {})
            g["espn_odds"] = dict(book=o["provider"]["name"], mlH=int(ml["home"]["close"]["odds"]), mlA=int(ml["away"]["close"]["odds"]),
                                  total=o.get("overUnder"), spread=o.get("spread"))
        except Exception:
            pass
        break
    return g


def season_events(L):
    cfg = LEAGUES[L]
    fresh = {}
    failures = 0
    attempts = 0
    if L == "NFL":
        week_fail = []
        week_ok = set()
        for wk in range(1, 20):
            attempts += 1
            d = getj(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=2026&seasontype=2&week={wk}", maxage=6 * 3600)
            if d is None:
                week_fail.append(wk)
                continue
            week_ok.add(wk)
            for e in d.get("events", []) or []:
                g = parse_event(e)
                if g:
                    g["week"] = wk
                    fresh[g["id"]] = g
        last = max(week_ok) if week_ok else 0
        failures = len([w for w in week_fail if w <= last or not week_ok])
    else:
        day = cfg["start"]
        end = TODAY + dt.timedelta(days=DAYS)
        while day <= end:
            attempts += 1
            ds = day.strftime("%Y%m%d")
            d = getj(
                f"https://site.api.espn.com/apis/site/v2/sports/{cfg['path']}/scoreboard?dates={ds}",
                maxage=(30 * 86400 if day < TODAY - dt.timedelta(days=2) else 3 * 3600),
            )
            if d is None:
                failures += 1
            else:
                for e in d.get("events", []) or []:
                    g = parse_event(e)
                    if g:
                        fresh[g["id"]] = g
            day += dt.timedelta(days=1)
    snap = load_snap(f"espn_{L}")
    snap_games = (snap or {}).get("data") or []
    snap_at = (snap or {}).get("at")
    if failures == 0 and fresh:
        games = sorted(fresh.values(), key=lambda g: g["date"])
        save_snap(f"espn_{L}", games)
        note("espn", f"{L} schedule", "fresh", now_et(), f"{L} schedule refreshed ({len(games)} games).")
        return games
    merged = {g["id"]: g for g in snap_games if isinstance(g, dict) and g.get("id")}
    merged.update(fresh)
    games = sorted(merged.values(), key=lambda g: g["date"])
    if not games:
        note("espn", f"{L} schedule", "unavailable", None, f"{L} schedule failed and there is no saved copy.")
        return []
    if fresh:
        save_snap(f"espn_{L}", games)
        note("espn", f"{L} schedule", "partial", snap_at or now_et(), f"{L} schedule missed {failures} of {attempts} requests. Gaps were filled from the last good schedule.")
        return games
    note("espn", f"{L} schedule", "stale", snap_at, f"{L} schedule failed. Using the last good schedule from {snap_at or 'an earlier run'}.")
    return games


def _v2_moneyline(rows):
    home = away = None
    for row in rows or []:
        if row.get("is_live") or row.get("period") not in (None, "event", "game"):
            continue
        if row.get("side") == "home":
            home = row
        elif row.get("side") == "away":
            away = row
    return home, away


def parse_v2_game(g):
    if not isinstance(g, dict):
        return None
    teams = []
    for t in g.get("teams") or []:
        if isinstance(t, dict) and t.get("full_name"):
            teams.append({"id": t.get("id"), "full_name": t.get("full_name")})
    odds = []
    for bid, blob in (g.get("markets") or {}).items():
        try:
            book_id = int(bid)
        except (TypeError, ValueError):
            continue
        event = (blob or {}).get("event") or {}
        home, away = _v2_moneyline(event.get("moneyline"))
        if not home or not away or home.get("odds") is None or away.get("odds") is None:
            continue
        total = next((x.get("value") for x in (event.get("total") or []) if x.get("side") == "over" and not x.get("is_live")), None)
        spread = next((x.get("value") for x in (event.get("spread") or []) if x.get("side") == "home" and not x.get("is_live")), None)
        odds.append({"book_id": book_id, "ml_home": home.get("odds"), "ml_away": away.get("odds"), "total": total, "spread_home": spread})
    if len(teams) < 2 or not odds:
        return None
    return {
        "home_team_id": g.get("home_team_id"), "away_team_id": g.get("away_team_id"),
        "start_time": g.get("start_time"), "teams": teams, "odds": odds,
    }


def an_games(L, day, maxage=3 * 3600, season=None):
    if isinstance(day, int):
        q = f"week={day}&season={season or 2026}"
    else:
        q = f"date={day.strftime('%Y%m%d')}"
    d = getj(f"https://api.actionnetwork.com/web/v2/scoreboard/{LEAGUES[L]['an']}?bookIds={BOOKIDS}&{q}", maxage=maxage)
    if d is None:
        return None
    return [g for g in (parse_v2_game(x) for x in (d.get("games") or [])) if g]


def slim_an(g):
    if not isinstance(g, dict):
        return None
    teams = []
    for t in g.get("teams") or []:
        if not isinstance(t, dict):
            continue
        teams.append({"id": t.get("id"), "full_name": t.get("full_name")})
    odds = []
    for b in g.get("odds") or []:
        if not isinstance(b, dict) or b.get("ml_home") is None or b.get("ml_away") is None:
            continue
        odds.append({"book_id": b.get("book_id"), "ml_home": b.get("ml_home"), "ml_away": b.get("ml_away"), "total": b.get("total"), "spread_home": b.get("spread_home")})
    if not teams:
        return None
    return {"home_team_id": g.get("home_team_id"), "away_team_id": g.get("away_team_id"), "start_time": g.get("start_time"), "teams": teams, "odds": odds}


def an_token(day, season=None):
    return f"week:{season or 2026}:{day}" if isinstance(day, int) else day.isoformat()


def index_an(games):
    idx = {}
    for g in games:
        tm = {t.get("id"): t for t in g.get("teams") or []}
        h, a = tm.get(g.get("home_team_id")), tm.get(g.get("away_team_id"))
        if not h or not a or not h.get("full_name") or not a.get("full_name") or not g.get("start_time"):
            continue
        idx[(nick(h["full_name"]), nick(a["full_name"]), g["start_time"][:10])] = g
    return idx


def load_an(L, days, historical=False, season=None):
    """Fetch Action Network v2 slates. Old dates reuse a saved copy; a failed day keeps its last copy."""
    key = f"an_{L}_{'close' if historical else 'upcoming'}"
    snap = load_snap(key) or {"data": {"tokens": {}}, "at": None}
    tokens = (snap.get("data") or {}).get("tokens") or {}
    if not isinstance(tokens, dict):
        tokens = {}
    failed_days = []
    fresh = 0
    changed = False
    collected = []
    pending = []
    for day in days:
        tok = an_token(day, season)
        old = tokens.get(tok) or []
        reuse = historical and not isinstance(day, int) and day < TODAY - dt.timedelta(days=2) and bool(old)
        if reuse:
            collected.extend(old)
            continue
        pending.append((day, tok, old))

    def pull(item):
        day, tok, old = item
        got = an_games(L, day, maxage=3 * 3600 if not historical else 12 * 3600, season=season)
        return tok, old, got

    pulled = []
    if pending:
        workers = 8 if len(pending) > 4 else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pulled = list(pool.map(pull, pending))
    for tok, old, got in pulled:
        if got is None:
            failed_days.append(tok)
            collected.extend(old)
            continue
        tokens[tok] = got
        changed = True
        fresh += 1
        collected.extend(got)
    if changed:
        save_snap(key, {"tokens": tokens})
        at = now_et()
    else:
        at = snap.get("at")
    label = f"{L} {'closing lines' if historical else 'upcoming odds'}"
    if fresh and not failed_days:
        note("action", label, "fresh", at, f"{label} refreshed ({fresh} slates).")
        state = "fresh"
    elif fresh and failed_days:
        note("action", label, "partial", at, f"{label} missed {len(failed_days)} slate(s); those days use the last good pull.")
        state = "partial"
    elif collected:
        note("action", label, "stale", at, f"{label} failed. Using the last good pull from {at or 'an earlier run'}.")
        state = "stale"
    else:
        note("action", label, "unavailable", None, f"{label} failed and there is no saved copy.")
        state = "unavailable"
    return index_an(collected), state, set(failed_days)


def match_an(idx, g):
    d0 = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
    for delta in (0, -1, 1):
        k = (nick(g["home"]["name"]), nick(g["away"]["name"]), (d0 + dt.timedelta(days=delta)).strftime("%Y-%m-%d"))
        if k in idx:
            return idx[k]
    return None


def drop_books_far_from_median(books):
    """v2 sometimes posts BetMGM with home and away swapped. Drop any book whose
    no-vig home price is far from the median of the other books."""
    if len(books) < 4:
        return books
    probs = []
    for b in books:
        ph, pa = ml_prob(b["mlH"]), ml_prob(b["mlA"])
        probs.append(ph / (ph + pa))
    med = sorted(probs)[len(probs) // 2]
    kept = [b for b, p in zip(books, probs) if abs(p - med) <= 0.12]
    return kept if len(kept) >= 2 else books


def odds_from_an(ag, espn):
    books = []
    cons = None
    for b in (ag or {}).get("odds", []) or []:
        if b.get("ml_home") is None or b.get("ml_away") is None:
            continue
        try:
            rec = dict(book=BOOKS.get(b["book_id"], f"book {b['book_id']}"), mlH=int(b["ml_home"]), mlA=int(b["ml_away"]), total=b.get("total"), spreadH=b.get("spread_home"))
        except (TypeError, ValueError):
            continue
        if b["book_id"] == 15:
            cons = rec
        elif b["book_id"] == 30:
            pass
        else:
            books.append(rec)
    if espn and not any(x["book"] == "DraftKings" for x in books):
        try:
            books.append(dict(book="DraftKings (ESPN)", mlH=int(espn["mlH"]), mlA=int(espn["mlA"]), total=espn.get("total"), spreadH=None))
        except (TypeError, ValueError, KeyError):
            pass
    if not books and cons:
        books = [dict(cons, book="Consensus")]
    books = drop_books_far_from_median(books)
    if not books:
        return None
    nv = []
    for b in books:
        ph, pa = ml_prob(b["mlH"]), ml_prob(b["mlA"])
        nv.append(ph / (ph + pa))
    nv.sort()
    m = nv[len(nv) // 2] if len(nv) % 2 else (nv[len(nv) // 2 - 1] + nv[len(nv) // 2]) / 2
    bh = max(books, key=lambda b: ml_dec(b["mlH"]))
    ba = max(books, key=lambda b: ml_dec(b["mlA"]))
    tots = [b["total"] for b in books if isinstance(b.get("total"), (int, float))]
    sps = [b["spreadH"] for b in books if isinstance(b.get("spreadH"), (int, float))]
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    return dict(books=books, pH=round(m, 4), bestH=dict(ml=bh["mlH"], book=bh["book"]), bestA=dict(ml=ba["mlA"], book=ba["book"]),
                total=med(tots), spreadH=med(sps), n=len(books))


def team_table(games, upto=None):
    T = defaultdict(lambda: dict(g=0, w=0, pf=0, pa=0, hg=0, hm=0, rg=0, rm=0, log=[], opps=[]))
    for g in games:
        if not g["completed"] or g["home"]["score"] is None:
            continue
        if upto and g["date"] >= upto:
            continue
        hs, as_ = g["home"]["score"], g["away"]["score"]
        for side, o, pf, pa in (("home", "away", hs, as_), ("away", "home", as_, hs)):
            k = g[side]["name"]
            t = T[k]
            t["g"] += 1
            t["w"] += pf > pa
            t["pf"] += pf
            t["pa"] += pa
            if side == "home":
                t["hg"] += 1
                t["hm"] += pf - pa
            else:
                t["rg"] += 1
                t["rm"] += pf - pa
            t["log"].append((g["date"], pf - pa, g[o]["name"], side))
            t["opps"].append(g[o]["name"])
    return T


def hfa_est(games, cfg, upto=None):
    s, n = 0, 0
    for g in games:
        if g["completed"] and g["home"]["score"] is not None and not g["neutral"] and (not upto or g["date"] < upto):
            s += g["home"]["score"] - g["away"]["score"]
            n += 1
    return (s + cfg["hfa_prior"] * cfg["prior_n"]) / (n + cfg["prior_n"]) if True else 0, n


def strength(T, k, form_n):
    t = T.get(k)
    if not t or t["g"] == 0:
        return None
    md = (t["pf"] - t["pa"]) / t["g"]
    opp = [(T[o]["pf"] - T[o]["pa"]) / T[o]["g"] for o in t["opps"] if T[o]["g"]]
    sos = sum(opp) / len(opp) if opp else 0
    last = t["log"][-form_n:]
    return dict(gp=t["g"], w=t["w"], md=md, adj=md + sos, sos=sos, form=sum(x[1] for x in last) / len(last), formN=len(last),
                formW=sum(1 for x in last if x[1] > 0), hm=t["hm"] / t["hg"] if t["hg"] else None, rm=t["rm"] / t["rg"] if t["rg"] else None,
                hg=t["hg"], rg=t["rg"], pf=t["pf"] / t["g"], pa=t["pa"] / t["g"], last=t["log"][-1][0] if t["log"] else None)


def rest_days(last_iso, game_iso):
    if not last_iso:
        return None
    a = dt.datetime.fromisoformat(last_iso.replace("Z", "+00:00")).astimezone(ET).date()
    b = dt.datetime.fromisoformat(game_iso.replace("Z", "+00:00")).astimezone(ET).date()
    return (b - a).days - 1


def rest_adj(L, rh, ra):
    cfg = LEAGUES[L]
    if rh is None or ra is None:
        return 0.0
    if L == "NFL":
        return max(-7, min(7, rh - ra)) * 0.15
    return (-cfg["b2b"] if rh == 0 else 0) - (-cfg["b2b"] if ra == 0 else 0)


SPLIT_K = {"NHL": 10, "NBA": 8, "NFL": 4}


def split_c(L, Sh, Sa, hfa):
    k = SPLIT_K[L]
    n = min(Sh["hg"], Sa["rg"])
    return (Sh["hm"] - Sa["rm"] - 2 * hfa) * n / (n + k)


FORM_N = {"NHL": 10, "NBA": 10, "NFL": 4}
RESULT_W = dict(season=1.0, form=0.35, split=0.25)


def results_margin(L, Sh, Sa, hfa, rh, ra, shrink):
    comps = dict(season=Sh["adj"] - Sa["adj"], form=Sh["form"] - Sa["form"])
    if Sh["hm"] is not None and Sa["rm"] is not None:
        comps["split"] = split_c(L, Sh, Sa, hfa)
    num = sum(RESULT_W[k] * v for k, v in comps.items())
    den = sum(RESULT_W[k] for k in comps)
    return shrink * num / den + hfa + rest_adj(L, rh, ra)


def backtest(L, games):
    cfg = LEAGUES[L]
    done = [g for g in games if g["completed"] and g["home"]["score"] is not None]
    mingp = 2 if L == "NFL" else 4
    rows = []
    for g in done:
        T = team_table(games, upto=g["date"])
        hfa, _ = hfa_est(games, cfg, upto=g["date"])
        Sh = strength(T, g["home"]["name"], FORM_N[L])
        Sa = strength(T, g["away"]["name"], FORM_N[L])
        if not Sh or not Sa or Sh["gp"] < mingp or Sa["gp"] < mingp:
            continue
        rh = rest_days(Sh["last"], g["date"])
        ra = rest_days(Sa["last"], g["date"])
        rows.append(dict(g=g, Sh=Sh, Sa=Sa, hfa=0 if g["neutral"] else hfa, rh=rh, ra=ra,
                         y=1 if g["home"]["score"] > g["away"]["score"] else 0))

    def evalm(shrink):
        ll = 0
        for r in rows:
            p = phi(results_margin(L, r["Sh"], r["Sa"], r["hfa"], r["rh"], r["ra"], shrink) / cfg["sigma"])
            p = min(max(p, 0.02), 0.98)
            ll -= math.log(p if r["y"] else 1 - p)
        return ll / len(rows) if rows else 9

    best = min([x / 20 for x in range(4, 21)], key=evalm) if rows else 0.6
    return rows, best


def bt_report(L, rows, shrink, cons_idx):
    cfg = LEAGUES[L]
    out = dict(n=len(rows), shrink=shrink)
    if not rows:
        return out
    hits = 0
    brier = 0
    ll = 0
    buckets = defaultdict(lambda: [0, 0, 0.0])
    mk = dict(n=0, mhit=0, mbrier=0, hit=0, brier=0)
    bets = dict(n=0, units=0.0, wins=0)
    for r in rows:
        p = phi(results_margin(L, r["Sh"], r["Sa"], r["hfa"], r["rh"], r["ra"], shrink) / cfg["sigma"])
        y = r["y"]
        hits += (p >= 0.5) == (y == 1)
        brier += (p - y) ** 2
        pc = min(max(p, .02), .98)
        ll -= math.log(pc if y else 1 - pc)
        fav = max(p, 1 - p)
        b = min(int((fav - 0.5) / 0.05), 4)
        bb = buckets[b]
        bb[0] += 1
        bb[1] += (y == 1) == (p >= 0.5)
        bb[2] += fav
        ag = match_an(cons_idx, r["g"])
        if ag:
            o = [x for x in ag.get("odds") or [] if x.get("book_id") == 15 and x.get("ml_home") is not None]
            if o:
                o = o[0]
                ph_, pa_ = ml_prob(o["ml_home"]), ml_prob(o["ml_away"])
                q = ph_ / (ph_ + pa_)
                mk["n"] += 1
                mk["mhit"] += (q >= 0.5) == (y == 1)
                mk["mbrier"] += (q - y) ** 2
                mk["hit"] += (p >= 0.5) == (y == 1)
                mk["brier"] += (p - y) ** 2
                for side, ps, ml, win in (("H", p, o["ml_home"], y == 1), ("A", 1 - p, o["ml_away"], y == 0)):
                    qs = q if side == "H" else 1 - q
                    if ps - qs > 0.03:
                        bets["n"] += 1
                        bets["wins"] += win
                        bets["units"] += (ml_dec(ml) - 1) if win else -1
    out.update(hit=hits / len(rows), brier=brier / len(rows), logloss=ll / len(rows),
               calib=[dict(lo=50 + 5 * b, hi=(55 + 5 * b) if b < 4 else 100, n=v[0], actual=v[1] / v[0], pred=v[2] / v[0]) for b, v in sorted(buckets.items())])
    if mk["n"]:
        out["market"] = dict(n=mk["n"], marketHit=mk["mhit"] / mk["n"], marketBrier=mk["mbrier"] / mk["n"], modelHit=mk["hit"] / mk["n"], modelBrier=mk["brier"] / mk["n"])
        out["bets"] = dict(n=bets["n"], wins=bets["wins"], units=round(bets["units"], 2), roi=bets["units"] / bets["n"] if bets["n"] else None)
    return out


def consensus_ml(ag):
    if not ag:
        return None
    for b in ag.get("odds") or []:
        if b.get("book_id") == 15 and b.get("ml_home") is not None and b.get("ml_away") is not None:
            return b
    return None


def grade_predictions(preds, idx):
    """Score a list of {iso, homeName, awayName, y, p} against consensus moneylines."""
    hits = brier = 0
    mk = dict(n=0, mhit=0, mbrier=0, hit=0, brier=0)
    bets = dict(n=0, units=0.0, wins=0)
    buckets = defaultdict(lambda: [0, 0, 0.0])
    used = 0
    for pr in preds:
        p, y = pr["p"], pr["y"]
        used += 1
        hits += (p >= 0.5) == (y == 1)
        brier += (p - y) ** 2
        fav = max(p, 1 - p)
        bkt = min(int((fav - 0.5) / 0.05), 4)
        bb = buckets[bkt]
        bb[0] += 1
        bb[1] += (y == 1) == (p >= 0.5)
        bb[2] += fav
        fake = {"date": pr["iso"], "home": {"name": pr["homeName"]}, "away": {"name": pr["awayName"]}}
        o = consensus_ml(match_an(idx, fake))
        if not o:
            continue
        ph_, pa_ = ml_prob(o["ml_home"]), ml_prob(o["ml_away"])
        q = ph_ / (ph_ + pa_)
        mk["n"] += 1
        mk["mhit"] += (q >= 0.5) == (y == 1)
        mk["mbrier"] += (q - y) ** 2
        mk["hit"] += (p >= 0.5) == (y == 1)
        mk["brier"] += (p - y) ** 2
        for side, ps, ml, win in (("H", p, o["ml_home"], y == 1), ("A", 1 - p, o["ml_away"], y == 0)):
            qs = q if side == "H" else 1 - q
            if ps - qs > 0.03:
                bets["n"] += 1
                bets["wins"] += win
                bets["units"] += (ml_dec(ml) - 1) if win else -1
    if not used:
        return {"n": 0}
    out = dict(n=used, hit=hits / used, brier=brier / used,
               calib=[dict(lo=50 + 5 * b, hi=(55 + 5 * b) if b < 4 else 100, n=v[0], actual=v[1] / v[0], pred=v[2] / v[0]) for b, v in sorted(buckets.items())])
    if mk["n"]:
        out["market"] = dict(n=mk["n"], marketHit=mk["mhit"] / mk["n"], marketBrier=mk["mbrier"] / mk["n"], modelHit=mk["hit"] / mk["n"], modelBrier=mk["brier"] / mk["n"])
        out["bets"] = dict(n=bets["n"], wins=bets["wins"], units=round(bets["units"], 2), roi=bets["units"] / bets["n"] if bets["n"] else None)
        out["marketBetter"] = out["market"]["marketBrier"] < out["market"]["modelBrier"]
    return out


def value_gate(bt):
    bets = (bt or {}).get("bets") or {}
    n = bets.get("n") or 0
    units = bets.get("units")
    eligible = isinstance(units, (int, float)) and n >= 250 and units > 0
    return {"eligible": bool(eligible), "n": n, "wins": bets.get("wins"), "units": units, "minBets": 250}


def espn_nfl_season(year):
    out = []
    for wk in range(1, 19):
        d = getj(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={year}&seasontype=2&week={wk}", maxage=7 * 86400)
        if not d:
            continue
        for e in d.get("events") or []:
            g = parse_event(e)
            if g:
                g["week"] = wk
                out.append(g)
    return out


SEV = {"Out": 1.0, "Injured Reserve": 1.0, "Suspension": 1.0, "Doubtful": 0.75, "Questionable": 0.35, "Day-To-Day": 0.3, "Physically Unable to Perform": 1.0}


def load_injuries(L):
    d = getj(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[L]['path']}/injuries")
    if not isinstance(d, dict) or "injuries" not in d:
        return None
    out = defaultdict(list)
    for t in d.get("injuries") or []:
        team = t.get("displayName")
        if not team:
            continue
        for i in t.get("injuries") or []:
            a = i.get("athlete") or {}
            st = i.get("status", "")
            if st not in SEV:
                continue
            link = next((l.get("href") for l in a.get("links") or [] if "playercard" in (l.get("rel") or [])), None)
            short, long_ = i.get("shortComment"), i.get("longComment")
            note = short if len(short or "") >= 30 else ((re.split(r"(?<=\.)\s", long_ or "")[0] or short))
            out[team].append(dict(name=a.get("displayName"), pos=(a.get("position") or {}).get("abbreviation"), status=st, note=note, date=i.get("date"), url=link))
    return {"teams": dict(out)}


def fnum(row, key):
    try:
        return float(row[key])
    except (TypeError, ValueError, KeyError):
        return None


def load_moneypuck():
    teams_txt = get("https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/teams.csv", kind="csv")
    goal_txt = get("https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/goalies.csv", kind="csv")
    skat_txt = get("https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/skaters.csv", kind="csv")
    if not teams_txt or not goal_txt or not skat_txt:
        return None
    alias = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
    teams = {}
    for r in csv.DictReader(io.StringIO(teams_txt)):
        if r.get("situation") != "all" or not r.get("team"):
            continue
        gp = fnum(r, "games_played")
        if not gp:
            continue
        teams[alias.get(r["team"], r["team"])] = r
    goalies = [r for r in csv.DictReader(io.StringIO(goal_txt)) if r.get("situation") == "all" and r.get("name")]
    skaters = []
    for r in csv.DictReader(io.StringIO(skat_txt)):
        if r.get("situation") != "all" or not r.get("name"):
            continue
        skaters.append({"name": r["name"], "games_played": r.get("games_played"), "I_F_points": r.get("I_F_points"), "I_F_xGoals": r.get("I_F_xGoals")})
    if len(teams) < 20 or len(goalies) < 10:
        return None
    return {"teams": teams, "goalies": goalies, "skaters": skaters}


def load_nhl_api():
    d = getj("https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId=20262027%20and%20gameTypeId=2")
    rows = (d or {}).get("data") if isinstance(d, dict) else None
    if not rows:
        return None
    out = {}
    for r in rows:
        name = r.get("teamFullName")
        if not name:
            continue
        out[name] = {
            "teamFullName": name,
            "powerPlayPct": r.get("powerPlayPct"),
            "penaltyKillPct": r.get("penaltyKillPct"),
            "shotsForPerGame": r.get("shotsForPerGame"),
            "shotsAgainstPerGame": r.get("shotsAgainstPerGame"),
        }
    return out if len(out) >= 20 else None


def load_dfo(days):
    found = {}
    ok_days = 0
    for day in days:
        html = get(f"https://www.dailyfaceoff.com/starting-goalies/{day.isoformat()}", kind="html", timeout=40)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
        if not m:
            continue
        try:
            blob = json.loads(m.group(1))["props"]["pageProps"].get("data") or []
        except Exception:
            continue
        ok_days += 1
        for gg in blob:
            if not isinstance(gg, dict):
                continue
            for side in ("home", "away"):
                team = gg.get(f"{side}TeamName")
                if not team:
                    continue
                found[f"{day.isoformat()}|{nick(team)}"] = dict(
                    name=gg.get(f"{side}GoalieName"),
                    status=gg.get(f"{side}NewsStrengthName") or "Projected",
                    sv=gg.get(f"{side}GoalieSavePercentage"),
                    src=gg.get(f"{side}NewsSourceUrl"),
                    page=f"https://www.dailyfaceoff.com/starting-goalies/{day.isoformat()}",
                )
    if ok_days == 0:
        return None
    found["_days"] = ok_days
    return found


def bref_table(url, tid):
    html = get(url, kind="html", timeout=50, referer="https://www.basketball-reference.com/")
    if not html:
        return None
    html = html.replace("<!--", "").replace("-->", "")
    m = re.search(r'(<table[^>]*id="%s".*?</table>)' % tid, html, re.S)
    if not m:
        return None
    df = pd.read_html(io.StringIO(m.group(1)))[0]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[1] for c in df.columns]
    return df


def load_bref_teams():
    adv = bref_table("https://www.basketball-reference.com/leagues/NBA_2027.html", "advanced-team")
    if adv is None:
        return None
    adv = adv.loc[:, ~adv.columns.duplicated()]
    out = {}
    for _, r in adv.iterrows():
        if not isinstance(r.get("Team"), str) or r["Team"] == "League Average":
            continue
        try:
            out[nick(r["Team"].replace("*", ""))] = dict(ortg=float(r["ORtg"]), drtg=float(r["DRtg"]), nrtg=float(r["NRtg"]), pace=float(r["Pace"]), srs=float(r["SRS"]), sos=float(r["SOS"]))
        except Exception:
            continue
    return out if len(out) >= 20 else None


def load_bref_players():
    time.sleep(3)
    padv = bref_table("https://www.basketball-reference.com/leagues/NBA_2027_advanced.html", "advanced")
    if padv is None:
        return None
    out = {}
    for _, r in padv.iterrows():
        try:
            out.setdefault(norm(r["Player"]), dict(g=float(r["G"]), mp=float(r["MP"]), bpm=float(r["BPM"]), team=None if pd.isna(r["Team"]) else str(r["Team"])))
        except Exception:
            continue
    return out if len(out) >= 50 else None


def load_nflverse():
    path = get("https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.csv.gz", name="pbp_2026.csv.gz", binary=True, maxage=12 * 3600, kind="gzip", timeout=120)
    if not path:
        return None
    pbp = pd.read_csv(path, usecols=["posteam", "defteam", "week", "qb_dropback", "passer_player_name", "passer_player_id", "epa", "success", "play_type", "season_type", "game_id"], low_memory=False)
    pbp = pbp[(pbp.season_type == "REG") & pbp.play_type.isin(["pass", "run"]) & pbp.epa.notna()]
    nflv = {"LA": "LAR", "WAS": "WSH"}
    pbp["posteam"] = pbp.posteam.replace(nflv)
    pbp["defteam"] = pbp.defteam.replace(nflv)
    gp_t = pbp.groupby("posteam").game_id.nunique()
    off = pbp.groupby("posteam").agg(oepa=("epa", "mean"), osr=("success", "mean"), oplays=("epa", "size"))
    de = pbp.groupby("defteam").agg(depa=("epa", "mean"), dsr=("success", "mean"), dplays=("epa", "size"))
    eff = {}
    for t in off.index:
        if t not in de.index or t not in gp_t.index:
            continue
        g_ = gp_t[t]
        eff[str(t)] = dict(oepa=float(off.loc[t, "oepa"]), depa=float(de.loc[t, "depa"]), osr=float(off.loc[t, "osr"]), dsr=float(de.loc[t, "dsr"]),
                           oppg=float(off.loc[t, "oplays"]) / float(g_), dppg=float(de.loc[t, "dplays"]) / float(g_), gp=int(g_))
    db = pbp[pbp.qb_dropback == 1].dropna(subset=["passer_player_name"])
    qbs = defaultdict(list)
    for (t, n), grp in db.groupby(["posteam", "passer_player_name"]):
        qbs[str(t)].append(dict(name=str(n), db=int(len(grp)), epa=float(grp.epa.mean())))
    for t in qbs:
        qbs[t].sort(key=lambda x: -x["db"])
    if len(eff) < 20:
        return None
    return {"eff": eff, "qbs": dict(qbs), "weeks": int(pbp.week.max())}


def copy_fields(info, prev, keys):
    if not prev:
        return
    for k in keys:
        if k not in info and prev.get(k) is not None:
            info[k] = prev[k]


def empty_league(L):
    cfg = LEAGUES[L]
    return dict(games=[], teams={}, hfa=None, hfaN=0, sigma=cfg["sigma"], shrink=None, unit=cfg["unit"], backtest={"n": 0}, formN=FORM_N[L], played=0)


def restore_league(L, reason):
    prev = (PREV.get("leagues") or {}).get(L)
    if not prev or not prev.get("games"):
        block = empty_league(L)
        block["keptFrom"] = None
        print(reason, "no previous", L, file=sys.stderr)
        return block
    block = json.loads(json.dumps(prev))
    block["keptFrom"] = PREV.get("asOf")
    print(reason, "kept", L, "from", PREV.get("asOf"), file=sys.stderr)
    return block


def build_league(L, games, ctx):
    cfg = LEAGUES[L]
    prev_lg = (PREV.get("leagues") or {}).get(L) or {}
    prev_teams = prev_lg.get("teams") or {}
    prev_games = {g.get("id"): g for g in prev_lg.get("games") or [] if g.get("id")}
    T = team_table(games)
    hfa, hfan = hfa_est(games, cfg)
    rows, shrink = backtest(L, games)
    past_days = sorted({dt.datetime.fromisoformat(r["g"]["date"].replace("Z", "+00:00")).astimezone(ET).date() for r in rows})
    if L == "NFL":
        cons_idx, close_state, _ = load_an(L, list(range(1, 19)), historical=True)
    else:
        cons_idx, close_state, _ = load_an(L, past_days, historical=True)
    bt = bt_report(L, rows, shrink, cons_idx)
    gate_bt = bt
    if L == "NFL":
        older = espn_nfl_season(2025)
        if older:
            grows, gshrink = backtest(L, older + games)
            idx_old, _, _ = load_an(L, list(range(1, 19)), historical=True, season=2025)
            merged = dict(idx_old)
            merged.update(cons_idx)
            gate_bt = bt_report(L, grows, gshrink, merged)
    if not bt.get("market") and close_state != "fresh":
        old_bt = prev_lg.get("backtest") or {}
        if old_bt.get("market"):
            bt["market"] = old_bt["market"]
            if old_bt.get("bets"):
                bt["bets"] = old_bt["bets"]
            bt["marketFrom"] = PREV.get("asOf")
    inj_raw = ctx["inj"].get(L)
    inj = (inj_raw or {}).get("teams") or {}
    inj_ok = inj_raw is not None
    days = [TODAY + dt.timedelta(days=i) for i in range(DAYS)]
    end = dt.datetime.combine(TODAY + dt.timedelta(days=DAYS), dt.time(5, 0), tzinfo=dt.timezone.utc).isoformat()[:16]
    now_s = dt.datetime.now(dt.timezone.utc).isoformat()[:16]
    if L == "NFL":
        up_weeks = sorted({g["week"] for g in games if g.get("week") and g.get("state") == "pre" and g["date"] >= now_s and g["date"] < end})
        up_days = up_weeks or [TODAY]
        up_idx, up_state, failed_days = load_an(L, up_days, historical=False, season=2026)
    else:
        up_idx, up_state, failed_days = load_an(L, days, historical=False)
    upcoming = [g for g in games if g["state"] == "pre" and g["date"] >= now_s and g["date"] < end]
    lg_pf = sum(t["pf"] for t in T.values()) / max(1, sum(t["g"] for t in T.values()))
    mp = ctx.get("mp") or {}
    mp_teams = mp.get("teams") or {}
    mpg = mp.get("goalies") or []
    mps = mp.get("skaters") or []
    mp_ok = ctx.get("mp") is not None
    nhlapi = ctx.get("nhl") or {}
    nhl_ok = ctx.get("nhl") is not None
    dfo = ctx.get("dfo") or {}
    dfo_ok = ctx.get("dfo") is not None
    bref_team = ctx.get("bref_team") or {}
    bref_ok = ctx.get("bref_team") is not None
    bref_players = ctx.get("bref_players") or {}
    nfl = ctx.get("nfl") or {}
    nfl_ok = ctx.get("nfl") is not None
    nfl_eff = nfl.get("eff") or {}
    qbs = nfl.get("qbs") or {}
    alias = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
    rev_alias = {v: k for k, v in alias.items()}
    out_games = []
    teamstats = {}
    for g in upcoming:
        rec = dict(id=g["id"], t=g["date"], venue=g["venue"], neutral=g["neutral"])
        S = {}
        notes = []
        for side in ("home", "away"):
            tm = g[side]
            s = strength(T, tm["name"], FORM_N[L])
            if not s:
                break
            prev = prev_teams.get(tm["abbr"]) or {}
            rd = rest_days(s["last"], g["date"])
            info = dict(n=tm["name"], a=tm["abbr"], rec=tm["records"].get("total"), hr=tm["records"].get("home"), rr=tm["records"].get("road"),
                        gp=s["gp"], md=round(s["md"], 2), sos=round(s["sos"], 2), adj=round(s["adj"], 2), form=round(s["form"], 2),
                        formRec=f"{s['formW']}-{s['formN'] - s['formW']}", formN=s["formN"], hm=None if s["hm"] is None else round(s["hm"], 2),
                        rm=None if s["rm"] is None else round(s["rm"], 2), hg=s["hg"], rg=s["rg"], pf=round(s["pf"], 2), pa=round(s["pa"], 2), rest=rd)
            if inj_ok:
                imp = 0.0
                inj_list = []
                for p in inj.get(tm["name"], []):
                    p = dict(p)
                    v = 0.0
                    if L == "NBA" and bref_players:
                        bp = bref_players.get(norm(p.get("name")))
                        if bp and bp["g"] > 0:
                            mpg_ = bp["mp"] / bp["g"]
                            share = min(1, bp["g"] / max(1, s["gp"]))
                            v = max(0, bp["bpm"] + 2.0) * mpg_ / 48 * share * SEV[p["status"]]
                            p["detail"] = f"{mpg_:.1f} min/g, BPM {bp['bpm']:+.1f}, played {int(bp['g'])} of {s['gp']}"
                    elif L == "NHL" and mps:
                        sk = next((x for x in mps if norm(x.get("name")) == norm(p.get("name"))), None)
                        if sk and fnum(sk, "games_played"):
                            gp_ = fnum(sk, "games_played")
                            pts = fnum(sk, "I_F_points")
                            xg = fnum(sk, "I_F_xGoals")
                            if pts is not None:
                                ppg = pts / gp_
                                share = min(1, gp_ / max(1, s["gp"]))
                                v = max(0, ppg - 0.25) * 0.35 * share * SEV[p["status"]]
                                p["detail"] = f"{ppg:.2f} pts/g, xG {(xg or 0):.1f}, played {int(gp_)} of {s['gp']}"
                    elif L == "NFL" and p.get("pos") == "QB" and qbs:
                        q = qbs.get(tm["abbr"], [])
                        if q and norm(p.get("name", "").split()[-1]) == norm(q[0]["name"].split(".")[-1]) and q[0]["db"] >= 60:
                            backup = q[1]["epa"] if len(q) > 1 and q[1]["db"] >= 30 else -0.15
                            dbpg = q[0]["db"] / max(1, nfl_eff.get(tm["abbr"], {}).get("gp", 1))
                            v = max(0, q[0]["epa"] - backup) * min(dbpg, 40) * SEV[p["status"]]
                            p["detail"] = f"starter by dropbacks ({q[0]['db']}), {q[0]['epa']:+.2f} EPA/dropback"
                    p["impact"] = round(v, 3)
                    imp += v
                    inj_list.append(p)
                inj_list.sort(key=lambda p: (-p["impact"], -SEV.get(p["status"], 0)))
                info["inj"] = [dict(n=p.get("name"), pos=p.get("pos"), st=p.get("status"), imp=p["impact"], d=p.get("detail")) for p in inj_list[:4]]
                info["injN"] = sum(1 for p in inj_list if SEV.get(p["status"], 0) >= 1)
                info["injImp"] = round(imp, 3)
                news = []
                for p in inj_list[:3]:
                    if p["impact"] > (0.03 if L == "NHL" else 0.3) or (L == "NFL" and p.get("pos") in ("QB", "WR", "RB", "TE") and SEV.get(p["status"], 0) >= 0.75):
                        t_ = (p.get("note") or "").strip()
                        if len(t_) < 25:
                            t_ = "per the ESPN injury report, no further detail."
                        if len(t_) > 150:
                            t_ = t_[:147].rsplit(" ", 1)[0] + "..."
                        news.append(dict(tx=f"{p.get('name')} ({p.get('status')}): {t_}", src=p.get("url"), imp=p["impact"]))
                info["news"] = news[:2]
            else:
                if prev and prev.get("injImp") is not None:
                    info["inj"] = prev.get("inj") or []
                    info["injN"] = prev.get("injN")
                    info["injImp"] = prev.get("injImp")
                    info["news"] = prev.get("news") or []
                else:
                    info["inj"] = []
                    info["injN"] = None
                    info["injImp"] = None
                    info["news"] = []
            if L == "NHL":
                if mp_ok:
                    r = mp_teams.get(tm["abbr"])
                    if r and fnum(r, "games_played"):
                        gp_ = fnum(r, "games_played")
                        xgf, xga = fnum(r, "scoreVenueAdjustedxGoalsFor"), fnum(r, "scoreVenueAdjustedxGoalsAgainst")
                        xgp, cf = fnum(r, "xGoalsPercentage"), fnum(r, "corsiPercentage")
                        gf, sogf = fnum(r, "goalsFor"), fnum(r, "shotsOnGoalFor")
                        ga, soga = fnum(r, "goalsAgainst"), fnum(r, "shotsOnGoalAgainst")
                        if None not in (xgf, xga, xgp, cf, gf, sogf, ga, soga):
                            info.update(xgf=round(xgf / gp_, 2), xga=round(xga / gp_, 2), xgp=round(xgp * 100, 1), cf=round(cf * 100, 1),
                                        pdo=round(100 * (gf / max(1, sogf) + 1 - ga / max(1, soga)), 1))
                else:
                    copy_fields(info, prev, ("xgf", "xga", "xgp", "cf", "pdo"))
                if nhl_ok:
                    api = nhlapi.get(tm["name"]) or next((v for k, v in nhlapi.items() if nick(k) == nick(tm["name"])), None)
                    if api and all(isinstance(api.get(k), (int, float)) for k in ("powerPlayPct", "penaltyKillPct", "shotsForPerGame", "shotsAgainstPerGame")):
                        info.update(pp=round(api["powerPlayPct"] * 100, 1), pk=round(api["penaltyKillPct"] * 100, 1), sf=round(api["shotsForPerGame"], 1), sa=round(api["shotsAgainstPerGame"], 1))
                else:
                    copy_fields(info, prev, ("pp", "pk", "sf", "sa"))
                if dfo_ok:
                    gday = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(ET).date().isoformat()
                    gl = dfo.get(f"{gday}|{nick(tm['name'])}")
                    team_g = [x for x in mpg if x.get("team") in (tm["abbr"], rev_alias.get(tm["abbr"], ""))]
                    tgsax = 0.0
                    gsax_rows = []
                    for x in team_g:
                        xg, goals = fnum(x, "xGoals"), fnum(x, "goals")
                        if xg is None or goals is None:
                            continue
                        gsax_rows.append((x, xg - goals))
                    if gsax_rows:
                        tgsax = sum(v for _, v in gsax_rows) / max(1, s["gp"])
                    gadj = 0.0
                    if gl and gl.get("name"):
                        st_ = next((x for x, _ in gsax_rows if norm(x.get("name")) == norm(gl["name"])), None)
                        if st_ and fnum(st_, "games_played"):
                            ggp = fnum(st_, "games_played")
                            gs = (fnum(st_, "xGoals") - fnum(st_, "goals")) / ggp
                            gs_reg = gs * ggp / (ggp + 10)
                            gadj = gs_reg - tgsax * s["gp"] / (s["gp"] + 10)
                            info["goalie"] = dict(n=gl["name"], st=gl["status"], gp=int(ggp), gsax=round(gs, 2), sv=gl.get("sv"))
                        else:
                            info["goalie"] = dict(n=gl["name"], st=gl["status"], gp=0, gsax=None, sv=gl.get("sv"))
                        gsax = info["goalie"].get("gsax")
                        notes.append(dict(tx=f"{tm['abbr']} goalie: {gl['name']} ({str(gl['status']).lower()} per DailyFaceoff)" + (f", {gsax:+.2f} GSAx/game this season" if isinstance(gsax, (int, float)) else ""), src=gl.get("src") or gl.get("page")))
                    info["gAdj"] = round(gadj, 3)
                else:
                    info["gAdj"] = 0.0
            elif L == "NBA":
                if bref_ok:
                    b = bref_team.get(nick(tm["name"]))
                    if b:
                        info.update(b)
                else:
                    copy_fields(info, prev, ("ortg", "drtg", "nrtg", "pace", "srs", "sos"))
            elif L == "NFL":
                if nfl_ok:
                    e_ = nfl_eff.get(tm["abbr"])
                    if e_:
                        info.update(oepa=round(e_["oepa"], 3), depa=round(e_["depa"], 3), osr=round(e_["osr"] * 100, 1), dsr=round(e_["dsr"] * 100, 1), ppg=round((e_["oppg"] + e_["dppg"]) / 2, 1))
                    q = qbs.get(tm["abbr"], [])
                    if q:
                        info["qb"] = dict(n=q[0]["name"], db=q[0]["db"], epa=round(q[0]["epa"], 3))
                else:
                    copy_fields(info, prev, ("oepa", "depa", "osr", "dsr", "ppg", "qb"))
            S[side] = info
        if len(S) < 2:
            continue
        h, a = S["home"], S["away"]
        if L == "NHL" and not dfo_ok:
            pg = prev_games.get(g["id"]) or {}
            fb = ((load_snap("goalie_fallback") or {}).get("data") or {}).get("games") or {}
            srcg = pg if (pg.get("hG") or pg.get("aG")) else (fb.get(g["id"]) or {})
            if srcg.get("hG") or srcg.get("aG"):
                h["goalie"] = srcg.get("hG")
                a["goalie"] = srcg.get("aG")
                if isinstance((srcg.get("adj") or {}).get("goalie"), (int, float)):
                    h["gAdj"] = srcg["adj"]["goalie"]
                else:
                    h["gAdj"] = srcg.get("goalie") or 0
                a["gAdj"] = 0
            else:
                h["gAdj"] = 0.0
                a["gAdj"] = 0.0
        C = dict(season=round(h["adj"] - a["adj"], 3), form=round(h["form"] - a["form"], 3))
        if h["hm"] is not None and a["rm"] is not None:
            C["split"] = round(split_c(L, dict(hm=h["hm"], hg=h["hg"]), dict(rm=a["rm"], rg=a["rg"]), hfa), 3)
        if L == "NHL" and "xgf" in h and "xgf" in a:
            C["xg"] = round((h["xgf"] - h["xga"]) - (a["xgf"] - a["xga"]), 3)
            C["corsi"] = round((h["cf"] - a["cf"]) * 0.12, 3)
        if L == "NBA" and "nrtg" in h and "nrtg" in a:
            C["srs"] = round(h["srs"] - a["srs"], 3)
            C["net"] = round((h["nrtg"] - a["nrtg"]) * (h["pace"] + a["pace"]) / 200, 3)
        if L == "NFL" and "oepa" in h and "oepa" in a:
            C["epa"] = round(((h["oepa"] - h["depa"]) - (a["oepa"] - a["depa"])) * (h["ppg"] + a["ppg"]) / 2, 3)
        h2h = [x for x in T[h["n"]]["log"] if x[2] == a["n"]]
        if h2h:
            C["h2h"] = round(sum(x[1] for x in h2h) / len(h2h) * len(h2h) / (len(h2h) + 2), 3)
        rec["h2h"] = [dict(d=x[0][:10], m=x[1], v=x[3]) for x in h2h]
        rec["C"] = C
        himp = h["injImp"] if isinstance(h.get("injImp"), (int, float)) else 0
        aimp = a["injImp"] if isinstance(a.get("injImp"), (int, float)) else 0
        rec["adj"] = dict(rest=round(rest_adj(L, h["rest"], a["rest"]), 3), inj=round(aimp - himp, 3),
                          goalie=round((h.get("gAdj") or 0) - (a.get("gAdj") or 0), 3) if L == "NHL" else 0)
        pack = ctx.get("nhl_pack")
        if L == "NHL" and pack:
            coef = pack["coef"]
            ratings = pack["ratings"]
            goalies = pack["goalies"]
            most = pack["most"]
            def goalie_name(info, abbr):
                posted = (info.get("goalie") or {}).get("n")
                if posted and nhl_xg.gkey(posted) in goalies:
                    return posted
                return most.get(abbr) or posted
            def shrunk_gs(name):
                recg = goalies.get(nhl_xg.gkey(name or ""))
                return recg["shrunk"] if recg else 0.0
            hn, an = goalie_name(h, h["a"]), goalie_name(a, a["a"])
            C["xg"] = round(coef["bXg"] * (ratings.get(h["a"], {}).get("xgd", 0.0) - ratings.get(a["a"], {}).get("xgd", 0.0)), 3)
            rec["adj"]["goalie"] = round(coef["bGoalie"] * (shrunk_gs(hn) - shrunk_gs(an)), 3)
            for info, name in ((h, hn), (a, an)):
                recg = goalies.get(nhl_xg.gkey(name or ""))
                if not name:
                    continue
                info["goalie"] = dict(n=name, st=(info.get("goalie") or {}).get("st") or "Most starts", gp=(recg or {}).get("gp") or 0,
                                      gsax=None if not recg or recg.get("shrunk") is None else round(recg["shrunk"], 2), sv=(info.get("goalie") or {}).get("sv"))
                if recg and isinstance(recg.get("raw"), (int, float)):
                    notes.append(dict(tx=f"{info['a']} goalie {name}: raw {recg['raw']:+.2f} GSAx/start over {recg['gp']} starts, shrunk to {recg['shrunk']:+.2f} (last season {recg['prior']:+.2f}).", src="https://moneypuck.com/data.htm"))
        pool = list(bref_team.values()) if bref_team else []
        if len(pool) < 10:
            pool = [t for t in prev_teams.values() if isinstance(t.get("ortg"), (int, float)) and isinstance(t.get("pace"), (int, float)) and isinstance(t.get("drtg"), (int, float))]
        if L == "NBA" and "ortg" in h and "ortg" in a and len(pool) >= 10:
            lg_r = sum(v["ortg"] for v in pool) / len(pool)
            lg_p = sum(v["pace"] for v in pool) / len(pool)
            pace = h["pace"] * a["pace"] / lg_p
            ph = h["ortg"] * a["drtg"] / lg_r * pace / 100
            pa_ = a["ortg"] * h["drtg"] / lg_r * pace / 100
        elif L == "NHL" and "xgf" in h and "xgf" in a:
            gf = lambda x: (x["pf"] + x["xgf"]) / 2
            ga = lambda x: (x["pa"] + x["xga"]) / 2
            ph = gf(h) * ga(a) / lg_pf
            pa_ = gf(a) * ga(h) / lg_pf
        else:
            ph = h["pf"] * a["pa"] / lg_pf
            pa_ = a["pf"] * h["pa"] / lg_pf
        rec["proj"] = dict(total=round(ph + pa_, 2))
        ag = match_an(up_idx, g)
        rec["odds"] = odds_from_an(ag, g.get("espn_odds"))
        if rec["odds"] is None and up_state in ("stale", "unavailable", "partial"):
            pg = prev_games.get(g["id"])
            gday = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(ET).date().isoformat()
            if pg and pg.get("odds") and (up_state != "partial" or gday in failed_days):
                rec["odds"] = pg["odds"]
        rec["notes"] = notes[:3]
        for side, x in (("h", h), ("a", a)):
            rec[side] = x["a"]
            rec[side + "G"] = x.pop("goalie", None)
            rec[side + "Rest"] = x.pop("rest")
            teamstats[x["a"]] = {k: v for k, v in x.items() if k not in ("gAdj",)}
        rec["h2h"] = [f'{x["d"][5:]} {"W by " + format(abs(x["m"]), "g") if x["m"] > 0 else ("L by " + format(abs(x["m"]), "g") if x["m"] < 0 else "T")} ({"home" if x["v"] == "home" else "away"})' for x in rec["h2h"]]
        if rec.get("odds") and rec["odds"].get("books") and isinstance(rec["odds"]["books"][0], dict):
            o = rec["odds"]
            o["books"] = [[b["book"], b["mlH"], b["mlA"]] for b in o["books"]]
        out_games.append(rec)
    block = dict(games=out_games, teams=teamstats, hfa=round(hfa, 3), hfaN=hfan, sigma=cfg["sigma"], shrink=shrink, unit=cfg["unit"],
                 backtest=bt, formN=FORM_N[L], played=sum(1 for g in games if g["completed"]), model="results",
                 valueGate=value_gate(gate_bt), sample="this season",
                 marketBetter=bool((gate_bt.get("market") or {}).get("marketBrier") is not None and gate_bt["market"]["marketBrier"] < (gate_bt.get("market") or {}).get("modelBrier", 9)))
    if L == "NFL" and gate_bt is not bt:
        block["backtest"] = gate_bt
        block["sample"] = "2025 and 2026 seasons"
        block["shrink"] = shrink
    pack = ctx.get("nhl_pack")
    if L == "NHL" and pack:
        oos_dates = sorted({int(pr["date"]) for pr in pack["oos"]})
        oos_days = [dt.date(int(str(d)[:4]), int(str(d)[4:6]), int(str(d)[6:])) for d in oos_dates]
        oos_idx, _, _ = load_an("NHL", oos_days, historical=True)
        after = grade_predictions(pack["oos"], oos_idx)
        hist = nhl_xg.as_results_games(pack["games"])
        hrows, hshrink = backtest("NHL", hist)
        before_preds = []
        oos_keys = {(pr["date"], pr["home"], pr["away"]) for pr in pack["oos"]}
        for r in hrows:
            d = dt.datetime.fromisoformat(r["g"]["date"].replace("Z", "+00:00"))
            keydate = int(d.strftime("%Y%m%d"))
            ha, aa = r["g"]["home"].get("abbr"), r["g"]["away"].get("abbr")
            if (keydate, ha, aa) not in oos_keys:
                continue
            p = phi(results_margin("NHL", r["Sh"], r["Sa"], r["hfa"], r["rh"], r["ra"], hshrink) / cfg["sigma"])
            before_preds.append({"iso": r["g"]["date"], "homeName": r["g"]["home"]["name"], "awayName": r["g"]["away"]["name"], "y": r["y"], "p": p, "date": keydate})
        before = grade_predictions(before_preds, oos_idx)
        after["before"] = {k: v for k, v in before.items() if k != "calib"}
        bt = after
        block["backtest"] = bt
        block["model"] = "xg"
        block["shrink"] = 1
        block["hfaEmpirical"] = round(hfa, 3)
        block["hfa"] = round(pack["coef"]["hfa"], 3)
        block["coef"] = {k: pack["coef"][k] for k in ("bXg", "bGoalie", "hfa", "halfLife", "kTeam", "kGoalie", "n", "season", "holdoutDate")}
        block["valueGate"] = value_gate(after)
        block["marketBetter"] = bool(after.get("marketBetter"))
        block["sample"] = "out of sample: last 40% of 2025-26, plus 2026-27"
    if L == "NFL":
        if nfl_ok and nfl.get("weeks"):
            block["pbpWeeks"] = nfl["weeks"]
        elif prev_lg.get("pbpWeeks"):
            block["pbpWeeks"] = prev_lg["pbpWeeks"]
    return block


def goalie_stamp():
    """Clock for projections carried forward after DailyFaceoff blocks the runner."""
    existing = load_snap("goalie_fallback")
    if existing and existing.get("at"):
        return existing["at"]
    for src in PREV.get("sources") or []:
        if src.get("id") == "goalies" and src.get("at") and src.get("state") in ("fresh", "stale", "partial"):
            return src["at"]
    return PREV.get("asOf") or now_et()


def freeze_goalies(data, dfo):
    """Remember the projections actually published, and say so when the feed is blocked."""
    nhl = (data.get("leagues") or {}).get("NHL") or {}
    published = {}
    for g in nhl.get("games") or []:
        if g.get("hG") or g.get("aG"):
            published[g["id"]] = {"hG": g.get("hG"), "aG": g.get("aG"), "goalie": (g.get("adj") or {}).get("goalie")}
    if dfo is not None:
        if published:
            save_snap("goalie_fallback", {"games": published})
        return
    if not published:
        return
    at = goalie_stamp()
    save_snap("goalie_fallback", {"games": published}, at=at)
    PARTS["goalies"] = [{
        "name": "DailyFaceoff",
        "state": "stale",
        "at": at,
        "detail": f"DailyFaceoff is blocked from this network. Projected goalies are the last good copy from {at}.",
    }]


def main():
    print("gather", TODAY.isoformat(), now_et(), file=sys.stderr)
    days = [TODAY + dt.timedelta(days=i) for i in range(DAYS)]
    mp = resolve("moneypuck", "MoneyPuck", "moneypuck", load_moneypuck)
    nhl = resolve("nhl", "NHL stats API", "nhl_api", load_nhl_api)
    dfo = resolve("goalies", "DailyFaceoff", "dailyfaceoff", lambda: load_dfo(days))
    bref_team = resolve("bref", "Basketball-Reference teams", "bref_teams", load_bref_teams)
    bref_players = resolve("bref", "Basketball-Reference players", "bref_players", load_bref_players)
    nfl = resolve("nflverse", "nflverse EPA", "nflverse", load_nflverse)
    nhl_pack = None
    try:
        print("fitting NHL xG model", file=sys.stderr)
        nhl_pack = nhl_xg.build()
        asof = int((TODAY + dt.timedelta(days=1)).strftime("%Y%m%d"))
        ratings, goalies, most = nhl_xg.live_ratings(nhl_pack, asof)
        nhl_pack = {**nhl_pack, "ratings": ratings, "goalies": goalies, "most": most}
        print("NHL coef", nhl_pack["coef"], "oos", len(nhl_pack["oos"]), file=sys.stderr)
    except Exception:
        traceback.print_exc()
        print("NHL xG model unavailable; using the results model", file=sys.stderr)
        nhl_pack = None
    inj = {}
    for L in LEAGUES:
        inj[L] = resolve("espn", f"{L} injuries", f"inj_{L}", lambda L=L: load_injuries(L), is_ok=lambda d: isinstance(d, dict) and "teams" in d)
    ctx = dict(mp=mp, nhl=nhl, dfo=dfo, bref_team=bref_team, bref_players=bref_players, nfl=nfl, inj=inj, nhl_pack=nhl_pack)
    data = dict(asOf=now_et(), leagues={})
    for L in LEAGUES:
        print("league", L, file=sys.stderr)
        try:
            games = season_events(L)
            if not games:
                data["leagues"][L] = restore_league(L, "empty schedule")
                continue
            data["leagues"][L] = build_league(L, games, ctx)
            lg = data["leagues"][L]
            print(L, len(lg["games"]), "games; odds", sum(1 for g in lg["games"] if g.get("odds")), "played", lg["played"], file=sys.stderr)
        except Exception:
            traceback.print_exc()
            data["leagues"][L] = restore_league(L, "build failed")
    freeze_goalies(data, dfo)
    data["sources"] = finalize_sources()
    new_n = sum(len((data["leagues"].get(L) or {}).get("games") or []) for L in LEAGUES)
    old_n = sum(len(((PREV.get("leagues") or {}).get(L) or {}).get("games") or []) for L in LEAGUES)
    if new_n == 0 and old_n > 0:
        print(f"refusing to replace {old_n} games with an empty slate", file=sys.stderr)
        return 0
    atomic_write(DATA_PATH, data)
    print("wrote", DATA_PATH, os.path.getsize(DATA_PATH), file=sys.stderr)
    for s in data["sources"]:
        print(f"source {s['id']}: {s['state']} @ {s.get('at')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if os.environ.get("GITHUB_ACTIONS") == "true" and os.path.exists(DATA_PATH) and os.path.getsize(DATA_PATH) > 1000:
            print("gather crashed; leaving the last data file in place", file=sys.stderr)
            sys.exit(0)
        sys.exit(1)
