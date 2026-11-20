"use strict";
// Texte: L("deutsch", "english"), t() für feste Texte, tr() für Texte vom Bot – siehe i18n.js
// Token-Namen kommen von außen -> immer escapen
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);
const sol = (x, d = 4) => (x == null || isNaN(x) ? "–" : Number(x).toFixed(d));
const pct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%");
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const span = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1) + " h";
  return (s / 86400).toFixed(1) + L(" T", " d");
};
const ago = (ts) => L("vor " + span(ts), span(ts) + " ago");
const dt = (ts) => new Date(ts * 1000).toLocaleString(LOCALE(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const dur = (a, b) => { const m = (b - a) / 60; return m < 60 ? Math.round(m) + " min" : (m / 60).toFixed(1) + " h"; };
const fmtMC = (x) => {
  if (x == null || !isFinite(x)) return "–";
  const a = Math.abs(x);
  return "$" + (a >= 1e9 ? (x / 1e9).toFixed(2) + "B" : a >= 1e6 ? (x / 1e6).toFixed(2) + "M" : a >= 1e3 ? (x / 1e3).toFixed(1) + "k" : x.toFixed(0));
};
const statusName = (s) => (STATUS[LANG] || STATUS.de)[s] || s;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

let tab = "overview", hours = 168, lastSummary = null;
let wsLive = false, ws = null;

// ---------------------------------------------------------------- Tabs & Bedienung
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  tab = b.dataset.tab;
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === tab));
  refresh();
});
document.querySelectorAll("#rangeSeg button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#rangeSeg button").forEach((x) => x.classList.toggle("active", x === b));
  hours = +b.dataset.h; loadEquity(true);
});
$("agentFilter").onchange = () => loadAgents();
const closeModal = () => { $("modal").classList.add("hidden"); if (tv.chart) { tv.chart.remove(); tv.chart = null; } };
$("modalClose").onclick = closeModal;
$("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
document.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
  const act = b.dataset.act;
  if (act === "kill" && !confirm(L("Wirklich alle Positionen verkaufen und den Bot stoppen?",
    "Really sell all positions and stop the bot?"))) return;
  await api("/api/control/" + act, { method: "POST" });
  refresh();
});
$("langSelect").value = LANG;
$("langSelect").onchange = () => {
  setLang($("langSelect").value);
  applyStaticLang();
  settingsLoaded = false;
  aiLoaded = false;
  refresh();
};
applyStaticLang();

// ---------------------------------------------------------------- Übersicht
function kpi(label, value, sub = "", c = "") {
  return `<div class="kpi"><div class="l">${label}</div><div class="v ${c}">${value}</div><div class="s">${sub}</div></div>`;
}

async function loadSummary() {
  renderSummary(await api("/api/summary"));
}

function renderSummary(s) {
  lastSummary = s;
  const stale = s.last_cycle && s.now - s.last_cycle > 120;
  $("statusDot").className = "dot " + (s.killed ? "bad" : s.paused || stale ? "warn" : "ok");
  $("modeBadge").textContent = s.sim ? "SIMULATION" : s.mode.toUpperCase();
  $("modeBadge").className = "badge " + s.mode;
  const st0 = LANG === "en" ? (BOT_STATUS_EN[s.status] ?? s.status) : s.status;
  $("statusLine").textContent = (s.killed ? L("NOT-AUS aktiv · ", "KILL SWITCH active · ") : s.paused ? L("pausiert · ", "paused · ") : "") +
    `${st0} (${L("aktiv", "active")} ${s.last_cycle ? ago(s.last_cycle) : "–"}) · Watchlist ${s.watchlist} Coins` +
    (s.watch_oldest_s != null ? L(`, max. ${Math.round(s.watch_oldest_s)} s alt`, `, max. ${Math.round(s.watch_oldest_s)} s old`) : "") +
    (wsLive ? " · ⚡ Live" : "");
  const st = s.stats;
  $("kpis").innerHTML =
    kpi(L("Kapital", "Capital"), sol(s.equity_sol, 3) + " SOL", L("Kasse ", "Cash ") + sol(s.cash_sol, 3)) +
    kpi(L("Rendite gesamt", "Total return"), pct(s.total_return_pct), "Start " + sol(s.start_sol, 3) + " SOL", cls(s.total_return_pct)) +
    kpi(L("Offen", "Open"), s.open_count + L(" Pos.", " pos."), L("unrealisiert ", "unrealized ") + sol(s.unrealized_sol), cls(s.unrealized_sol)) +
    kpi(L("Letzte 24 h", "Last 24 h"), sol(s.last24h.pnl_sol) + " SOL", s.last24h.anzahl + " Trades", cls(s.last24h.pnl_sol)) +
    kpi("Trades", st.anzahl, st.gewinner + L(" Gewinner", " winners")) +
    kpi(L("Trefferquote", "Win rate"), st.anzahl ? st.trefferquote + "%" : "–", L("Ø ", "avg ") + pct(st.avg_pct)) +
    kpi(L("Profit-Faktor", "Profit factor"), st.profit_faktor ?? "–", L("Gewinne / Verluste", "wins / losses"),
      st.profit_faktor > 1 ? "pos" : st.profit_faktor ? "neg" : "") +
    kpi("Max. Drawdown", st.max_drawdown_pct != null ? st.max_drawdown_pct + "%" : "–", L("vom Höchststand", "from peak"));
}

let eqRows = [], eqLoaded = 0;
async function loadEquity(force) {
  // Verlauf alle 30 s neu laden, dazwischen nur den aktuellen Live-Punkt anhängen
  if (force || Date.now() - eqLoaded > 30000) {
    eqRows = await api("/api/equity?hours=" + hours);
    eqLoaded = Date.now();
  }
  const pts = eqRows.map((r) => [r.ts, r.equity_sol]);
  if (lastSummary) pts.push([lastSummary.now, lastSummary.equity_sol]);
  drawChart($("equityChart"), pts, lastSummary?.start_sol);
}

function drawChart(cv, pts, baseline) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 200;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  g.font = "11px sans-serif"; g.fillStyle = "#8b949e";
  if (pts.length < 2) {
    g.fillText(L("Noch keine Daten – der Verlauf erscheint nach ein paar Minuten.", "No data yet – the curve appears after a few minutes."), 8, 20);
    return;
  }
  const pad = { l: 46, r: 8, t: 8, b: 20 };
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  if (baseline) ys.push(baseline);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 1e-6) { y0 -= 0.01; y1 += 0.01; }
  const m = (y1 - y0) * 0.08; y0 -= m; y1 += m;
  const X = (t) => pad.l + (t - xs[0]) / (xs[xs.length - 1] - xs[0] || 1) * (w - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - y0) / (y1 - y0)) * (h - pad.t - pad.b);
  g.strokeStyle = "#262d36"; g.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) * i / 3, y = Y(v);
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(w - pad.r, y); g.stroke();
    g.fillText(v.toFixed(3), 2, y + 4);
  }
  g.fillText(dt(xs[0]), pad.l, h - 4);
  const end = dt(xs[xs.length - 1]); g.fillText(end, w - pad.r - g.measureText(end).width, h - 4);
  if (baseline) {
    g.setLineDash([4, 4]); g.strokeStyle = "#8b949e";
    g.beginPath(); g.moveTo(pad.l, Y(baseline)); g.lineTo(w - pad.r, Y(baseline)); g.stroke(); g.setLineDash([]);
  }
  const up = pts[pts.length - 1][1] >= (baseline ?? pts[0][1]);
  g.strokeStyle = up ? "#3fb950" : "#f85149"; g.lineWidth = 2;
  g.beginPath();
  pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
  g.stroke();
}

function srcBadge(pr) {
  if (!pr) return "";
  return pr.source === "helius" ? `<span class="pos">⚡ live</span>` : `<span title="${esc(tr(pr.note || ""))}">DexScreener</span>`;
}

function posRow(p) {
  const open = p.status === "open";
  const v = open ? p.unrealized_sol : p.pnl_sol, vp = open ? p.unrealized_pct : p.pnl_pct;
  const rest = p.tokens_total ? Math.round(p.tokens_remaining / p.tokens_total * 100) : 0;
  const meta = open
    ? `${srcBadge(p.price)} · ${L("seit", "for")} ${span(p.opened_at)} · ${L("Einsatz", "Stake")} ${sol(p.size_sol)} · Score ${Math.round(p.score)}` +
      (p.tp_hits ? L(` · TP ${p.tp_hits} verkauft: ${sol(p.sol_received)} SOL gesichert, Rest ${rest}%`,
                     ` · TP ${p.tp_hits} sold: ${sol(p.sol_received)} SOL secured, ${rest}% left`) : "")
    : `${dt(p.closed_at)} · ${esc(tr(p.exit_reason))} · ${dur(p.opened_at, p.closed_at)} · ${L("Einsatz", "Stake")} ${sol(p.size_sol)}`;
  const mcLine = p.entry_mcap ? `<span class="mc">MC ${fmtMC(p.entry_mcap)} → ${fmtMC(p.last_mcap)}</span>` : "";
  return `<div class="row" data-id="${p.id}">
    <div class="sym">${esc(p.symbol || p.mint.slice(0, 6))} ${mcLine}</div>
    <div class="pnl ${cls(v)}">${sol(v)} SOL</div>
    ${open && p.spark && p.spark.length > 1 ? `<canvas class="spark" data-spark='${JSON.stringify(p.spark)}' data-entry="${p.entry_mcap || ""}"></canvas>` : ""}
    <div class="meta">${meta}</div>
    <div class="meta pnl ${cls(vp)}">${pct(vp)}</div></div>`;
}

function bindRows(el) {
  el.querySelectorAll(".row").forEach((r) => r.onclick = () => showPosition(r.dataset.id));
  el.querySelectorAll("canvas.spark").forEach(drawSpark);
}

// Mini-Linie seit Einstieg; gestrichelt = Einstieg
function drawSpark(cv) {
  const pts = JSON.parse(cv.dataset.spark || "[]"), entry = +cv.dataset.entry || null;
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const ys = pts.map((p) => p[1]).concat(entry ? [entry] : []);
  const y0 = Math.min(...ys), y1 = Math.max(...ys), t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const X = (t) => 2 + (t - t0) / (t1 - t0 || 1) * (w - 4), Y = (v) => 2 + (1 - (v - y0) / (y1 - y0 || 1)) * (h - 4);
  if (entry) { g.setLineDash([3, 3]); g.strokeStyle = "#8b949e"; g.beginPath(); g.moveTo(0, Y(entry)); g.lineTo(w, Y(entry)); g.stroke(); g.setLineDash([]); }
  const last = pts[pts.length - 1][1];
  g.strokeStyle = !entry || last >= entry ? "#3fb950" : "#f85149"; g.lineWidth = 1.5;
  g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1])))); g.stroke();
}

async function loadOpen() {
  renderOpen(await api("/api/positions?state=open"));
}

function renderOpen(rows) {
  $("openList").innerHTML = rows.length ? rows.map(posRow).join("")
    : `<div class="empty">${L("Keine offenen Positionen.", "No open positions.")}</div>`;
  bindRows($("openList"));
}

// ---------------------------------------------------------------- Trades
async function loadTrades() {
  const rows = await api("/api/positions?state=closed&limit=500");
  $("tradeList").innerHTML = rows.length ? rows.map(posRow).join("")
    : `<div class="empty">${L("Noch keine abgeschlossenen Trades.", "No closed trades yet.")}</div>`;
  bindRows($("tradeList"));
}

const PARTS = ["kaufdruck", "volumen_beschleunigung", "momentum", "trend", "beteiligung", "liquiditaet"];
function scoreBlock(sd) {
  const name = (k) => (LANG === "en" ? PART_EN[k] : k.replaceAll("_", " "));
  const num = (x) => (x || 0).toLocaleString(LOCALE());
  const bars = PARTS.filter((k) => k in sd).map((k) =>
    `<div>${name(k)}</div><div>${sd[k]}<div class="bar"><i style="width:${sd[k]}%"></i></div></div>`).join("");
  return `<div class="kv">${bars}
      <div>${L("Kauf/Verkauf 5m", "Buys/sells 5m")}</div><div>${sd.kauf_verkauf ?? "–"}</div>
      <div>${L("Volumen-Beschl.", "Volume accel.")}</div><div>${sd.vol_beschl ?? "–"}×</div>
      <div>${L("Kurs 5m / 1h", "Price 5m / 1h")}</div><div>${pct(sd.chg_m5)} / ${pct(sd.chg_h1)}</div>
      <div>${L("Liquidität / FDV", "Liquidity / FDV")}</div><div>$${num(sd.liq_usd)} / $${num(sd.fdv_usd)}</div>
      <div>${L("Alter", "Age")}</div><div>${sd.alter_min ?? "–"} min</div>
      <div>${L("Abstand vom Hoch", "Distance from high")}</div><div>${sd.vom_hoch_pct != null
        ? pct(sd.vom_hoch_pct) + L(" (Hoch vor " + sd.std_seit_hoch + " h)", " (high " + sd.std_seit_hoch + " h ago)") : "–"}</div>
    </div>`;
}

// ---------------------------------------------------------------- Trade-Chart (TradingView Lightweight Charts)
const tv = { chart: null, series: null, pid: null, last: null, isMcap: true };
const TZ = -new Date().getTimezoneOffset() * 60;            // Chart zeigt Ortszeit
const LEVEL_COLOR = { sl: "#f85149", be: "#d29922", tp: "#3fb950", trail: "#7c8cff" };

async function renderTradeChart(p) {
  const el = $("tvChart");
  if (!el || !window.LightweightCharts) return;
  let d;
  try { d = await api(`/api/positions/${p.id}/chart`); } catch (e) { el.innerHTML = `<p class="muted">${L("Chart nicht verfügbar", "Chart unavailable")}</p>`; return; }
  if (!d.candles.length) { el.innerHTML = `<p class="muted">${L("Noch keine Kursdaten für den Chart.", "No price data for the chart yet.")}</p>`; return; }
  const LW = window.LightweightCharts;
  const chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#8b949e", fontSize: 11 },
    grid: { vertLines: { color: "#1d232b" }, horzLines: { color: "#1d232b" } },
    rightPriceScale: { borderColor: "#262d36" },
    timeScale: { borderColor: "#262d36", timeVisible: true, secondsVisible: false },
    localization: { priceFormatter: d.is_mcap ? fmtMC : (x) => x.toExponential(2) },
    crosshair: { mode: 0 },
  });
  const series = chart.addSeries(LW.CandlestickSeries, {
    upColor: "#3fb950", downColor: "#f85149", borderVisible: false, wickUpColor: "#3fb950", wickDownColor: "#f85149",
    priceFormat: { type: "custom", formatter: d.is_mcap ? fmtMC : (x) => x.toExponential(2), minMove: 1e-12 },
  });
  series.setData(d.candles.map((c) => ({ ...c, time: c.time + TZ })));
  const legend = [];
  if (d.entry_mcap) {
    series.createPriceLine({ price: d.entry_mcap, color: "#c9d1d9", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: L("Einstieg", "Entry") });
    legend.push(`<span><i style="border-color:#c9d1d9"></i>${L("Einstieg", "Entry")} ${fmtMC(d.entry_mcap)}</span>`);
  }
  for (const lv of d.levels) {
    series.createPriceLine({ price: lv.value, color: LEVEL_COLOR[lv.kind], lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: lv.label });
    legend.push(`<span><i style="border-color:${LEVEL_COLOR[lv.kind]}"></i>${esc(lv.label)} ${fmtMC(lv.value)}</span>`);
  }
  for (const f of d.fills.filter((f) => f.side === "sell")) {
    series.createPriceLine({ price: f.mcap, color: "#e3b341", lineWidth: 1, lineStyle: 0, axisLabelVisible: false, title: tr(f.reason) });
  }
  const markers = d.fills.map((f) => ({
    time: Math.floor(f.time / 60) * 60 + TZ, position: f.side === "buy" ? "belowBar" : "aboveBar",
    color: f.side === "buy" ? "#7c8cff" : "#e3b341", shape: f.side === "buy" ? "arrowUp" : "arrowDown",
    text: f.side === "buy" ? L("Kauf", "Buy") : tr(f.reason),
  }));
  if (markers.length) LW.createSeriesMarkers(series, markers);
  legend.push(`<span>${L("Quelle", "Source")}: ${d.source === "geckoterminal" ? "GeckoTerminal + live" : L("eigene Kursaufzeichnung", "own price recording")}</span>`);
  $("tvLegend").innerHTML = legend.join("");
  chart.timeScale().fitContent();
  Object.assign(tv, { chart, series, pid: p.id, last: { ...d.candles[d.candles.length - 1], time: d.candles[d.candles.length - 1].time + TZ },
    isMcap: d.is_mcap, open: d.status === "open" });
}

// Echtzeit: letzten Kurs aus dem Live-Push in die laufende Minute einarbeiten
function updateTradeChart(openRows) {
  if (!tv.chart || !tv.open) return;
  const p = openRows.find((x) => x.id === tv.pid);
  if (!p) return;
  const v = tv.isMcap ? p.last_mcap : p.last_price;
  if (!v) return;
  const t = Math.floor(Date.now() / 60000) * 60 + TZ;
  if (tv.last && tv.last.time === t) {
    tv.last = { ...tv.last, high: Math.max(tv.last.high, v), low: Math.min(tv.last.low, v), close: v };
  } else if (!tv.last || t > tv.last.time) {
    tv.last = { time: t, open: tv.last ? tv.last.close : v, high: v, low: v, close: v };
  }
  tv.series.update(tv.last);
}

function aiBlock(sd) {
  if (sd.ki_score == null) return "";
  const flags = (sd.ki_red_flags || []).map((f) => `<li>${esc(f)}</li>`).join("");
  return `<h2>${L("KI-Analyst", "AI analyst")} <span class="muted">(${sd.ki_modus === "filter" ? L("Filter", "filter") : L("nur beobachten", "observe only")})</span></h2>
    <div class="kv">
      <div>${L("KI-Score", "AI score")}</div><div>${sd.ki_score}<div class="bar"><i style="width:${sd.ki_score}%"></i></div></div>
      <div>${L("Urteil", "Verdict")}</div><div>${esc(L(sd.ki_urteil_de, sd.ki_urteil_en || sd.ki_urteil_de))}</div>
    </div>${flags ? `<p class="hint">${L("Warnsignale", "Red flags")}:</p><ul>${flags}</ul>` : ""}`;
}

async function showPosition(id) {
  const p = await api("/api/positions/" + id);
  const open = p.status === "open";
  const sd = p.score_details || {}, sec = p.security || {};
  const entry = +p.entry_price;
  const vsEntry = (x) => (x && entry ? pct((x / entry - 1) * 100) : "–");
  const fmtP = (x) => (x ? (+x).toExponential(3) : "–");
  const tm = (ts) => new Date(ts * 1000).toLocaleTimeString(LOCALE());
  const fills = p.fills.map((f) => {
    const buy = f.side === "buy";
    const tx = f.signature && f.signature !== "paper" ? ` · <a href="https://solscan.io/tx/${esc(f.signature)}" target="_blank">Tx</a>` : "";
    return `<tr><td>${tm(f.ts)}<br><span class="muted">${buy ? L("Kauf", "Buy") : esc(tr(f.reason))}${tx}</span></td>
    <td class="num">${sol(f.sol_amount)}</td>
    <td class="num">${f.mcap_usd ? fmtMC(f.mcap_usd) : fmtP(f.price)}<br><span class="muted">${f.mcap_usd ? fmtP(f.price) + " SOL" : L("Markt ", "Market ") + fmtP(f.market_price)}</span></td>
    <td class="num"><span class="${buy ? "" : cls(f.price / entry - 1)}">${buy ? L("Einstieg", "Entry") : vsEntry(f.price)}</span><br>
      <span class="muted">${buy ? "" : L("Ausl. ", "Trig. ") + vsEntry(f.market_price)}</span></td></tr>`;
  }).join("");
  const authority = (x) => (x ? L("aktiv", "active") : L("entzogen", "revoked"));
  $("modalContent").innerHTML = `
    <h2>${esc(p.symbol)} <span class="muted">${esc(p.name)}</span></h2>
    <div id="tvChart"></div><div class="legend" id="tvLegend"></div>
    <div class="kv">
      <div>Status</div><div>${open ? L("offen", "open") : L("geschlossen – ", "closed – ") + esc(tr(p.exit_reason))}</div>
      <div>${L("Ergebnis", "Result")}</div><div class="${cls(open ? p.unrealized_sol : p.pnl_sol)}">${sol(open ? p.unrealized_sol : p.pnl_sol)} SOL (${pct(open ? p.unrealized_pct : p.pnl_pct)})</div>
      <div>${L("Einsatz", "Stake")}</div><div>${sol(p.size_sol)} SOL</div>
      <div>${L("Zurück erhalten", "Received back")}</div><div>${sol(p.sol_received)} SOL${open && p.tp_hits ? L(" (aus Teilverkäufen)", " (from partial sells)") : ""}</div>
      ${open ? `<div>${L("Noch gehalten", "Still held")}</div><div>${Math.round(p.tokens_remaining / p.tokens_total * 100)}% ${L("der Token, Wert", "of tokens, value")} ${sol(p.value_sol)} SOL</div>` : ""}
      <div>${L("Marktkap. Einstieg / Hoch / Letzter", "Market cap entry / high / last")}</div><div>${p.entry_mcap
        ? `${fmtMC(p.entry_mcap)} / ${fmtMC(p.high_mcap)} / ${fmtMC(p.last_mcap)}`
        : `${(+p.entry_price).toExponential(3)} / ${(+p.high_price).toExponential(3)} / ${(+p.last_price).toExponential(3)} SOL`}</div>
      <div>${L("Eröffnet", "Opened")}</div><div>${dt(p.opened_at)}${p.closed_at ? L(" · Dauer ", " · duration ") + dur(p.opened_at, p.closed_at) : ""}</div>
      <div>Score</div><div>${Math.round(p.score)}</div>
      <div>Mint</div><div style="word-break:break-all">${esc(p.mint)}</div>
      <div>Links</div><div><a href="https://dexscreener.com/solana/${esc(p.pair_address || p.mint)}" target="_blank">DexScreener</a> ·
        <a href="https://rugcheck.xyz/tokens/${esc(p.mint)}" target="_blank">RugCheck</a></div>
    </div>
    <h2>${L("Warum gekauft (Stratege)", "Why it was bought (strategist)")}</h2>
    ${scoreBlock(sd)}
    ${aiBlock(sd)}
    <h2>${L("Sicherheit (Rug-Analyst)", "Security (rug analyst)")}</h2>
    <div class="kv">
      <div>${L("RugCheck-Score", "RugCheck score")}</div><div>${sec.score ?? "–"}</div>
      <div>${L("LP gelockt", "LP locked")}</div><div>${sec.lp_locked_pct ?? "–"}%</div>
      <div>${L("Top-10-Holder", "Top 10 holders")}</div><div>${sec.top10_pct ?? "–"}%</div>
      <div>Mint/Freeze</div><div>${authority(sec.mint_authority)} / ${authority(sec.freeze_authority)}</div>
    </div>
    <h2>${L("Ausführungen", "Executions")}</h2>
    <p class="hint">${L(`Marktkapitalisierung zum tatsächlich erzielten Preis (inkl. Preiseinfluss, Gebühren, Sicherheitsabschlag), darunter der Preis in SOL pro Token.
      „Ausl.“ (Auslöser) = Abstand des Marktkurses zum Einstieg im Moment der Entscheidung.`,
      `Market cap at the price actually achieved (incl. price impact, fees, safety discount), below it the price in SOL per token.
      "Trig." (trigger) = distance of the market price from the entry at the moment of the decision.`)}</p>
    <div class="scroll"><table class="fills"><tr><th>${L("Zeit / Grund", "Time / reason")}</th><th class="num">SOL</th>
      <th class="num">${L("Marktkap.", "Mkt cap")}</th><th class="num">± Entry</th></tr>${fills}</table></div>
    ${open ? `<p><button class="btn small danger" id="closeBtn">${L("Position jetzt verkaufen", "Sell position now")}</button></p>` : ""}`;
  $("modal").classList.remove("hidden");
  renderTradeChart(p);
  if (open) $("closeBtn").onclick = async () => {
    if (!confirm(L("Position verkaufen?", "Sell position?"))) return;
    await api(`/api/positions/${p.id}/close`, { method: "POST" });
    $("modal").classList.add("hidden"); refresh();
  };
}

// ---------------------------------------------------------------- Agenten
let candCache = [];
async function loadAgents() {
  const [cands, evs] = await Promise.all([
    api("/api/candidates?limit=80"),
    api("/api/events?limit=200" + ($("agentFilter").value ? "&agent=" + encodeURIComponent($("agentFilter").value) : "")),
  ]);
  candCache = cands;
  $("candTable").innerHTML = `<tr><th>Token</th><th>Status</th><th class="num">Score</th><th>${L("Grund", "Reason")}</th>
    <th>${L("Quelle", "Source")}</th><th>${L("geprüft", "checked")}</th></tr>` +
    cands.map((c, i) => `<tr class="click" data-i="${i}"><td>${esc(c.symbol || c.mint.slice(0, 6))}</td>
      <td><span class="tag ${esc(c.status)}">${esc(statusName(c.status))}</span></td>
      <td class="num">${c.score != null ? Math.round(c.score) : "–"}</td><td>${esc(tr(c.reason))}</td><td>${esc(c.source)}</td>
      <td>${c.last_checked ? ago(c.last_checked) : "–"}</td></tr>`).join("");
  $("candTable").querySelectorAll("tr.click").forEach((r) => r.onclick = () => showCandidate(candCache[+r.dataset.i]));
  $("eventLog").innerHTML = evs.map((e) => `<div class="${esc(e.level)}"><span class="t">${dt(e.ts)}</span>
    <span class="a">${esc(agentName(e.agent))}</span> ${esc(tr(e.message))}</div>`).join("")
    || `<div class="empty">${L("Noch keine Einträge.", "No entries yet.")}</div>`;
}

function showCandidate(c) {
  const sd = c.details || {};
  $("modalContent").innerHTML = `
    <h2>${esc(c.symbol || c.mint.slice(0, 6))} <span class="tag ${esc(c.status)}">${esc(statusName(c.status))}</span></h2>
    <div class="kv">
      <div>Score</div><div>${c.score != null ? Math.round(c.score) : "–"} (${L("Kaufschwelle", "buy threshold")} ${lastSummary?.min_score ?? "–"})</div>
      <div>${L("Entscheidung", "Decision")}</div><div>${esc(tr(c.reason))}</div>
      <div>${L("Quelle", "Source")}</div><div>${esc(c.source)}</div>
      <div>${L("Entdeckt / geprüft", "Found / checked")}</div><div>${ago(c.first_seen)} / ${c.last_checked ? ago(c.last_checked) : "–"}</div>
      <div>Mint</div><div style="word-break:break-all">${esc(c.mint)}</div>
      <div>Links</div><div><a href="https://dexscreener.com/solana/${esc(c.mint)}" target="_blank">DexScreener</a> ·
        <a href="https://rugcheck.xyz/tokens/${esc(c.mint)}" target="_blank">RugCheck</a></div>
    </div>
    <h2>${L("Bewertung (Stratege)", "Rating (strategist)")}</h2>
    ${Object.keys(sd).length ? scoreBlock(sd) : `<p class="muted">${L("Noch keine Marktdaten.", "No market data yet.")}</p>`}
    ${aiBlock(sd)}`;
  $("modal").classList.remove("hidden");
}

// ---------------------------------------------------------------- Analyse
function aggTable(obj, first) {
  const rows = Object.entries(obj);
  return `<tr><th>${first}</th><th class="num">Trades</th><th class="num">${L("Treffer", "Wins")}</th><th class="num">Ø %</th>
    <th class="num">PnL SOL</th><th class="num">PF</th></tr>` +
    (rows.length ? rows.map(([k, v]) => `<tr><td>${esc(tr(k))}</td><td class="num">${v.anzahl}</td><td class="num">${v.anzahl ? v.trefferquote + "%" : "–"}</td>
      <td class="num ${cls(v.avg_pct)}">${v.anzahl ? pct(v.avg_pct) : "–"}</td><td class="num ${cls(v.pnl_sol)}">${sol(v.pnl_sol)}</td>
      <td class="num">${v.profit_faktor ?? "–"}</td></tr>`).join("")
      : `<tr><td colspan="6" class="muted">${L("keine Daten", "no data")}</td></tr>`);
}

async function loadAnalysis() {
  const [a, f] = await Promise.all([api("/api/analysis"), api("/api/funnel")]);
  $("funnelInfo").textContent = L(`${f.kandidaten} Coins geprüft. Letzte Entscheidung je Coin, Kaufschwelle Score ${f.min_score}.`,
    `${f.kandidaten} coins checked. Latest decision per coin, buy threshold score ${f.min_score}.`);
  $("funnel").innerHTML = `<tr><th>${L("Grund", "Reason")}</th><th class="num">Coins</th><th class="num">Ø Score</th><th class="num">max</th></tr>` +
    (f.gruende.map((g) => `<tr><td>${esc(tr(g.grund))}</td><td class="num">${g.anzahl}</td>
      <td class="num">${g.avg_score ?? "–"}</td><td class="num">${g.max_score ?? "–"}</td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">${L("keine Daten", "no data")}</td></tr>`);
  const maxN = Math.max(1, ...Object.values(f.score_verteilung));
  $("scoreDist").innerHTML = Object.entries(f.score_verteilung).map(([k, n]) =>
    `<tr><td style="width:60px">${k}</td><td><div class="bar" style="height:10px"><i style="width:${n / maxN * 100}%;${
      +k.split("–")[0] >= f.min_score ? "background:var(--green)" : ""}"></i></div></td><td class="num" style="width:40px">${n}</td></tr>`).join("");
  $("tips").innerHTML = a.vorschlaege.map((x) => `<li>${esc(tr(x))}</li>`).join("");
  $("byExit").innerHTML = aggTable(a.nach_ausstieg, L("Ausstieg", "Exit"));
  $("byScore").innerHTML = aggTable(a.nach_score, "Score");
  $("byAi").innerHTML = aggTable(a.nach_ki_score || {}, L("KI-Score", "AI score"));
}

// ---------------------------------------------------------------- KI-Analyst
let aiLoaded = false;
const usd = (x, d = 4) => "$" + Number(x || 0).toFixed(d);
const num = (x) => Number(x || 0).toLocaleString(LOCALE());

async function loadAI() {
  const d = await api("/api/ai");
  if (!aiLoaded) { renderAISettings(d); aiLoaded = true; }
  renderAIUsage(d.usage);
}

function renderAISettings(d) {
  const s = d.settings;
  const checks = Object.entries(d.checks).map(([k, v]) => `<label><input type="checkbox" data-check="${esc(k)}"
    ${s.checks[k] ? "checked" : ""}> ${esc(L(v.de, v.en))}</label>`).join("");
  const models = Object.entries(d.models).map(([k, v]) => `<option value="${esc(k)}" ${k === s.model ? "selected" : ""}>${esc(v)}</option>`).join("");
  const keyInfo = s.api_key_masked
    ? L(`Gespeichert: ${esc(s.api_key_masked)}${s.api_key_from_env ? " (aus /etc/memebot.env)" : ""}`,
        `Stored: ${esc(s.api_key_masked)}${s.api_key_from_env ? " (from /etc/memebot.env)" : ""}`)
    : L("Noch kein Key gespeichert.", "No key stored yet.");
  $("aiSettings").innerHTML = `
    <p class="hint">${L(`Optionaler Zusatz-Agent: Claude bewertet Name, Story, Socials, Website und Warnsignale eines Coins –
      nur für Coins, die alle anderen Prüfungen bestanden haben. Fällt die KI aus, entscheidet der Bot ohne sie.`,
      `Optional extra agent: Claude rates a coin's name, story, socials, website and red flags – only for coins that passed
      every other check. If the AI fails, the bot decides without it.`)}</p>
    <label class="switch"><input type="checkbox" id="aiEnabled" ${s.enabled ? "checked" : ""}> ${L("KI-Analyst eingeschaltet", "AI analyst enabled")}</label>
    <div class="form">
      <label>${L("Modus", "Mode")}</label><span></span>
      <select id="aiMode">
        <option value="observe" ${s.mode === "observe" ? "selected" : ""}>${L("Nur beobachten (Score speichern, Kauf unabhängig)", "Observe only (store score, buying unaffected)")}</option>
        <option value="filter" ${s.mode === "filter" ? "selected" : ""}>${L("Filtern (Coins unter Mindest-Score ablehnen)", "Filter (reject coins below minimum score)")}</option>
      </select>
      <label>${L("Modell", "Model")}</label><span></span>
      <select id="aiModel">${models}</select>
      <label for="aiMin">${L("Mindest-Score (nur im Filter-Modus)", "Minimum score (filter mode only)")}</label>
      <input id="aiMin" type="number" inputmode="decimal" min="0" max="100" value="${s.min_score}">
      <label for="aiBudget">${L("Tagesbudget in USD (danach Pause bis Mitternacht UTC)", "Daily budget in USD (then paused until midnight UTC)")}</label>
      <input id="aiBudget" type="number" inputmode="decimal" step="0.1" min="0" max="100" value="${s.daily_budget_usd}">
    </div>
    <h3>${L("Was die KI prüft", "What the AI checks")}</h3>
    <div class="checklist">${checks}</div>
    <h3>${L("Anthropic API-Key", "Anthropic API key")}</h3>
    <div class="keyrow"><input id="aiKey" type="password" autocomplete="off" placeholder="sk-ant-…"></div>
    <p class="hint">${keyInfo} ${L(`Key aus der Claude Console (platform.claude.com), Abrechnung pro Nutzung, getrennt vom claude.ai-Abo.
      Er wird in der Datenbank des Bots gespeichert und nie an die Oberfläche zurückgegeben. Feld leer lassen = Key behalten.`,
      `Key from the Claude Console (platform.claude.com), billed per use, separate from the claude.ai subscription.
      It is stored in the bot's database and never sent back to the interface. Leave empty = keep key.`)}
      ${s.api_key_masked && !s.api_key_from_env ? `<a href="#" id="aiClearKey">${L("Key löschen", "Delete key")}</a>` : ""}</p>`;
  const clr = $("aiClearKey");
  if (clr) clr.onclick = async (e) => { e.preventDefault(); if (confirm(L("API-Key löschen?", "Delete API key?"))) await saveAI({ clear_key: true }); };
}

function renderAIUsage(u) {
  const box = (label, a) => kpi(label, usd(a.cost, 3), `${a.calls} ${L("Aufrufe", "calls")} · ${num(a.inp)} / ${num(a.out)} Tok.` +
    (a.searches ? ` · ${a.searches} ${L("Suchen", "searches")}` : "") + (a.errors ? ` · ${a.errors} ${L("Fehler", "errors")}` : ""));
  $("aiKpis").innerHTML = box(L("Heute (UTC)", "Today (UTC)"), u.today) + box(L("7 Tage", "7 days"), u.week) +
    box(L("Gesamt", "Total"), u.total) +
    kpi(L("Ø pro Aufruf", "Avg per call"), u.total.calls ? usd(u.total.cost / u.total.calls, 5) : "–", L("Kosten", "cost"));
  $("aiDaily").innerHTML = `<tr><th>${L("Tag", "Day")}</th><th class="num">${L("Aufrufe", "Calls")}</th><th class="num">Input</th>
    <th class="num">Output</th><th class="num">${L("Suchen", "Searches")}</th><th class="num">${L("Kosten", "Cost")}</th></tr>` +
    (u.daily.map((r) => `<tr><td>${esc(r.day)}</td><td class="num">${r.calls}</td><td class="num">${num(r.inp)}</td>
      <td class="num">${num(r.out)}</td><td class="num">${r.searches || 0}</td><td class="num">${usd(r.cost)}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">${L("noch keine Aufrufe", "no calls yet")}</td></tr>`);
  $("aiRecent").innerHTML = `<tr><th>${L("Zeit", "Time")}</th><th>Token</th><th class="num">Score</th><th>${L("Urteil", "Verdict")}</th>
    <th class="num">Tok. in/out</th><th class="num">${L("Kosten", "Cost")}</th></tr>` +
    (u.recent.map((r) => `<tr><td>${dt(r.ts)}</td><td>${esc(r.symbol || "–")}</td>
      <td class="num">${r.score != null ? Math.round(r.score) : "–"}</td>
      <td style="white-space:normal">${r.ok ? esc(r.verdict || "") : `<span class="neg">${esc(r.error || L("Fehler", "error"))}</span>`}</td>
      <td class="num">${num(r.input_tokens)} / ${num(r.output_tokens)}</td><td class="num">${usd(r.cost_usd, 5)}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">${L("noch keine Aufrufe", "no calls yet")}</td></tr>`);
}

async function saveAI(extra) {
  const body = extra || {
    enabled: $("aiEnabled").checked, mode: $("aiMode").value, model: $("aiModel").value,
    min_score: $("aiMin").value, daily_budget_usd: $("aiBudget").value,
    checks: Object.fromEntries([...document.querySelectorAll("[data-check]")].map((c) => [c.dataset.check, c.checked])),
  };
  if (!extra && $("aiKey").value.trim()) body.api_key = $("aiKey").value.trim();
  const r = await fetch("/api/ai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  $("aiMsg").textContent = r.ok ? L("Gespeichert ✓", "Saved ✓") : L("Fehler: ", "Error: ") + (j.detail || r.status);
  $("aiMsg").className = "hint " + (r.ok ? "pos" : "neg");
  if (r.ok) { renderAISettings(j); renderAIUsage(j.usage); }
}
$("aiSave").onclick = () => saveAI();
$("aiTest").onclick = async () => {
  $("aiMsg").textContent = L("Teste …", "Testing …"); $("aiMsg").className = "hint";
  if ($("aiKey") && $("aiKey").value.trim()) await saveAI();
  const r = await fetch("/api/ai/test", { method: "POST" });
  const j = await r.json();
  $("aiMsg").textContent = r.ok ? L(`Verbindung ok ✓ (${j.model}, Kosten ${usd(j.cost_usd, 5)})`, `Connection ok ✓ (${j.model}, cost ${usd(j.cost_usd, 5)})`)
    : L("Fehler: ", "Error: ") + (j.detail || r.status);
  $("aiMsg").className = "hint " + (r.ok ? "pos" : "neg");
  loadAI();
};

// ---------------------------------------------------------------- Konfiguration
let settingsLoaded = false;
function renderDataInfo() {
  const s = lastSummary || {};
  const feed = !s.feed_enabled ? L("aus (kein Helius-Key)", "off (no Helius key)")
    : s.feed_connected ? L("verbunden ✓", "connected ✓") : L("nicht verbunden – Rückfall auf DexScreener", "not connected – falling back to DexScreener");
  $("dataInfo").innerHTML = L(`
    <div>Kurse offener Positionen</div><div><b>⚡ Live aus der Blockchain</b> (Helius, bei jedem Trade im Pool, ~0,5–1 s) für PumpSwap-Pools.
      Andere Pools: DexScreener alle 2 s (DexScreener selbst hinkt einige Sekunden hinterher). Status: ${feed}</div>
    <div>Stop-Loss / Take-Profit</div><div>werden bei jedem neuen Kurs sofort geprüft</div>
    <div>Watchlist</div><div>DexScreener, jeder Kandidat alle 30 s</div>
    <div>Neue Kandidaten</div><div>Graduations live (Helius + PumpPortal), GeckoTerminal/DexScreener-Listen jede Minute</div>
    <div>Phasen- & Sicherheitscheck</div><div>GeckoTerminal-Kerzen und RugCheck, nur direkt vor einem Kauf</div>
    <div>Statuszeile oben</div><div>„aktiv vor X s“ = wann der Bot zuletzt eine Runde beendet hat (sollte nur wenige Sekunden sein, sonst hängt er).
      „Watchlist N Coins, max. Y s alt“ = so alt sind die Daten des am längsten nicht geprüften Coins (Takt ~30 s)</div>
    <div>Diese Oberfläche</div><div>${wsLive ? "⚡ Live-Push vom Bot (WebSocket), bis zu 4×/s" : "Abfrage alle 2 s (Live-Verbindung nicht aktiv)"};
      Kapitalverlauf: Punkt pro Minute + Live-Punkt</div>`, `
    <div>Prices of open positions</div><div><b>⚡ Live from the blockchain</b> (Helius, on every trade in the pool, ~0.5–1 s) for PumpSwap pools.
      Other pools: DexScreener every 2 s (DexScreener itself lags a few seconds). Status: ${feed}</div>
    <div>Stop-loss / take-profit</div><div>checked immediately on every new price</div>
    <div>Watchlist</div><div>DexScreener, every candidate every 30 s</div>
    <div>New candidates</div><div>graduations live (Helius + PumpPortal), GeckoTerminal/DexScreener lists every minute</div>
    <div>Phase & security check</div><div>GeckoTerminal candles and RugCheck, only right before a buy</div>
    <div>Status line at the top</div><div>"active X s ago" = when the bot last finished a round (should be a few seconds, otherwise it is stuck).
      "Watchlist N coins, max. Y s old" = age of the data of the coin that has gone unchecked the longest (cycle ~30 s)</div>
    <div>This interface</div><div>${wsLive ? "⚡ live push from the bot (WebSocket), up to 4×/s" : "polling every 2 s (live connection not active)"};
      equity curve: one point per minute + live point</div>`);
}

async function loadConfig() {
  renderDataInfo();
  $("cfg").textContent = JSON.stringify(await api("/api/config"), null, 2);
  if (settingsLoaded) return;          // Eingaben beim Auto-Refresh nicht überschreiben
  const items = await api("/api/settings");
  const lbl = (it) => (LANG === "en" ? it.label_en || it.label : it.label);
  $("settingsForm").innerHTML = items.map((it) => `<label for="s_${esc(it.key)}">${esc(lbl(it))}</label>` + (it.options
    ? `<select id="s_${esc(it.key)}" data-key="${esc(it.key)}">${Object.entries(LANG === "en" && it.options_en ? it.options_en : it.options).map(([v, l]) =>
        `<option value="${esc(v)}" ${v === it.value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`
    : `<input id="s_${esc(it.key)}" data-key="${esc(it.key)}" type="number" inputmode="decimal" step="any"
      min="${it.min}" max="${it.max}" value="${it.value}">`)).join("");
  settingsLoaded = true;
  const modeSel = $("s_exit.mode");
  const syncMode = () => document.querySelectorAll("#settingsForm input").forEach((i) => {
    const k = i.dataset.key, simple = modeSel.value === "simple";
    const ladderOnly = /tp[12]_|trailing/.test(k), simpleOnly = k.includes("simple_");
    const hide = (simple && ladderOnly) || (!simple && simpleOnly);
    i.style.display = i.previousElementSibling.style.display = hide ? "none" : "";
  });
  modeSel.onchange = syncMode; syncMode();
}
$("saveSettings").onclick = async () => {
  const body = {};
  document.querySelectorAll("#settingsForm input, #settingsForm select").forEach((i) => body[i.dataset.key] = i.value);
  const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  $("settingsMsg").textContent = r.ok ? L("Gespeichert ✓", "Saved ✓") : L("Fehler: ", "Error: ") + (j.detail || r.status);
  $("settingsMsg").className = "hint " + (r.ok ? "pos" : "neg");
  if (r.ok) { settingsLoaded = false; loadConfig(); }
};

// ---------------------------------------------------------------- Refresh
async function refresh() {
  try {
    await loadSummary();
    if (tab === "overview") { await Promise.all([loadOpen(), loadEquity()]); }
    else if (tab === "trades") await loadTrades();
    else if (tab === "agents") await loadAgents();
    else if (tab === "analysis") await loadAnalysis();
    else if (tab === "config") await loadConfig();
    else if (tab === "ai") await loadAI();
  } catch (e) {
    $("statusDot").className = "dot bad";
    $("statusLine").textContent = L("Keine Verbindung zum Bot (", "No connection to the bot (") + e.message + ")";
  }
}

// ---------------------------------------------------------------- Live-Push vom Bot (WebSocket)
async function connectWs() {
  try {
    const { token } = await api("/api/ws-token");
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?token=" + token);
    ws.onopen = () => { wsLive = true; };
    ws.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (!$("modal").classList.contains("hidden")) { updateTradeChart(d.open); return; }
      renderSummary(d.summary);
      if (tab === "overview") { renderOpen(d.open); loadEquity(); }
    };
    ws.onclose = () => { wsLive = false; setTimeout(connectWs, 3000); };
  } catch (e) { wsLive = false; setTimeout(connectWs, 5000); }
}
connectWs();

refresh();
// Übersicht alle 2 s ohne Live-Verbindung, andere Tabs alle 10 s
let tick = 0;
setInterval(() => {
  tick++;
  if (document.hidden) return;
  if (!$("modal").classList.contains("hidden")) {               // Chart ohne Live-Verbindung alle 2 s nachführen
    if (tv.chart && tv.open && !wsLive) api("/api/positions?state=open").then(updateTradeChart).catch(() => {});
    return;
  }
  if ((tab === "overview" && !wsLive) || tick % 5 === 0) refresh();
}, 2000);
window.addEventListener("resize", () => tab === "overview" && loadEquity());
