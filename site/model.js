// Client model. Probabilities come from the stats in data.json, never from the
// betting line. The line is used only to measure disagreement.
// Game times are parsed by hand (no Date-string parsing) and shifted onto the
// America/Toronto clock so Safari and a phone set to another zone still show
// Eastern Time.

export const LGS = ["NHL", "NBA", "NFL"];
export const COMPS = {
  NHL: [
    { key: "season", label: "Season goal diff, schedule-adjusted", def: 1 },
    { key: "form", label: "Last 10 games form", def: 0.3 },
    { key: "split", label: "Home / road split", def: 0.2 },
    { key: "xg", label: "Expected goals diff (MoneyPuck)", def: 1 },
    { key: "corsi", label: "Shot share, Corsi", def: 0.4 },
    { key: "h2h", label: "Head-to-head this season", def: 0.1 },
  ],
  NBA: [
    { key: "season", label: "Season point diff, schedule-adjusted", def: 0.5 },
    { key: "form", label: "Last 10 games form", def: 0.3 },
    { key: "split", label: "Home / road split", def: 0.2 },
    { key: "srs", label: "SRS (Basketball-Reference)", def: 1 },
    { key: "net", label: "Net rating x pace", def: 1 },
    { key: "h2h", label: "Head-to-head this season", def: 0.1 },
  ],
  NFL: [
    { key: "season", label: "Season point diff, schedule-adjusted", def: 0.6 },
    { key: "form", label: "Last 4 games form", def: 0.3 },
    { key: "split", label: "Home / road split", def: 0.15 },
    { key: "epa", label: "EPA per play, net (nflverse)", def: 1 },
    { key: "h2h", label: "Head-to-head this season", def: 0.1 },
  ],
};
export const ADJS = [
  { key: "hfa", label: "Home advantage" },
  { key: "rest", label: "Rest and back-to-backs" },
  { key: "inj", label: "Injuries" },
  { key: "goalie", label: "Projected goalie (NHL)" },
];
export const FORM_N = { NHL: 10, NBA: 10, NFL: 4 };
export const UNIT = { NHL: "goals", NBA: "points", NFL: "points" };
export const BOOK = { DK: "DraftKings", FD: "FanDuel", MGM: "BetMGM", BR: "BetRivers", 365: "bet365", CZR: "Caesars", BRNY: "BetRivers NY", UNI: "Unibet", FAN: "Fanatics", CONS: "Consensus" };
export const STATUS = { O: "Out", IR: "Injured reserve", S: "Suspended", D: "Doubtful", Q: "Questionable", DTD: "Day-to-day", PUP: "PUP list" };
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MO = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// A wild ticket is a long shot: decimal odds of 11 are American +1000,
// which returns $110 on a $10 stake including the stake.
export const WILD_MIN_LEGS = 5;
export const WILD_MAX_LEGS = 8;
export const WILD_MIN_DECIMAL = 11;
export const WILD_POOL = 18;

export function defaults(data) {
  const w = {};
  LGS.forEach((L) => {
    COMPS[L].forEach((c) => { w[`${L}.${c.key}`] = c.def; });
    ADJS.forEach((a) => { w[`${L}.${a.key}`] = 1; });
    w[`${L}.shrink`] = data.L[L].shrink;
  });
  return w;
}

export function erf(x) {
  const s = x < 0 ? -1 : 1;
  const a = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * a);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-a * a));
  return s * y;
}
export const Phi = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
export const dec = (ml) => (typeof ml !== "number" || !isFinite(ml) || ml === 0 ? 1 : ml > 0 ? 1 + ml / 100 : 1 + 100 / -ml);
export const fmtML = (ml) => (typeof ml !== "number" || !isFinite(ml) ? "unavailable" : ml > 0 ? `+${ml}` : `${ml}`);
export const pct = (p) => (typeof p !== "number" || !isFinite(p) ? "unavailable" : `${(p * 100).toFixed(1)}%`);
export const sgn = (v, d = 2) => (typeof v !== "number" || !isFinite(v) ? "unavailable" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`);

export function ts(t) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)/.exec(String(t ?? ""));
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : NaN;
}

function nthSundayUTC(year, monthIndex, n, hour) {
  const firstDow = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
  const day = 1 + (firstDow === 0 ? 0 : 7 - firstDow) + (n - 1) * 7;
  return Date.UTC(year, monthIndex, day, hour, 0, 0);
}

// Hours to subtract from UTC. DST: second Sunday in March 07:00 UTC through
// the first Sunday in November 06:00 UTC.
export function easternOffsetHours(utcMs) {
  const y = new Date(utcMs).getUTCFullYear();
  const start = nthSundayUTC(y, 2, 2, 7);
  const end = nthSundayUTC(y, 10, 1, 6);
  return utcMs >= start && utcMs < end ? 4 : 5;
}

export function etDate(t) {
  const utc = ts(t);
  if (isNaN(utc)) return new Date(NaN);
  return new Date(utc - easternOffsetHours(utc) * 3600 * 1000);
}
export function dayKey(t) {
  const d = etDate(t);
  return isNaN(d.getTime()) ? "TBD" : d.toISOString().slice(0, 10);
}
export function dayLabel(k) {
  const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(String(k ?? ""));
  if (!m) return "Date TBD";
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], 12));
  return `${WD[d.getUTCDay()]} ${MO[d.getUTCMonth()]} ${d.getUTCDate()}`;
}
export function timeLabel(t) {
  const d = etDate(t);
  if (isNaN(d.getTime())) return "TBD";
  const h = d.getUTCHours();
  const m = d.getUTCMinutes();
  return `${h % 12 === 0 ? 12 : h % 12}:${m < 10 ? "0" : ""}${m} ${h < 12 ? "AM" : "PM"}`;
}
export function formatAsOf(asOf) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)/.exec(String(asOf ?? ""));
  if (!m) return "time unavailable";
  const h = +m[4];
  const ap = h < 12 ? "AM" : "PM";
  const hr = h % 12 === 0 ? 12 : h % 12;
  return `${dayLabel(`${m[1]}-${m[2]}-${m[3]}`)}, ${hr}:${m[5]} ${ap} ET`;
}
export function americanFromDec(d) {
  if (!(typeof d === "number" && isFinite(d) && d > 1)) return null;
  const profit = d - 1;
  if (profit >= 1) return Math.round(profit * 100);
  return Math.round(-100 / profit);
}
export const fx = (v, d) => (typeof v === "number" && isFinite(v) ? v.toFixed(d) : "unavailable");
export function okOdds(o) {
  return Array.isArray(o) && typeof o[0] === "number" && o[0] > 0 && o[0] < 1 && typeof o[1] === "number" && o[1] !== 0 && typeof o[3] === "number" && o[3] !== 0;
}

export function sanitize(data) {
  data.L = data.L || {};
  data.sources = Array.isArray(data.sources) ? data.sources : [];
  LGS.forEach((L) => {
    const lg = (data.L[L] = data.L[L] || {});
    lg.teams = lg.teams || {};
    Object.keys(lg.teams).forEach((k) => {
      const t = lg.teams[k];
      if (!Array.isArray(t)) {
        delete lg.teams[k];
        return;
      }
      [15, 16, 17].forEach((i) => { if (!Array.isArray(t[i])) t[i] = []; });
    });
    lg.hfa = Number(lg.hfa) || 0;
    lg.sigma = Number(lg.sigma) > 0 ? Number(lg.sigma) : L === "NHL" ? 2.4 : 13;
    lg.shrink = Number(lg.shrink) > 0 ? Number(lg.shrink) : 0.5;
    const bt = (lg.bt = lg.bt || {});
    bt.n = Number(bt.n) || 0;
    bt.hit = Number(bt.hit) || 0;
    bt.brier = Number(bt.brier) || 0;
    bt.calib = (Array.isArray(bt.calib) ? bt.calib : []).filter((c) => Array.isArray(c) && [0, 2, 3].every((i) => typeof c[i] === "number" && isFinite(c[i])));
    if (bt.mkt && !(typeof bt.mkt[1] === "number" && typeof bt.mkt[2] === "number")) bt.mkt = null;
    if (bt.bets && !(typeof bt.bets[2] === "number")) bt.bets = null;
    const tot = L === "NHL" ? 6 : L === "NBA" ? 228 : 44;
    lg.games = (Array.isArray(lg.games) ? lg.games : [])
      .filter((g) => Array.isArray(g) && !isNaN(ts(g[0])) && !!lg.teams[g[1]] && !!lg.teams[g[2]])
      .map((g0) => {
        const g = [...g0];
        if (!Array.isArray(g[5])) g[5] = [];
        const adj = Array.isArray(g[6]) ? g[6] : [];
        g[6] = [0, 1, 2].map((i) => (typeof adj[i] === "number" && isFinite(adj[i]) ? adj[i] : 0));
        if (typeof g[7] !== "number" || !isFinite(g[7])) g[7] = tot;
        if (!okOdds(g[8])) g[8] = null;
        else if (!Array.isArray(g[8][7])) g[8][7] = [];
        if (!Array.isArray(g[11])) g[11] = [];
        return g;
      });
  });
  return data;
}

export function evaluate(data, L, g, idx, w) {
  const lg = data.L[L];
  const neutral = g[12] === 1;
  const C = g[5];
  let num = 0;
  let den = 0;
  const parts = [];
  COMPS[L].forEach((c, i) => {
    const v = c.key === "split" && neutral ? null : C[i];
    const wt = w[`${L}.${c.key}`];
    if (v === null || v === undefined || !(wt > 0)) return;
    num += wt * v;
    den += wt;
    parts.push({ label: c.label, wv: wt * v });
  });
  const s = w[`${L}.shrink`];
  const contribs = parts.map((x) => ({ label: x.label, v: den > 0 ? (s * x.wv) / den : 0 }));
  const hfa = neutral ? 0 : lg.hfa * w[`${L}.hfa`];
  const rest = g[6][0] * w[`${L}.rest`];
  const inj = g[6][1] * w[`${L}.inj`];
  const goalie = L === "NHL" ? g[6][2] * w[`${L}.goalie`] : 0;
  contribs.push({ label: neutral ? "Home advantage (neutral site)" : "Home advantage", v: hfa });
  if (rest !== 0) contribs.push({ label: "Rest and back-to-backs", v: rest });
  if (inj !== 0) contribs.push({ label: "Injuries", v: inj });
  if (L === "NHL" && goalie !== 0) contribs.push({ label: "Projected goalies", v: goalie });
  const margin = contribs.reduce((acc, c) => acc + c.v, 0);
  const p = Phi(margin / lg.sigma);
  const o = g[8];
  let q = null;
  let edgeH = null;
  let edgeA = null;
  let best = null;
  if (o) {
    q = o[0];
    edgeH = p - o[0];
    edgeA = (1 - p) - (1 - o[0]);
    best = edgeH >= edgeA
      ? { side: "h", edge: edgeH, ml: o[1], book: o[2], p }
      : { side: "a", edge: edgeA, ml: o[3], book: o[4], p: 1 - p };
  }
  return { id: `${L}-${idx}`, L, g, h: lg.teams[g[1]], a: lg.teams[g[2]], p, margin, contribs, q, edgeH, edgeA, best };
}

export function allRows(data, w) {
  const out = [];
  LGS.forEach((L) => data.L[L].games.forEach((g, i) => out.push(evaluate(data, L, g, i, w))));
  return out;
}

export function kellyStake(p, ml, kFrac, bankroll) {
  const b = dec(ml) - 1;
  if (!(b > 0) || typeof p !== "number" || !isFinite(p)) return 0;
  const f = (b * p - (1 - p)) / b;
  return Math.max(0, f) * kFrac * bankroll;
}

function legOf(r) {
  const home = r.p >= 0.5;
  const o = r.g[8];
  return {
    r,
    pick: home ? r.g[1] : r.g[2],
    p: home ? r.p : 1 - r.p,
    ml: home ? o[1] : o[3],
    book: home ? o[2] : o[4],
    edge: home ? r.edgeH : r.edgeA,
    iso: r.g[0],
    L: r.L,
  };
}

export function prodP(ls) {
  return ls.reduce((acc, x) => acc * x.p, 1);
}
export function prodD(ls) {
  return ls.reduce((acc, x) => acc * dec(x.ml), 1);
}

function bundle(combo) {
  const ordered = [...combo].sort((a, b) => (a.pick < b.pick ? -1 : a.pick > b.pick ? 1 : 0));
  const p = prodP(ordered);
  const d = prodD(ordered);
  const edges = ordered.reduce((acc, x) => acc + (typeof x.edge === "number" && x.edge > 0 ? 1 : 0), 0);
  const picks = ordered.map((x) => x.pick).join("|");
  const ev = p * d - 1;
  return {
    legs: ordered,
    p,
    d,
    ev,
    edges,
    key: [Math.round(ev * 1e6) / 1e6, edges, Math.round(d * 1e6) / 1e6, picks],
  };
}

function better(a, b) {
  for (let i = 0; i < a.key.length; i++) if (a.key[i] !== b.key[i]) return a.key[i] > b.key[i];
  return false;
}

function trimPool(legs, cap) {
  if (legs.length <= cap) return legs.slice();
  const value = [];
  const rest = [];
  legs.forEach((x) => ((typeof x.edge === "number" && x.edge > 0 ? value : rest).push(x)));
  const byScore = (a, b) => {
    const as = a.p * dec(a.ml);
    const bs = b.p * dec(b.ml);
    if (as !== bs) return bs - as;
    return a.pick < b.pick ? -1 : a.pick > b.pick ? 1 : 0;
  };
  value.sort(byScore);
  rest.sort(byScore);
  return value.concat(rest).slice(0, cap);
}

function eachCombo(pool, k, fn) {
  const n = pool.length;
  const idx = Array.from({ length: k }, (_, i) => i);
  while (true) {
    fn(idx.map((i) => pool[i]));
    let i = k - 1;
    while (i >= 0 && idx[i] === n - k + i) i--;
    if (i < 0) break;
    idx[i] += 1;
    for (let j = i + 1; j < k; j++) idx[j] = idx[j - 1] + 1;
  }
}

// Maximize model EV among 5-8 leg tickets at +1000 or longer.
// The search prefers positive-edge legs when it has to trim a huge slate,
// and positive-edge count breaks an EV tie.
export function pickWild(dayLegs) {
  if (dayLegs.length < WILD_MIN_LEGS) return null;
  const pool = trimPool(dayLegs, WILD_POOL);
  let best = null;
  const maxK = Math.min(WILD_MAX_LEGS, pool.length);
  for (let k = WILD_MIN_LEGS; k <= maxK; k++) {
    eachCombo(pool, k, (combo) => {
      const b = bundle(combo);
      if (b.d < WILD_MIN_DECIMAL) return;
      if (!best || better(b, best)) best = b;
    });
  }
  if (!best) return null;
  return [...best.legs].sort((a, b) => (a.iso < b.iso ? -1 : a.iso > b.iso ? 1 : a.pick < b.pick ? -1 : a.pick > b.pick ? 1 : 0));
}

export function buildParlays(rows) {
  const by = {};
  rows.forEach((r) => {
    if (!r.g[8]) return;
    const k = dayKey(r.g[0]);
    (by[k] = by[k] || []).push(legOf(r));
  });
  return Object.keys(by).sort().map((day) => {
    const s = [...by[day]].sort((x, y) => y.p - x.p);
    let small = null;
    let big = null;
    let wild = null;
    if (s.length >= 3) small = prodP(s.slice(0, 3)) >= 0.35 ? s.slice(0, 3) : s.slice(0, 2);
    if (s.length >= 5) {
      let n = Math.min(6, s.length);
      while (n > 4 && prodP(s.slice(0, n)) < 0.15) n--;
      big = s.slice(0, n);
      wild = pickWild(s);
    }
    return { day, n: s.length, small, big, wild };
  }).filter((d) => d.small || d.big || d.wild);
}

export function bookName(b) {
  return b === "CONS" ? "Consensus line" : (BOOK[b] ?? b ?? "unavailable");
}
