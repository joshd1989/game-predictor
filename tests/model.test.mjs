import test from "node:test";
import assert from "node:assert/strict";
import {
  sanitize, pickWild, dayKey, timeLabel, ts, prodD, dec,
} from "../site/model.js";

test("manual Eastern clock, including daylight time", () => {
  assert.equal(dayKey("2026-11-11T00:00Z"), "2026-11-10");
  assert.equal(timeLabel("2026-11-11T00:00Z"), "7:00 PM");
  assert.equal(dayKey("2026-07-11T00:00Z"), "2026-07-10");
  assert.equal(timeLabel("2026-07-11T00:00Z"), "8:00 PM");
  assert.equal(dayKey("2026-03-08T06:30Z"), "2026-03-08");
  assert.equal(timeLabel("2026-03-08T06:30Z"), "1:30 AM");
  assert.equal(timeLabel("2026-03-08T07:30Z"), "3:30 AM");
  assert.equal(timeLabel("2026-11-01T05:30Z"), "1:30 AM");
  assert.equal(timeLabel("2026-11-01T06:30Z"), "1:30 AM");
  assert.ok(Number.isNaN(ts("not-a-date")));
});

test("sanitize drops a bad date and an unknown team", () => {
  const data = {
    asOf: "2026-11-10 09:07",
    L: {
      NHL: {
        teams: { TOR: ["Toronto Maple Leafs"], COL: ["Colorado Avalanche"] },
        games: [
          ["nope", "TOR", "COL"],
          ["2026-11-11T00:00Z", "ZZZ", "COL"],
          ["2026-11-11T00:00Z", "TOR", "COL", 1, 2, [1], null, null, [1.2, 100, "DK", -120, "FD"], null, null, "bad-h2h"],
        ],
        bt: { calib: [[50, 4, "bad", 0.4], [50, 4, 0.52, 0.5]] },
      },
    },
  };
  sanitize(data);
  assert.equal(data.L.NHL.games.length, 1);
  assert.equal(data.L.NHL.games[0][8], null);
  assert.deepEqual(data.L.NHL.games[0][11], []);
  assert.ok(Array.isArray(data.L.NHL.teams.TOR[15]));
  assert.equal(data.L.NHL.bt.calib.length, 1);
  assert.equal(data.L.NBA.games.length, 0);
  assert.equal(data.L.NFL.sigma, 13);
});

function leg(pick, p, ml, edge) {
  return { pick, p, ml, edge, iso: "2026-11-11T00:00Z" };
}

test("wild maximizes model EV and stays at +1000 or longer", () => {
  const dogs = ["A", "B", "C", "D", "E"].map((pick, i) => leg(pick, 0.5, 300, 0.05 - i * 0.01));
  const chalk = leg("F", 0.5, -200, 0.2);
  const wild = pickWild([...dogs, chalk]);
  assert.deepEqual(wild.map((x) => x.pick), ["A", "B", "C", "D", "E"]);
  assert.ok(prodD(wild) >= 11);
  assert.equal(wild.length >= 5 && wild.length <= 8, true);
});

test("wild is omitted when the prices cannot reach +1000", () => {
  const short = Array.from({ length: 8 }, (_, i) => leg("T" + i, 0.7, -500, 0.01));
  assert.equal(pickWild(short), null);
  assert.equal(pickWild([leg("A", 0.6, 150, 0.1), leg("B", 0.6, 150, 0.1)]), null);
});

test("an EV tie goes to the lexicographically greater set of picks", () => {
  // p * decimal = 0.8, so a sixth leg lowers EV and the five-leg sets tie.
  const legs = ["A", "B", "C", "D", "E", "F"].map((pick) => leg(pick, 0.4, 100, 0.02));
  const wild = pickWild(legs);
  assert.deepEqual(wild.map((x) => x.pick), ["B", "C", "D", "E", "F"]);
});

test("positive-edge legs are kept when the slate is trimmed", () => {
  const good = ["A", "B", "C", "D", "E"].map((pick) => leg(pick, 0.5, 300, 0.04));
  const junk = Array.from({ length: 20 }, (_, i) => leg("Z" + String(i).padStart(2, "0"), 0.25, -200, -0.05));
  const wild = pickWild([...junk, ...good]);
  assert.deepEqual(wild.map((x) => x.pick).sort(), ["A", "B", "C", "D", "E"]);
});

function brute(legs) {
  let best = null;
  const n = legs.length;
  const walk = (start, chosen) => {
    if (chosen.length > 8) return;
    if (chosen.length >= 5) {
      const ordered = [...chosen].sort((a, b) => (a.pick < b.pick ? -1 : 1));
      const p = ordered.reduce((a, x) => a * x.p, 1);
      const d = ordered.reduce((a, x) => a * dec(x.ml), 1);
      if (d >= 11) {
        const edges = ordered.filter((x) => x.edge > 0).length;
        const key = [Math.round((p * d - 1) * 1e6) / 1e6, edges, Math.round(d * 1e6) / 1e6, ordered.map((x) => x.pick).join("|")];
        const wins = !best || key.some((v, i) => v !== best.key[i] && v > best.key[i] && key.slice(0, i).every((x, j) => x === best.key[j]));
        if (wins) best = { key, picks: ordered.map((x) => x.pick) };
      }
    }
    for (let i = start; i < n && chosen.length < 8; i++) walk(i + 1, chosen.concat(legs[i]));
  };
  walk(0, []);
  return best && best.picks;
}

test("pickWild matches an independent search on a 9-leg slate", () => {
  const legs = [
    leg("BOS", 0.62, -150, 0.03),
    leg("EDM", 0.58, 120, 0.06),
    leg("COL", 0.71, -180, -0.02),
    leg("DAL", 0.54, 140, 0.05),
    leg("MIN", 0.66, -130, 0.01),
    leg("CAR", 0.6, 110, 0.04),
    leg("NJD", 0.51, 180, 0.02),
    leg("TBL", 0.69, -160, 0),
    leg("WPG", 0.48, -110, -0.04),
  ];
  // WPG is not a model side in the real builder (p < 0.5 is still a leg if we pass it in).
  // The search itself does not require p >= 0.5; the site only hands it model sides.
  const wild = pickWild(legs).map((x) => x.pick).sort();
  assert.deepEqual(wild, brute(legs).slice().sort());
  assert.ok(prodD(pickWild(legs)) >= 11);
});
