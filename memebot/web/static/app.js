"use strict";
// Token-Namen kommen von außen -> immer escapen
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);
const sol = (x, d = 4) => (x == null || isNaN(x) ? "–" : Number(x).toFixed(d));
const pct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%");
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1) + " h";
  return (s / 86400).toFixed(1) + " T";
};
const dt = (ts) => new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const dur = (a, b) => { const m = (b - a) / 60; return m < 60 ? Math.round(m) + " min" : (m / 60).toFixed(1) + " h"; };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

let tab = "overview", hours = 168, lastSummary = null;

// ---------------------------------------------------------------- Tabs
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
$("modalClose").onclick = () => $("modal").classList.add("hidden");
$("modal").onclick = (e) => { if (e.target.id === "modal") $("modal").classList.add("hidden"); };
document.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
  const act = b.dataset.act;
  if (act === "kill" && !confirm("Wirklich alle Positionen verkaufen und den Bot stoppen?")) return;
  await api("/api/control/" + act, { method: "POST" });
  refresh();
});

// ---------------------------------------------------------------- Übersicht
function kpi(label, value, sub = "", c = "") {
  return `<div class="kpi"><div class="l">${label}</div><div class="v ${c}">${value}</div><div class="s">${sub}</div></div>`;
}

async function loadSummary() {
  const s = await api("/api/summary");
  lastSummary = s;
  const stale = s.last_cycle && s.now - s.last_cycle > 120;
  $("statusDot").className = "dot " + (s.killed ? "bad" : s.paused || stale ? "warn" : "ok");
  $("modeBadge").textContent = s.sim ? "SIMULATION" : s.mode.toUpperCase();
  $("modeBadge").className = "badge " + s.mode;
  $("statusLine").textContent = (s.killed ? "NOT-AUS aktiv · " : s.paused ? "pausiert · " : "") +
    `${s.status} · letzter Zyklus vor ${s.last_cycle ? ago(s.last_cycle) : "–"} · Watchlist ${s.watchlist}`;
  const st = s.stats;
  $("kpis").innerHTML =
    kpi("Kapital", sol(s.equity_sol, 3) + " SOL", "Kasse " + sol(s.cash_sol, 3)) +
    kpi("Rendite gesamt", pct(s.total_return_pct), "Start " + sol(s.start_sol, 3) + " SOL", cls(s.total_return_pct)) +
    kpi("Offen", s.open_count + " Pos.", "unrealisiert " + sol(s.unrealized_sol), cls(s.unrealized_sol)) +
    kpi("Letzte 24 h", sol(s.last24h.pnl_sol) + " SOL", s.last24h.anzahl + " Trades", cls(s.last24h.pnl_sol)) +
    kpi("Trades", st.anzahl, st.gewinner + " Gewinner") +
    kpi("Trefferquote", st.anzahl ? st.trefferquote + "%" : "–", "Ø " + pct(st.avg_pct)) +
    kpi("Profit-Faktor", st.profit_faktor ?? "–", "Gewinne / Verluste", st.profit_faktor > 1 ? "pos" : st.profit_faktor ? "neg" : "") +
    kpi("Max. Drawdown", st.max_drawdown_pct != null ? st.max_drawdown_pct + "%" : "–", "vom Höchststand");
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
  if (pts.length < 2) { g.fillText("Noch keine Daten – der Verlauf erscheint nach ein paar Minuten.", 8, 20); return; }
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

function posRow(p) {
  const open = p.status === "open";
  const v = open ? p.unrealized_sol : p.pnl_sol, vp = open ? p.unrealized_pct : p.pnl_pct;
  const meta = open
    ? `seit ${ago(p.opened_at)} · Einsatz ${sol(p.size_sol)} · Score ${Math.round(p.score)}` +
      (p.tp_hits ? ` · TP ${p.tp_hits} verkauft: ${sol(p.sol_received)} SOL gesichert, Rest ${Math.round(p.tokens_remaining / p.tokens_total * 100)}%` : "")
    : `${dt(p.closed_at)} · ${esc(p.exit_reason)} · ${dur(p.opened_at, p.closed_at)} · Einsatz ${sol(p.size_sol)}`;
  return `<div class="row" data-id="${p.id}">
    <div class="sym">${esc(p.symbol || p.mint.slice(0, 6))}</div>
    <div class="pnl ${cls(v)}">${sol(v)} SOL</div>
    <div class="meta">${meta}</div>
    <div class="meta pnl ${cls(vp)}">${pct(vp)}</div></div>`;
}

function bindRows(el) {
  el.querySelectorAll(".row").forEach((r) => r.onclick = () => showPosition(r.dataset.id));
}

async function loadOpen() {
  const rows = await api("/api/positions?state=open");
  $("openList").innerHTML = rows.length ? rows.map(posRow).join("") : `<div class="empty">Keine offenen Positionen.</div>`;
  bindRows($("openList"));
}

// ---------------------------------------------------------------- Trades
async function loadTrades() {
  const rows = await api("/api/positions?state=closed&limit=500");
  $("tradeList").innerHTML = rows.length ? rows.map(posRow).join("") : `<div class="empty">Noch keine abgeschlossenen Trades.</div>`;
  bindRows($("tradeList"));
}

const PARTS = ["kaufdruck", "volumen_beschleunigung", "momentum", "trend", "beteiligung", "liquiditaet"];
function scoreBlock(sd) {
  const bars = PARTS.filter((k) => k in sd).map((k) =>
    `<div>${k.replaceAll("_", " ")}</div><div>${sd[k]}<div class="bar"><i style="width:${sd[k]}%"></i></div></div>`).join("");
  return `<div class="kv">${bars}
      <div>Kauf/Verkauf 5m</div><div>${sd.kauf_verkauf ?? "–"}</div>
      <div>Volumen-Beschl.</div><div>${sd.vol_beschl ?? "–"}×</div>
      <div>Kurs 5m / 1h</div><div>${pct(sd.chg_m5)} / ${pct(sd.chg_h1)}</div>
      <div>Liquidität / FDV</div><div>$${(sd.liq_usd || 0).toLocaleString("de-DE")} / $${(sd.fdv_usd || 0).toLocaleString("de-DE")}</div>
      <div>Alter</div><div>${sd.alter_min ?? "–"} min</div>
      <div>Abstand vom Hoch</div><div>${sd.vom_hoch_pct != null ? pct(sd.vom_hoch_pct) + " (Hoch vor " + sd.std_seit_hoch + " h)" : "–"}</div>
    </div>`;
}

async function showPosition(id) {
  const p = await api("/api/positions/" + id);
  const open = p.status === "open";
  const sd = p.score_details || {}, sec = p.security || {};
  const fills = p.fills.map((f) => `<tr><td>${dt(f.ts)}</td><td>${f.side === "buy" ? "Kauf" : "Verkauf"}</td>
    <td class="num">${sol(f.sol_amount)}</td><td>${esc(f.reason)}</td>
    <td>${f.signature && f.signature !== "paper" ? `<a href="https://solscan.io/tx/${esc(f.signature)}" target="_blank">Tx</a>` : "paper"}</td></tr>`).join("");
  $("modalContent").innerHTML = `
    <h2>${esc(p.symbol)} <span class="muted">${esc(p.name)}</span></h2>
    <div class="kv">
      <div>Status</div><div>${open ? "offen" : "geschlossen – " + esc(p.exit_reason)}</div>
      <div>Ergebnis</div><div class="${cls(open ? p.unrealized_sol : p.pnl_sol)}">${sol(open ? p.unrealized_sol : p.pnl_sol)} SOL (${pct(open ? p.unrealized_pct : p.pnl_pct)})</div>
      <div>Einsatz</div><div>${sol(p.size_sol)} SOL</div>
      <div>Zurück erhalten</div><div>${sol(p.sol_received)} SOL${open && p.tp_hits ? " (aus Teilverkäufen)" : ""}</div>
      ${open ? `<div>Noch gehalten</div><div>${Math.round(p.tokens_remaining / p.tokens_total * 100)}% der Token, Wert ${sol(p.value_sol)} SOL</div>` : ""}
      <div>Einstieg / Hoch / Letzter</div><div>${(+p.entry_price).toExponential(3)} / ${(+p.high_price).toExponential(3)} / ${(+p.last_price).toExponential(3)}</div>
      <div>Eröffnet</div><div>${dt(p.opened_at)}${p.closed_at ? " · Dauer " + dur(p.opened_at, p.closed_at) : ""}</div>
      <div>Score</div><div>${Math.round(p.score)}</div>
      <div>Mint</div><div style="word-break:break-all">${esc(p.mint)}</div>
      <div>Links</div><div><a href="https://dexscreener.com/solana/${esc(p.pair_address || p.mint)}" target="_blank">DexScreener</a> ·
        <a href="https://rugcheck.xyz/tokens/${esc(p.mint)}" target="_blank">RugCheck</a></div>
    </div>
    <h2>Warum gekauft (Stratege)</h2>
    ${scoreBlock(sd)}
    <h2>Sicherheit (Rug-Analyst)</h2>
    <div class="kv">
      <div>RugCheck-Score</div><div>${sec.score ?? "–"}</div>
      <div>LP gelockt</div><div>${sec.lp_locked_pct ?? "–"}%</div>
      <div>Top-10-Holder</div><div>${sec.top10_pct ?? "–"}%</div>
      <div>Mint/Freeze</div><div>${sec.mint_authority ? "aktiv" : "entzogen"} / ${sec.freeze_authority ? "aktiv" : "entzogen"}</div>
    </div>
    <h2>Ausführungen</h2>
    <div class="scroll"><table><tr><th>Zeit</th><th>Art</th><th class="num">SOL</th><th>Grund</th><th></th></tr>${fills}</table></div>
    ${open ? `<p><button class="btn small danger" id="closeBtn">Position jetzt verkaufen</button></p>` : ""}`;
  $("modal").classList.remove("hidden");
  if (open) $("closeBtn").onclick = async () => {
    if (!confirm("Position verkaufen?")) return;
    await api(`/api/positions/${p.id}/close`, { method: "POST" });
    $("modal").classList.add("hidden"); refresh();
  };
}

// ---------------------------------------------------------------- Agenten
const STATUS_DE = { new: "neu", waiting: "wartet", rejected: "abgelehnt", bought: "gekauft", expired: "abgelaufen" };
async function loadAgents() {
  const [cands, evs] = await Promise.all([
    api("/api/candidates?limit=80"),
    api("/api/events?limit=200" + ($("agentFilter").value ? "&agent=" + encodeURIComponent($("agentFilter").value) : "")),
  ]);
  candCache = cands;
  $("candTable").innerHTML = `<tr><th>Token</th><th>Status</th><th class="num">Score</th><th>Grund</th><th>Quelle</th><th>geprüft</th></tr>` +
    cands.map((c, i) => `<tr class="click" data-i="${i}"><td>${esc(c.symbol || c.mint.slice(0, 6))}</td><td><span class="tag ${esc(c.status)}">${esc(STATUS_DE[c.status] || c.status)}</span></td>
      <td class="num">${c.score != null ? Math.round(c.score) : "–"}</td><td>${esc(c.reason)}</td><td>${esc(c.source)}</td>
      <td>${c.last_checked ? ago(c.last_checked) : "–"}</td></tr>`).join("");
  $("candTable").querySelectorAll("tr.click").forEach((r) => r.onclick = () => showCandidate(candCache[+r.dataset.i]));
  $("eventLog").innerHTML = evs.map((e) => `<div class="${esc(e.level)}"><span class="t">${dt(e.ts)}</span>
    <span class="a">${esc(e.agent)}</span> ${esc(e.message)}</div>`).join("") || `<div class="empty">Noch keine Einträge.</div>`;
}

let candCache = [];
function showCandidate(c) {
  const sd = c.details || {};
  $("modalContent").innerHTML = `
    <h2>${esc(c.symbol || c.mint.slice(0, 6))} <span class="tag ${esc(c.status)}">${esc(STATUS_DE[c.status] || c.status)}</span></h2>
    <div class="kv">
      <div>Score</div><div>${c.score != null ? Math.round(c.score) : "–"} (Kaufschwelle ${lastSummary?.min_score ?? "–"})</div>
      <div>Entscheidung</div><div>${esc(c.reason)}</div>
      <div>Quelle</div><div>${esc(c.source)}</div>
      <div>Entdeckt / geprüft</div><div>vor ${ago(c.first_seen)} / ${c.last_checked ? "vor " + ago(c.last_checked) : "–"}</div>
      <div>Mint</div><div style="word-break:break-all">${esc(c.mint)}</div>
      <div>Links</div><div><a href="https://dexscreener.com/solana/${esc(c.mint)}" target="_blank">DexScreener</a> ·
        <a href="https://rugcheck.xyz/tokens/${esc(c.mint)}" target="_blank">RugCheck</a></div>
    </div>
    <h2>Bewertung (Stratege)</h2>
    ${Object.keys(sd).length ? scoreBlock(sd) : `<p class="muted">Noch keine Marktdaten.</p>`}`;
  $("modal").classList.remove("hidden");
}

// ---------------------------------------------------------------- Analyse
function aggTable(obj, first) {
  const rows = Object.entries(obj);
  return `<tr><th>${first}</th><th class="num">Trades</th><th class="num">Treffer</th><th class="num">Ø %</th><th class="num">PnL SOL</th><th class="num">PF</th></tr>` +
    (rows.length ? rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v.anzahl}</td><td class="num">${v.anzahl ? v.trefferquote + "%" : "–"}</td>
      <td class="num ${cls(v.avg_pct)}">${v.anzahl ? pct(v.avg_pct) : "–"}</td><td class="num ${cls(v.pnl_sol)}">${sol(v.pnl_sol)}</td>
      <td class="num">${v.profit_faktor ?? "–"}</td></tr>`).join("") : `<tr><td colspan="6" class="muted">keine Daten</td></tr>`);
}

async function loadAnalysis() {
  const [a, f] = await Promise.all([api("/api/analysis"), api("/api/funnel")]);
  $("funnelInfo").textContent = `${f.kandidaten} Coins geprüft. Letzte Entscheidung je Coin, Kaufschwelle Score ${f.min_score}.`;
  $("funnel").innerHTML = `<tr><th>Grund</th><th class="num">Coins</th><th class="num">Ø Score</th><th class="num">max</th></tr>` +
    (f.gruende.map((g) => `<tr><td>${esc(g.grund)}</td><td class="num">${g.anzahl}</td>
      <td class="num">${g.avg_score ?? "–"}</td><td class="num">${g.max_score ?? "–"}</td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">keine Daten</td></tr>`);
  const maxN = Math.max(1, ...Object.values(f.score_verteilung));
  $("scoreDist").innerHTML = Object.entries(f.score_verteilung).map(([k, n]) =>
    `<tr><td style="width:60px">${k}</td><td><div class="bar" style="height:10px"><i style="width:${n / maxN * 100}%;${
      +k.split("–")[0] >= f.min_score ? "background:var(--green)" : ""}"></i></div></td><td class="num" style="width:40px">${n}</td></tr>`).join("");
  $("tips").innerHTML = a.vorschlaege.map((t) => `<li>${esc(t)}</li>`).join("");
  $("byExit").innerHTML = aggTable(a.nach_ausstieg, "Ausstieg");
  $("byScore").innerHTML = aggTable(a.nach_score, "Score");
}

let settingsLoaded = false;
async function loadControl() {
  $("cfg").textContent = JSON.stringify(await api("/api/config"), null, 2);
  if (settingsLoaded) return;          // Eingaben beim Auto-Refresh nicht überschreiben
  const items = await api("/api/settings");
  $("settingsForm").innerHTML = items.map((it) => `<label for="s_${esc(it.key)}">${esc(it.label)}</label>
    <input id="s_${esc(it.key)}" data-key="${esc(it.key)}" type="number" inputmode="decimal" step="any"
      min="${it.min}" max="${it.max}" value="${it.value}">`).join("");
  settingsLoaded = true;
}
$("saveSettings").onclick = async () => {
  const body = {};
  document.querySelectorAll("#settingsForm input").forEach((i) => body[i.dataset.key] = i.value);
  const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  $("settingsMsg").textContent = r.ok ? "Gespeichert ✓" : "Fehler: " + (j.detail || r.status);
  $("settingsMsg").className = "hint " + (r.ok ? "pos" : "neg");
  if (r.ok) { settingsLoaded = false; loadControl(); }
};

// ---------------------------------------------------------------- Refresh
async function refresh() {
  try {
    await loadSummary();
    if (tab === "overview") { await Promise.all([loadOpen(), loadEquity()]); }
    else if (tab === "trades") await loadTrades();
    else if (tab === "agents") await loadAgents();
    else if (tab === "analysis") await loadAnalysis();
    else if (tab === "control") await loadControl();
  } catch (e) {
    $("statusDot").className = "dot bad";
    $("statusLine").textContent = "Keine Verbindung zum Bot (" + e.message + ")";
  }
}
refresh();
// Übersicht alle 2 s (nur Datenbank-Lesen, belastet den Bot nicht), andere Tabs alle 5 s
let tick = 0;
setInterval(() => {
  tick++;
  if (document.hidden || !$("modal").classList.contains("hidden")) return;
  if (tab === "overview" || tick % 5 === 0) refresh();
}, 2000);
window.addEventListener("resize", () => tab === "overview" && loadEquity());
