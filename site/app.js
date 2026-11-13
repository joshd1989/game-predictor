import {
  LGS, COMPS, ADJS, FORM_N, UNIT, BOOK, STATUS,
  sanitize, defaults, allRows, buildParlays, kellyStake,
  dayKey, dayLabel, timeLabel, formatAsOf, pct, sgn, fmtML, fx,
  prodP, prodD, dec, bookName, americanFromDec, easternOffsetHours,
} from "./model.js";

const SVG = "http://www.w3.org/2000/svg";
const ROW_CAP = 20;

const state = {
  view: "ALL",
  day: "all",
  sort: "edge",
  oddsOnly: false,
  valueOnly: false,
  thr: "4",
  bank: "1000",
  kelly: "25",
  w: null,
  tuneL: "NHL",
  sel: "",
  showAll: false,
  showNba: false,
  data: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else node.setAttribute(k, String(v));
  }
  for (const kid of [].concat(kids)) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function svg(tag, attrs, kids = []) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  for (const kid of [].concat(kids)) if (kid) node.append(kid);
  return node;
}

function money(x) {
  if (typeof x !== "number" || !isFinite(x)) return "unavailable";
  return `${x < 0 ? "-$" : "$"}${Math.abs(x).toFixed(2)}`;
}

function showNum(v, d = 1, signed = false) {
  if (typeof v !== "number" || !isFinite(v)) return "unavailable";
  return signed ? sgn(v, d) : v.toFixed(d);
}

function loadSettings() {
  try { return JSON.parse(localStorage.getItem("gp-settings") || "{}"); }
  catch { return {}; }
}
function saveSettings() {
  try {
    localStorage.setItem("gp-settings", JSON.stringify({
      v: 3, thr: state.thr, bank: state.bank, kelly: state.kelly, showNba: state.showNba, w: state.w,
    }));
  } catch { /* private mode */ }
}

function applyThemeLabel() {
  const explicit = document.documentElement.getAttribute("data-theme");
  const dark = explicit ? explicit === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("theme").textContent = dark ? "Light mode" : "Dark mode";
}

function readNumbers() {
  const threshold = (parseFloat(state.thr) || 0) / 100;
  const bankroll = Math.max(0, parseFloat(state.bank) || 0);
  const kFrac = Math.max(0, Math.min(100, parseFloat(state.kelly) || 0)) / 100;
  return { threshold, bankroll, kFrac };
}

function viewLeagues() {
  return state.view === "ALL" ? LGS : [state.view];
}

function rowsNow() {
  return allRows(state.data, state.w);
}

function allowsValue(L) {
  const gate = state.data.L[L] && state.data.L[L].valueGate;
  if (!gate) return true;
  if (gate.eligible) return true;
  return L === "NBA" && state.showNba;
}

function isValue(r, threshold) {
  return allowsValue(r.L) && !!(r.best && r.best.edge >= threshold);
}

function stakeOf(r, kFrac, bankroll) {
  if (!r.best) return 0;
  return kellyStake(r.best.p, r.best.ml, kFrac, bankroll);
}

function matchup(r) {
  return `${r.g[2]} @ ${r.g[1]}`;
}

function sideAbbr(r, s) {
  return s === "h" ? r.g[1] : r.g[2];
}

function table(columns, rows, empty) {
  if (!rows.length) return el("p", { class: "empty" }, empty || "Nothing to show.");
  const t = el("table", { class: "cards" });
  t.append(el("thead", {}, el("tr", {}, columns.map((c) => el("th", { scope: "col" }, c)))));
  const body = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr");
    r.forEach((c, i) => {
      const td = el("td", { "data-label": columns[i] || "" });
      if (c == null) td.append("unavailable");
      else if (c.nodeType) td.append(c);
      else if (typeof c === "object" && "text" in c) td.append(el("span", { class: c.cls || "" }, c.text));
      else td.append(String(c));
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body);
  return el("div", { class: "table-wrap" }, t);
}

function toneEV(ev) {
  return { text: `${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%`, cls: ev >= 0 ? "pos" : "neg" };
}

function renderSources(data) {
  const list = $("sources");
  list.replaceChildren();
  const sources = data.sources || [];
  if (!sources.length) {
    list.hidden = false;
    list.append(el("li", { class: "src unavailable" }, [
      el("span", { class: "src-name" }, "Source status unavailable"),
      el("span", { class: "src-detail" }, "This file does not record which feeds were refreshed."),
    ]));
    return;
  }
  list.hidden = false;
  sources.forEach((s) => {
    const when = s.at ? formatAsOf(s.at) : "";
    let name = s.label || s.id || "Source";
    if (s.state === "fresh") name += ` · fresh${when ? " " + when : ""}`;
    else if (s.state === "stale") name += ` · last good${when ? " " + when : ""}`;
    else if (s.state === "partial") name += ` · incomplete refresh${when ? ", oldest piece " + when : ""}`;
    else name += " · unavailable";
    list.append(el("li", { class: `src ${s.state || "unavailable"}` }, [
      el("span", { class: "src-name" }, name),
      s.detail ? el("span", { class: "src-detail" }, s.detail) : null,
    ]));
  });
}

function renderLeagueButtons(all) {
  const box = $("leagues");
  const options = [{ id: "ALL", label: "All", n: all.length }].concat(LGS.map((L) => ({ id: L, label: L, n: state.data.L[L].games.length })));
  box.replaceChildren(...options.map((o) => {
    const b = el("button", { type: "button", class: `lg lg-${o.id}`, role: "radio", "aria-checked": state.view === o.id ? "true" : "false", onclick: () => { state.view = o.id; state.sel = ""; renderDynamic(); } }, [o.label, el("span", { class: "count" }, String(o.n))]);
    return b;
  }));
}

function setSelect(select, options, value) {
  const sig = options.map((o) => `${o.value}:${o.label}`).join("|");
  if (select.dataset.sig !== sig) {
    select.replaceChildren(...options.map((o) => el("option", { value: o.value }, o.label)));
    select.dataset.sig = sig;
  }
  if ([...select.options].some((o) => o.value === value)) select.value = value;
}

function contribChart(rows, home, away, unit) {
  const W = 640;
  const rh = 28;
  const top = 8;
  const H = top + rows.length * rh + 28;
  const lx = 230;
  const cx = lx + (W - lx) / 2;
  const half = (W - lx) / 2 - 46;
  const mx = Math.max(0.01, ...rows.map((r) => Math.abs(r.v)));
  const parts = [
    svg("line", { x1: cx, x2: cx, y1: top - 4, y2: top + rows.length * rh, stroke: "currentColor", "stroke-opacity": "0.35" }),
  ];
  rows.forEach((r, i) => {
    const y = top + i * rh;
    const len = (Math.abs(r.v) / mx) * half;
    const x = r.v >= 0 ? cx : cx - len;
    const label = r.label.length > 34 ? `${r.label.slice(0, 33)}…` : r.label;
    parts.push(svg("text", { x: 0, y: y + 18, fill: "currentColor", "fill-opacity": "0.75", "font-size": "12" }, label));
    parts.push(svg("rect", { x, y: y + 6, width: Math.max(1, len), height: rh - 12, rx: 3, fill: r.v >= 0 ? "var(--accent)" : "var(--copper)" }));
    parts.push(svg("text", {
      x: r.v >= 0 ? cx + len + 6 : cx - len - 6,
      y: y + 18,
      "text-anchor": r.v >= 0 ? "start" : "end",
      fill: "currentColor",
      "font-size": "12",
    }, sgn(r.v, unit === "goals" ? 2 : 1)));
  });
  parts.push(svg("text", { x: cx - 8, y: H - 8, "text-anchor": "end", fill: "currentColor", "fill-opacity": "0.6", "font-size": "11" }, `favours ${away}`));
  parts.push(svg("text", { x: cx + 8, y: H - 8, "text-anchor": "start", fill: "currentColor", "fill-opacity": "0.6", "font-size": "11" }, `favours ${home}`));
  return svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Contribution of each factor to the projected margin" }, parts);
}

function renderDynamic() {
  const data = state.data;
  const { threshold, bankroll, kFrac } = readNumbers();
  const leagues = viewLeagues();
  const all = rowsNow();
  const inView = all.filter((r) => leagues.includes(r.L));
  const days = [...new Set(inView.map((r) => dayKey(r.g[0])))].sort();
  const dayOk = state.day === "all" || days.includes(state.day);
  let rows = inView.filter((r) => (state.day === "all" || !dayOk || dayKey(r.g[0]) === state.day) && (!state.oddsOnly || r.g[8]) && (!state.valueOnly || isValue(r, threshold)));
  rows = [...rows].sort((x, y) => {
    if (state.sort === "time") return x.g[0] < y.g[0] ? -1 : x.g[0] > y.g[0] ? 1 : 0;
    if (state.sort === "conf") return Math.max(y.p, 1 - y.p) - Math.max(x.p, 1 - x.p);
    return (y.best ? y.best.edge : -9) - (x.best ? x.best.edge : -9);
  });
  const values = inView.filter((r) => (state.day === "all" || !dayOk || dayKey(r.g[0]) === state.day) && isValue(r, threshold))
    .sort((x, y) => (y.best ? y.best.edge : 0) - (x.best ? x.best.edge : 0));
  const calL = state.view === "ALL" ? state.tuneL : state.view;
  const withOdds = inView.filter((r) => r.g[8]).length;

  $("asof").textContent = `Updated ${formatAsOf(data.asOf)}. Game times are Eastern.`;
  $("kept").replaceChildren(...leagues.filter((L) => data.L[L].keptFrom).map((L) => el("p", { class: "kept" }, `${L} is the last good slate, from ${formatAsOf(data.L[L].keptFrom)}, because this refresh did not produce a new one.`)));
  renderLeagueButtons(all);
  setSelect($("day"), [{ value: "all", label: "All 7 days" }].concat(days.map((d) => ({ value: d, label: dayLabel(d) }))), dayOk ? state.day : "all");

  $("metrics").replaceChildren(
    metric(String(inView.length), "Games in window", state.view === "ALL" ? "NHL, NBA and NFL" : state.view),
    metric(String(withOdds), "Games with a posted line", "No line means the number stays off"),
    metric(String(values.length), "Value picks", `Edge of ${state.thr || 0} points or more`),
    metric(pct(data.L[calL].bt.hit), `${calL} backtest hit rate`, `${data.L[calL].bt.n} games`),
  );

  const gate = $("value-gate");
  if (gate) gate.textContent = valueGateText();
  const nbaWrap = $("showNbaWrap");
  if (nbaWrap) {
    const nbaGate = data.L.NBA.valueGate;
    nbaWrap.hidden = !!(nbaGate && nbaGate.eligible);
  }
  renderParlays(inView, kFrac, bankroll);
  renderValue(values, kFrac, bankroll);
  renderGames(rows);
  renderDetail(all, rows);
  renderWeights();
  renderRecord(calL);
  saveSettings();
}

function valueGateText() {
  const bits = LGS.map((L) => {
    const g = state.data.L[L].valueGate;
    if (!g) return `${L}: not gated`;
    const units = typeof g.units === "number" ? `${g.units > 0 ? "+" : ""}${Number(g.units).toFixed(1)} units` : "units unavailable";
    let status = g.eligible ? "badges on" : "badges off";
    if (!g.eligible && L === "NBA" && state.showNba) status = "badges on because the toggle is checked";
    return `${L} ${g.n} flat bets, ${units}, ${status}`;
  });
  const minBets = (state.data.L.NHL.valueGate && state.data.L.NHL.valueGate.minBets) || 250;
  return `${bits.join(". ")}. A league's badges stay off unless it has at least ${minBets} out-of-sample flat bets and a positive unit total.`;
}

function metric(value, label, detail) {
  return el("div", { class: "metric" }, [el("b", {}, value), el("span", {}, label), el("small", {}, detail)]);
}

const parlaySeen = new Set();
const parlayOpen = new Set();

function todayEtKey() {
  const now = Date.now();
  const shifted = new Date(now - easternOffsetHours(now) * 3600 * 1000);
  return shifted.toISOString().slice(0, 10);
}

function parlayIsOpen(key, defaultOpen) {
  if (!parlaySeen.has(key)) {
    parlaySeen.add(key);
    if (defaultOpen) parlayOpen.add(key);
  }
  return parlayOpen.has(key);
}

function fold(key, summaryKids, bodyKids, defaultOpen, extraClass) {
  const node = document.createElement("details");
  node.className = extraClass ? `fold ${extraClass}` : "fold";
  node.open = parlayIsOpen(key, defaultOpen);
  node.addEventListener("toggle", () => {
    if (node.open) parlayOpen.add(key);
    else parlayOpen.delete(key);
  });
  const summary = document.createElement("summary");
  summary.append(el("span", { class: "fold-line" }, summaryKids));
  node.append(summary, el("div", { class: "fold-body" }, bodyKids));
  return node;
}

function parlayFigures(ls, kFrac, bankroll) {
  const d = prodD(ls);
  const p = prodP(ls);
  const ev = p * d - 1;
  const b = d - 1;
  const frac = b > 0 ? Math.max(0, (b * p - (1 - p)) / b) * kFrac * bankroll : 0;
  const am = americanFromDec(d);
  const pays = `$${(10 * d).toFixed(2)}${am == null ? "" : ` (${fmtML(am)})`}`;
  const stake = frac > 0 ? `${money(frac)} pays ${money(frac * d)}` : "No stake, negative EV";
  return { p, d, pays, row: [String(ls.length), pct(p), pct(1 / d), pays, toneEV(ev), stake] };
}

function renderParlays(inView, kFrac, bankroll) {
  const parlays = buildParlays(inView);
  const body = $("parlays-body");
  if (!parlays.length) {
    body.replaceChildren(el("p", { class: "empty" }, "No day in this view has enough priced games for a parlay."));
    return;
  }
  const today = todayEtKey();
  const blocks = parlays.map((d) => {
    const tickets = [];
    const names = [];
    [["Small", d.small, false], ["Big", d.big, false], ["Wild", d.wild, true]].forEach(([name, ls, wild]) => {
      if (!ls) return;
      names.push(name);
      const fig = parlayFigures(ls, kFrac, bankroll);
      const legRows = ls.map((x) => {
        const edge = typeof x.edge === "number" ? { text: `${sgn(x.edge * 100, 1)} pts`, cls: x.edge > 0 ? "pos" : x.edge < 0 ? "neg" : "" } : "unavailable";
        return [
          wild ? el("span", {}, [name, el("span", { class: "tag" }, "Long shot")]) : name,
          `${x.r.L} ${matchup(x.r)}, ${timeLabel(x.r.g[0])}`,
          x.pick,
          fmtML(x.ml),
          bookName(x.book),
          pct(x.p),
          edge,
        ];
      });
      const inside = [];
      if (wild) {
        inside.push(el("p", { class: "honest" }, `Wild is a long shot. ${pct(fig.p)} is the model's combined probability for these ${ls.length} legs, not a confidence costume. The ticket was chosen because it had the highest model expected value among 5–8 leg combinations that pay +1000 or longer.`));
      }
      inside.push(table(["Legs", "Model hit", "Book-implied", "Pays on $10", "Model EV", "Kelly stake"], [fig.row]));
      inside.push(table(["Parlay", "Leg (ET)", "Pick", "Best price", "Book", "Model", "Edge"], legRows));
      const title = wild
        ? el("span", { class: "fold-title" }, [name, el("span", { class: "tag" }, "Long shot")])
        : el("span", { class: "fold-title" }, name);
      tickets.push(fold(`ticket:${d.day}:${name}`, [
        title,
        el("span", { class: "fold-meta" }, `${ls.length} legs`),
        el("span", { class: "fold-meta" }, `${pct(fig.p)} model`),
        el("span", { class: "fold-meta" }, `${fig.pays.split(" (")[0]} on $10`),
      ], inside, false, `ticket ticket-${name.toLowerCase()}`));
    });
    const dayBody = [...tickets];
    if (!d.wild && d.n >= 5) {
      dayBody.push(el("p", { class: "note" }, "No Wild ticket today: no 5–8 leg combination of model sides reaches +1000 at the best posted prices."));
    }
    return fold(`day:${d.day}`, [
      el("span", { class: "fold-title" }, dayLabel(d.day)),
      el("span", { class: "fold-meta" }, `${d.n} priced games`),
      el("span", { class: "fold-meta" }, names.join(", ") || "No ticket"),
    ], dayBody, d.day === today);
  });
  body.replaceChildren(...blocks);
}

function renderValue(values, kFrac, bankroll) {
  const shown = values.slice(0, 8).map((r) => {
    const b = r.best;
    const ev = (b.p * dec(b.ml) - 1) * 100;
    const gap = Math.abs(b.edge) >= 0.12 ? " · check notes" : "";
    return [
      `${state.view === "ALL" ? r.L + "  " : ""}${matchup(r)}, ${dayLabel(dayKey(r.g[0]))} ${timeLabel(r.g[0])}`,
      `${sideAbbr(r, b.side)} moneyline`,
      pct(b.p),
      pct(b.side === "h" ? r.q : 1 - r.q),
      { text: `${sgn(b.edge * 100, 1)}${gap}`, cls: "badge badge-value" },
      `${fmtML(b.ml)} ${bookName(b.book)}`,
      money(ev),
      money(stakeOf(r, kFrac, bankroll)),
    ];
  });
  const gatedOff = viewLeagues().filter((L) => {
    const g = state.data.L[L].valueGate;
    return g && !allowsValue(L);
  });
  const names = gatedOff.length <= 1 ? gatedOff.join("") : gatedOff.length === 2 ? gatedOff.join(" and ") : `${gatedOff.slice(0, -1).join(", ")}, and ${gatedOff[gatedOff.length - 1]}`;
  const empty = gatedOff.length
    ? `${names} value cards stay off. A league needs at least 250 out-of-sample flat bets and a positive result. The line under this heading has the count.`
    : "No game in this view clears the value threshold.";
  $("value-body").replaceChildren(shown.length
    ? table(["Game (ET)", "Bet", "Model", "Market", "Edge", "Best price", "EV per $100", "Stake"], shown)
    : el("p", { class: "empty" }, empty));
}

function renderGames(rows) {
  const shown = state.showAll ? rows : rows.slice(0, ROW_CAP);
  const body = shown.map((r) => {
    const pickH = r.p >= 0.5;
    const pk = pickH ? r.g[1] : r.g[2];
    const pp = pickH ? r.p : 1 - r.p;
    const o = r.g[8];
    const mk = r.q == null ? "No line yet" : pct(pickH ? r.q : 1 - r.q);
    const edge = r.q == null ? "" : sgn(((pickH ? r.edgeH : r.edgeA)) * 100, 1);
    const edgeCell = isValue(r, readNumbers().threshold) && r.best
      ? { text: `${sideAbbr(r, r.best.side)} ${sgn(r.best.edge * 100, 1)}`, cls: "badge badge-value" }
      : edge;
    const tot = `${showNum(r.g[7], 1)}${o && o[5] != null ? ` vs ${o[5]}` : ""}`;
    const open = state.sel === r.id;
    const match = el("span", { class: "matchup" }, state.view === "ALL"
      ? [el("span", { class: `lg-badge lg-${r.L}` }, r.L), document.createTextNode(matchup(r))]
      : matchup(r));
    return [
      `${dayLabel(dayKey(r.g[0]))}, ${timeLabel(r.g[0])}`,
      match,
      `${pk} ${pct(pp)}`,
      mk,
      edgeCell,
      tot,
      el("button", { type: "button", class: open ? "primary" : "quiet", onclick: () => openDetail(r.id) }, open ? "Showing" : "Details"),
    ];
  });
  const kids = [table(["When (ET)", "Matchup", "Pick", "Market", "Edge", "Total: model vs line", ""], body, "No games match these filters.")];
  if (rows.length > ROW_CAP) {
    kids.push(el("button", {
      type: "button",
      class: "ghost",
      onclick: () => { state.showAll = !state.showAll; renderDynamic(); },
    }, state.showAll ? `Show first ${ROW_CAP} games` : `Show all ${rows.length} games`));
  }
  $("games-body").replaceChildren(...kids);
}

function openDetail(id) {
  state.sel = id;
  renderDynamic();
  const node = $("detail");
  if (node && node.scrollIntoView) node.scrollIntoView({ behavior: "smooth", block: "start" });
}

function statRow(label, away, home) {
  return [label, away, home];
}

function renderDetail(all, rows) {
  const current = all.find((r) => r.id === state.sel) || rows[0] || all[0];
  const host = $("detail-body");
  if (!current) {
    host.replaceChildren(el("p", { class: "empty" }, "No game is available to open."));
    return;
  }
  const options = [];
  const seen = new Set();
  [current, ...rows].forEach((r) => {
    if (seen.has(r.id)) return;
    seen.add(r.id);
    options.push(r);
  });
  const picker = el("label", {}, ["Game", el("select", { id: "gamepick" })]);
  const select = picker.querySelector("select");
  options.forEach((r) => select.append(el("option", { value: r.id }, `${dayLabel(dayKey(r.g[0]))} ${timeLabel(r.g[0])}  ${r.L} ${matchup(r)}`)));
  select.value = current.id;
  select.addEventListener("change", () => { state.sel = select.value; renderDynamic(); });

  const r = current;
  const L = r.L;
  const h = r.h;
  const a = r.a;
  const g = r.g;
  const o = g[8];
  const unit = UNIT[L];
  const dp = L === "NHL" ? 2 : 1;
  const hs = (g[7] + r.margin) / 2;
  const as = (g[7] - r.margin) / 2;
  const f = (v, d = 2) => showNum(v, d, true);
  const n = (v, d = 1) => showNum(v, d, false);
  const stat = [
    statRow("Record (home / road)", `${a[1] ?? "unavailable"} (${a[2] ?? "unavailable"} / ${a[3] ?? "unavailable"})`, `${h[1] ?? "unavailable"} (${h[2] ?? "unavailable"} / ${h[3] ?? "unavailable"})`),
    statRow(`Avg margin per game, ${unit}`, f(a[5]), f(h[5])),
    statRow("Schedule strength (opponents' avg margin)", f(a[6]), f(h[6])),
    statRow(`Last ${FORM_N[L]}: record, avg margin`, `${a[8] ?? "unavailable"}, ${f(a[7], 1)}`, `${h[8] ?? "unavailable"}, ${f(h[7], 1)}`),
    statRow("Road margin (away) / home margin (home)", f(a[10]), f(h[9])),
    statRow("Scored / allowed per game", `${n(a[11], dp)} / ${n(a[12], dp)}`, `${n(h[11], dp)} / ${n(h[12], dp)}`),
    statRow("Rest days before this game", g[4] == null ? "unavailable" : String(g[4]), g[3] == null ? "unavailable" : String(g[3])),
    statRow("Players out, model injury cost", `${a[13] == null ? "unavailable" : a[13]}, ${n(a[14], 2)}`, `${h[13] == null ? "unavailable" : h[13]}, ${n(h[14], 2)}`),
  ];
  const extra = (team) => (Array.isArray(team[17]) ? team[17] : []);
  if (L === "NHL") {
    const xa = extra(a); const xh = extra(h);
    stat.push(statRow("Expected goals for / against per game", `${n(xa[0])} / ${n(xa[1])}`, `${n(xh[0])} / ${n(xh[1])}`));
    stat.push(statRow("Expected goals share", xa[2] == null ? "unavailable" : `${n(xa[2])}%`, xh[2] == null ? "unavailable" : `${n(xh[2])}%`));
    stat.push(statRow("Corsi (shot attempt share)", xa[3] == null ? "unavailable" : `${n(xa[3])}%`, xh[3] == null ? "unavailable" : `${n(xh[3])}%`));
    stat.push(statRow("PDO (shooting % + save %)", n(xa[4]), n(xh[4])));
    stat.push(statRow("Power play / penalty kill", `${n(xa[5])}% / ${n(xa[6])}%`, `${n(xh[5])}% / ${n(xh[6])}%`));
    const gl = (G) => (Array.isArray(G) ? `${G[0]}, ${G[1] == null ? "no NHL games logged this season" : `${sgn(G[1])} GSAx per game`} (${String(G[2] || "").toLowerCase()})` : "unavailable");
    stat.push(statRow(state.data.L.NHL.model === "xg" ? "Goalie, shrunk GSAx per start" : "Goalie (DailyFaceoff)", gl(g[10]), gl(g[9])));
  } else if (L === "NBA") {
    const xa = extra(a); const xh = extra(h);
    stat.push(statRow("Offensive / defensive rating", `${n(xa[0])} / ${n(xa[1])}`, `${n(xh[0])} / ${n(xh[1])}`));
    stat.push(statRow("Net rating", f(xa[2], 1), f(xh[2], 1)));
    stat.push(statRow("Pace (possessions per 48)", n(xa[3]), n(xh[3])));
    stat.push(statRow("SRS (rating adjusted for schedule)", f(xa[4], 1), f(xh[4], 1)));
  } else {
    const xa = extra(a); const xh = extra(h);
    stat.push(statRow("Offense EPA per play", f(xa[0], 3), f(xh[0], 3)));
    stat.push(statRow("Defense EPA per play allowed", f(xa[1], 3), f(xh[1], 3)));
    stat.push(statRow("Success rate, offense / defense", `${n(xa[2])}% / ${n(xa[3])}%`, `${n(xh[2])}% / ${n(xh[3])}%`));
    const qb = (team) => (Array.isArray(team[18]) ? `${team[18][0]}, ${sgn(team[18][1], 3)}` : "unavailable");
    stat.push(statRow("Lead passer by dropbacks, EPA per dropback", qb(a), qb(h)));
  }

  const notes = [];
  if (L === "NHL" && (g[9] || g[10])) {
    const gsLabel = state.data.L.NHL.model === "xg" ? "shrunk GSAx/start" : "GSAx/game";
    const nm = (G, ab) => (Array.isArray(G) ? `${ab} ${G[0]} (${String(G[2] || "").toLowerCase()}${G[1] == null ? "" : `, ${sgn(G[1])} ${gsLabel}`})` : `${ab} not posted`);
    notes.push({ tx: `Goalies: ${nm(g[10], g[2])} vs ${nm(g[9], g[1])}.`, src: `https://www.dailyfaceoff.com/starting-goalies/${dayKey(g[0])}` });
  }
  const news = [];
  for (let i = 0; i < 2; i++) {
    if (h[16] && h[16][i]) news.push([g[1], h[16][i]]);
    if (a[16] && a[16][i]) news.push([g[2], a[16][i]]);
  }
  news.forEach((x) => { if (notes.length < 3) notes.push({ tx: `${x[0]}: ${x[1][0]}`, src: x[1][1] }); });

  const inj = [];
  [[g[2], a], [g[1], h]].forEach(([abbr, team]) => {
    (team[15] || []).forEach((p) => {
      if (!Array.isArray(p)) return;
      inj.push([p[0] || "unavailable", abbr, STATUS[p[1]] || p[1] || "unavailable", typeof p[2] === "number" && p[2] > 0 ? `-${Number(p[2]).toFixed(2)} ${unit}` : "Not in the margin"]);
    });
  });

  const props = el("dl", { class: "props" }, [
    prop("Start", `${dayLabel(dayKey(g[0]))}, ${timeLabel(g[0])} ET${g[12] === 1 ? ", neutral site" : ""}`),
    prop("Model", `${g[1]} ${pct(r.p)}, ${g[2]} ${pct(1 - r.p)}`),
    prop("Projected score", `${g[2]} ${showNum(as, 1)}, ${g[1]} ${showNum(hs, 1)} (margin ${sgn(r.margin, 1)} ${unit} to ${g[1]})`),
    prop("Market, no-vig", o ? `${g[1]} ${pct(o[0])}, ${g[2]} ${pct(1 - o[0])}; total ${o[5] ?? "unavailable"}, ${g[1]} spread ${o[6] == null ? "unavailable" : sgn(o[6], 1)}` : "No sportsbook line posted yet"),
    prop("Best prices", o ? `${g[1]} ${fmtML(o[1])} (${bookName(o[2])}), ${g[2]} ${fmtML(o[3])} (${bookName(o[4])})` : "unavailable"),
    prop("Head-to-head this season", g[11].length ? `${g[1]}: ${g[11].join("; ")}` : "No meetings yet"),
  ]);

  const kids = [
    el("h3", {}, `${a[0] || g[2]} at ${h[0] || g[1]}`),
    picker,
    props,
    el("div", { class: "split" }, [
      el("div", {}, [
        el("h3", {}, "What drives the pick"),
        el("div", { class: "legend" }, [el("span", {}, [el("i", { class: "swatch home" }), g[1]]), el("span", {}, [el("i", { class: "swatch away" }), g[2]])]),
        contribChart(r.contribs, g[1], g[2], unit),
      ]),
      el("div", {}, [
        el("h3", {}, "Stat comparison"),
        table(["Stat", g[2], g[1]], stat),
      ]),
    ]),
  ];
  if (notes.length) {
    kids.push(el("h3", {}, "Research notes"));
    notes.forEach((note) => {
      const bits = [note.tx + " "];
      if (typeof note.src === "string" && /^https?:\/\//.test(note.src)) {
        bits.push(el("a", { href: note.src, target: "_blank", rel: "noopener noreferrer" }, "Source"));
      } else bits.push("Source unavailable.");
      kids.push(el("p", { class: "note" }, bits));
    });
  }
  const lower = [];
  if (inj.length) lower.push(el("div", {}, [el("h3", {}, "Key absences"), table(["Player", "Team", "Status", "Model cost"], inj)]));
  if (o) {
    const books = (o[7] || []).map((b) => [bookName(b[0]), fmtML(b[2]), fmtML(b[1])]);
    lower.push(el("div", {}, [el("h3", {}, "Moneyline by book"), table(["Book", g[2], g[1]], books, "No per-book prices were posted.")]));
  }
  if (lower.length) kids.push(el("div", { class: "split" }, lower));
  host.replaceChildren(...kids);
}

function prop(label, value) {
  return el("div", {}, [el("dt", {}, label), el("dd", {}, value)]);
}

let builtTune = "";
function renderWeights() {
  const tune = $("tune");
  if (state.view === "ALL") {
    tune.hidden = false;
    tune.replaceChildren(...LGS.map((L) => el("button", {
      type: "button", class: `lg lg-${L}`, role: "radio", "aria-checked": state.tuneL === L ? "true" : "false",
      onclick: () => { state.tuneL = L; renderDynamic(); },
    }, L)));
  } else {
    tune.hidden = true;
    tune.replaceChildren();
  }
  const tL = state.view === "ALL" ? state.tuneL : state.view;
  const host = $("sliders");
  if (builtTune !== tL) {
    builtTune = tL;
    const comps = COMPS[tL].map((c) => slider(`${tL}.${c.key}`, c.label, 0, 2, 0.05, (v) => v.toFixed(2)));
    const adjs = ADJS.filter((a) => a.key !== "goalie" || tL === "NHL").map((a) => slider(`${tL}.${a.key}`, a.label, 0, 2, 0.05, (v) => `×${v.toFixed(2)}`));
    adjs.push(slider(`${tL}.shrink`, "Early-season trust in the stats", 0.1, 1, 0.05, (v) => `${Math.round(v * 100)}%`));
    host.replaceChildren(
      el("p", { class: "weight-label" }, `Strength factors for ${tL}`),
      el("div", { class: "slider-grid" }, comps),
      el("p", { class: "weight-label" }, "Adjustments, as multipliers on the measured effect"),
      el("div", { class: "slider-grid" }, adjs),
    );
  }
  host.querySelectorAll("input[type=range]").forEach((input) => {
    const key = input.dataset.weight;
    if (document.activeElement !== input) input.value = String(state.w[key]);
    const lab = [...host.querySelectorAll("[data-val]")].find((n) => n.getAttribute("data-val") === key);
    if (lab) lab.textContent = input.dataset.fmt === "pct" ? `${Math.round(state.w[key] * 100)}%` : input.dataset.fmt === "x" ? `×${Number(state.w[key]).toFixed(2)}` : Number(state.w[key]).toFixed(2);
  });
}

function slider(key, label, min, max, step, fmt) {
  const kind = key.endsWith(".shrink") ? "pct" : key.includes(".hfa") || key.includes(".rest") || key.includes(".inj") || key.includes(".goalie") ? "x" : "num";
  const input = el("input", { type: "range", min, max, step, value: state.w[key], "data-weight": key, "data-fmt": kind });
  input.addEventListener("input", () => {
    state.w[key] = parseFloat(input.value);
    renderDynamic();
  });
  return el("label", { class: "slider" }, [
    el("span", { class: "slider-top" }, [el("span", {}, label), el("b", { "data-val": key }, fmt(state.w[key]))]),
    input,
  ]);
}

function asRecord(b) {
  if (!b) return null;
  if (!Array.isArray(b)) return b;
  const mkt = typeof b[3] === "number" && typeof b[4] === "number" ? [b[0], b[3], b[4]] : null;
  const bets = typeof b[7] === "number" ? [b[5], b[6], b[7]] : null;
  return { n: b[0], hit: b[1], brier: b[2], mkt, bets };
}

function recordRow(L, label, b) {
  b = asRecord(b);
  if (!b) return null;
  const units = b.bets ? `${b.bets[2] > 0 ? "+" : ""}${fx(b.bets[2], 1)} units` : null;
  return [
    el("span", { class: `lg-badge lg-${L}` }, label),
    String(b.n),
    pct(b.hit),
    b.mkt ? pct(b.mkt[1]) : "unavailable",
    fx(b.brier, 3),
    b.mkt ? fx(b.mkt[2], 3) : "unavailable",
    b.bets ? `${b.bets[0]} bets, ${b.bets[1]} won` : "unavailable",
    units ? { text: units, cls: b.bets[2] > 0 ? "pos" : "neg" } : "unavailable",
  ];
}

function marketNote() {
  const comparable = LGS.filter((L) => state.data.L[L].bt && state.data.L[L].bt.mkt);
  if (comparable.length && comparable.every((L) => state.data.L[L].marketBetter)) {
    const who = state.data.L.NHL.bt && state.data.L.NHL.bt.before ? "the NHL after row, the NBA, and the NFL" : "every league";
    return `The market's Brier score is lower than the model's for ${who}. A lower Brier score means the probabilities were closer to the results. The market is still the more accurate number. Edges are a shortlist to research, not a signal on their own.`;
  }
  const bits = comparable.map((L) => {
    const b = state.data.L[L].bt;
    return `${L} model ${fx(b.brier, 3)} vs market ${fx(b.mkt[2], 3)}`;
  });
  return `Brier score, lower is more accurate: ${bits.join("; ") || "market comparison unavailable"}. Edges are a shortlist to research, not a signal on their own.`;
}

function recordNote() {
  const samples = LGS.map((L) => `${L}: ${state.data.L[L].sample || "this season"}`).join(". ");
  const nhl = state.data.L.NHL;
  const extra = nhl.model === "xg" && nhl.bt && nhl.bt.before
    ? " NHL before is the old results model on the same out-of-sample games. NHL after is the xG and goalie model. NBA and NFL formulas are unchanged."
    : " NBA and NFL formulas are unchanged.";
  return `${samples}.${extra} Calibration below is the favourite's predicted win chance for the league selected above.`;
}

function renderRecord(calL) {
  const rows = [];
  LGS.forEach((L) => {
    const lg = state.data.L[L];
    const b = lg.bt;
    if (L === "NHL" && b.before) {
      const before = recordRow(L, "NHL before", b.before);
      if (before) rows.push(before);
    }
    rows.push(recordRow(L, L === "NHL" && b.before ? "NHL after" : L, b));
  });
  const cal = state.data.L[calL].bt.calib;
  const kids = [
    table(["League", "Games", "Model hit", "Market hit", "Model Brier", "Market Brier", "Flat bets at 3+ pt edge", "Result"], rows),
    el("p", { class: "note" }, recordNote()),
    el("p", { class: "honest" }, marketNote()),
  ];
  if (cal.length) {
    const max = 100;
    const chart = el("div", {}, [
      el("div", { class: "legend" }, [el("span", {}, [el("i", { class: "swatch home" }), "Predicted"]), el("span", {}, [el("i", { class: "swatch away" }), "Actual"])]),
      ...cal.map((c) => {
        const label = c[0] >= 70 ? "70%+" : `${c[0]}–${c[0] + 5}%`;
        const pred = Math.max(0, Math.min(100, c[2] * 100));
        const act = Math.max(0, Math.min(100, c[3] * 100));
        return el("div", { class: "bar-row" }, [
          el("span", {}, `${label} · n=${c[1] ?? "?"}`),
          el("span", { class: "pair" }, [
            el("span", { class: "bars", style: "flex:1" }, [
              el("i", { class: "pred", style: `width:${pred / max * 50}%` }),
              el("i", { class: "act", style: `width:${act / max * 50}%` }),
            ]),
            el("span", {}, `${pred.toFixed(0)} / ${act.toFixed(0)}`),
          ]),
        ]);
      }),
    ]);
    kids.splice(1, 0, chart);
  }
  $("record-body").replaceChildren(...kids);
}

function renderHow() {
  const data = state.data;
  const weeks = data.L.NFL.pbpWeeks;
  const xg = data.L.NHL.model === "xg";
  const coef = data.L.NHL.coef || null;
  const coefLine = coef
    ? ` The NHL coefficients on the page were fit on ${coef.season || "2025-26"} (${coef.n} games): ${coef.bXg} goals per expected-goal point, ${coef.bGoalie} per GSAx point, home edge ${coef.hfa}. Recency half-life ${coef.halfLife} games, team shrinkage ${coef.kTeam}, goalie shrinkage ${coef.kGoalie}. Other NHL factors and rest start at weight 0 and stay adjustable. If MoneyPuck cannot be read, the page uses the older results model.`
    : "";
  $("how-body").replaceChildren(
    el("p", { class: "eq" }, "margin = s × (Σ weight × factor) / (Σ weight) + home + rest + injuries + goalie"),
    el("p", { class: "eq" }, "P(home win) = Φ(margin / σ)"),
    el("p", {}, `Each factor is a home-minus-away strength gap, in goals per game for the NHL and points per game for the NBA and NFL. s is the early-season trust setting. σ is ${data.L.NHL.sigma} goals in the NHL, ${data.L.NBA.sigma} points in the NBA, and ${data.L.NFL.sigma} points in the NFL. Home advantage is ${showNum(data.L.NHL.hfa, 2)} goals in the NHL, ${showNum(data.L.NBA.hfa, 2)} points in the NBA, ${showNum(data.L.NFL.hfa, 2)} points in the NFL.${coefLine}`),
    table(["Factor", "NHL", "NBA", "NFL"], [
      ["Season margin with schedule strength", xg ? "Available, weight 0" : "Yes", "Yes", "Yes"],
      ["Recent form", xg ? "Available, weight 0" : "Last 10", "Last 10", "Last 4"],
      ["Home / road splits, shrunk for a small sample", xg ? "Available, weight 0" : "Yes", "Yes", "Yes"],
      ["Advanced efficiency", xg ? "Recency-weighted xG, shrunk toward last season" : "xG and Corsi (MoneyPuck)", "SRS, net rating, pace (Basketball-Reference)", `EPA per play${weeks ? `, weeks 1–${weeks}` : ""} (nflverse)`],
      ["Injuries", xg ? "Available, weight 0" : "Points per game, shrunk", "BPM and minutes", "Starting quarterback only"],
      ["Goalie", xg ? "GSAx per start, shrunk toward last season" : "DailyFaceoff projection and GSAx", "—", "—"],
      ["Rest", xg ? "Available, weight 0" : "Back-to-back", "Back-to-back", "Rest-day gap, including byes"],
      ["Head-to-head", xg ? "Available, weight 0" : "Yes, shrunk", "Yes, shrunk", "Yes, shrunk"],
    ]),
    el("p", {}, "Not included: travel distance, referees, weather, and confirmed lineups beyond the injury report. NHL starters come from DailyFaceoff when that name is in the MoneyPuck goalie list, otherwise the goalie with the most starts. Games without a posted line show the model only."),
    el("p", { class: "honest" }, `These are model estimates, not guarantees. Bet only what you are comfortable losing. ${marketNote()}`),
    el("h3", {}, "Where the numbers come from"),
    el("ul", {}, [
      linkItem("ESPN public scoreboard and injury JSON", "https://site.api.espn.com/apis/site/v2/sports"),
      linkItem("Action Network v2 scoreboard (moneylines, books far from the median dropped)", "https://api.actionnetwork.com/web/v2/scoreboard/nhl"),
      linkItem("MoneyPuck expected goals and goalie GSAx", "https://moneypuck.com/data.htm"),
      linkItem("NHL official team summary (special teams and shots)", "https://api.nhle.com/stats/rest/en/team/summary"),
      linkItem("DailyFaceoff projected starting goalies", "https://www.dailyfaceoff.com/starting-goalies"),
      linkItem("Basketball-Reference team ratings and player BPM", "https://www.basketball-reference.com/leagues/NBA_2027.html"),
      linkItem("nflverse play-by-play EPA", "https://github.com/nflverse/nflverse-data"),
    ]),
  );
}

function linkItem(label, href) {
  return el("li", {}, el("a", { href, target: "_blank", rel: "noopener noreferrer" }, label));
}

function bind() {
  $("theme").addEventListener("click", () => {
    const explicit = document.documentElement.getAttribute("data-theme");
    const dark = explicit ? explicit === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("gp-theme", next); } catch { /* ignore */ }
    applyThemeLabel();
  });
  $("day").addEventListener("change", () => { state.day = $("day").value; renderDynamic(); });
  $("sort").addEventListener("change", () => { state.sort = $("sort").value; renderDynamic(); });
  ["thr", "bank", "kelly"].forEach((id) => {
    $(id).addEventListener("input", () => { state[id] = $(id).value; renderDynamic(); });
  });
  $("oddsOnly").addEventListener("change", () => { state.oddsOnly = $("oddsOnly").checked; renderDynamic(); });
  $("valueOnly").addEventListener("change", () => { state.valueOnly = $("valueOnly").checked; renderDynamic(); });
  const showNba = $("showNba");
  if (showNba) showNba.addEventListener("change", () => { state.showNba = showNba.checked; renderDynamic(); });
  $("reset").addEventListener("click", () => {
    state.w = defaults(state.data);
    builtTune = "";
    renderDynamic();
  });
  applyThemeLabel();
}

async function main() {
  bind();
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const data = sanitize(await res.json());
    state.data = data;
    const saved = loadSettings();
    const base = defaults(data);
    if (saved.v === 3 && saved.w && typeof saved.w === "object") {
      for (const k of Object.keys(base)) {
        const v = saved.w[k];
        if (typeof v === "number" && isFinite(v)) base[k] = v;
      }
    }
    state.w = base;
    if (data.L.NHL.model === "xg") {
      const xg = COMPS.NHL.find((c) => c.key === "xg");
      if (xg) xg.label = "Recency-weighted xG, shrunk toward last season";
      const goalie = ADJS.find((a) => a.key === "goalie");
      if (goalie) goalie.label = "Starting goalie GSAx, shrunk";
    }
    if (saved.v === 3 && typeof saved.showNba === "boolean") {
      state.showNba = saved.showNba;
      const box = $("showNba");
      if (box) box.checked = state.showNba;
    }
    if (typeof saved.thr === "string" || typeof saved.thr === "number") state.thr = String(saved.thr);
    if (typeof saved.bank === "string" || typeof saved.bank === "number") state.bank = String(saved.bank);
    if (typeof saved.kelly === "string" || typeof saved.kelly === "number") state.kelly = String(saved.kelly);
    $("thr").value = state.thr;
    $("bank").value = state.bank;
    $("kelly").value = state.kelly;
    renderSources(data);
    renderHow();
    renderDynamic();
  } catch (err) {
    $("asof").textContent = "The slate could not be loaded. data.json is missing or unreadable. Nothing on this page is filled in with a guess.";
    console.error(err);
  }
}

main();
