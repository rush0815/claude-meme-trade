"use strict";
// Sprache der Oberfläche. Deutsch ist die Grundsprache (Texte im Code), Englisch wird hier übersetzt.
let LANG = (() => { try { return localStorage.getItem("memebot_lang") || "de"; } catch (e) { return "de"; } })();
function setLang(l) {
  LANG = l;
  try { localStorage.setItem("memebot_lang", l); } catch (e) { /* privater Modus: nur für diese Sitzung */ }
}
const L = (de, en) => (LANG === "en" ? en : de);          // Satz in beiden Sprachen direkt im Code
const LOCALE = () => (LANG === "en" ? "en-GB" : "de-DE");

// Feste Texte (deutscher Text = Schlüssel)
const UI_EN = {
  "Übersicht": "Overview", "Trades": "Trades", "Agenten": "Agents", "Analyse": "Analysis",
  "Konfiguration": "Configuration", "Steuerung": "Control", "verbinde…": "connecting…",
  "Kapitalverlauf": "Equity curve", "7T": "7D", "30T": "30D", "Offene Positionen": "Open positions",
  "Abgeschlossene Trades": "Closed trades", "Watchlist & Entscheidungen": "Watchlist & decisions",
  "Agenten-Log": "Agent log", "alle": "all", "Stratege": "Strategist", "Phasen-Analyst": "Phase analyst",
  "Woran scheitern die Kandidaten? (24 h)": "Why do candidates fail? (24 h)", "Score-Verteilung": "Score distribution",
  "Vorschläge des Analysten": "Analyst suggestions", "Nach Ausstiegsgrund": "By exit reason",
  "Nach Einstiegs-Score": "By entry score", "Pausieren (keine neuen Käufe)": "Pause (no new buys)",
  "Fortsetzen / Not-Aus lösen": "Resume / release kill switch",
  "NOT-AUS: alles verkaufen & stoppen": "KILL SWITCH: sell everything & stop",
  "Pausieren lässt offene Positionen weiterlaufen (Stop-Loss etc. bleiben aktiv).":
    "Pausing keeps open positions running (stop-loss etc. stay active).",
  "Ausstieg & Risiko anpassen": "Exit & risk settings",
  "Gilt sofort, auch für offene Positionen. Wird gespeichert und überlebt Neustarts.":
    "Applies immediately, also to open positions. Saved and kept across restarts.",
  "Speichern": "Save", "Woher kommen die Daten?": "Where does the data come from?",
  "Aktive Konfiguration": "Active configuration", "Sprache": "Language",
  "Sprache der Oberfläche. Wird in diesem Browser gespeichert.": "Interface language. Stored in this browser.",
  "KI-Analyst": "AI analyst", "Verbindung testen": "Test connection", "Token-Verbrauch": "Token usage",
  "Letzte KI-Aufrufe": "Recent AI calls", "Nach KI-Score": "By AI score",
  "Alle Aufrufe anzeigen": "Show all calls", "Als CSV herunterladen": "Download as CSV",
};
const t = (s) => (LANG === "en" ? (UI_EN[s] ?? s) : s);

const AGENT_EN = { "Stratege": "Strategist", "Phasen-Analyst": "Phase analyst", "KI-Analyst": "AI analyst" };
const STATUS = {
  de: { new: "neu", waiting: "wartet", rejected: "abgelehnt", bought: "gekauft", expired: "abgelaufen" },
  en: { new: "new", waiting: "waiting", rejected: "rejected", bought: "bought", expired: "expired" },
};
const BOT_STATUS_EN = { "läuft": "running", "startet": "starting", "gestoppt": "stopped" };
const PART_EN = {
  kaufdruck: "buy pressure", volumen_beschleunigung: "volume acceleration", momentum: "momentum",
  trend: "trend", beteiligung: "participation", liquiditaet: "liquidity",
};

// Texte, die vom Bot kommen (Gründe, Ausstiege, Log, Vorschläge). Zahlen werden übernommen.
const N = "([-+]?\\$?[\\d.,…]+)";
const PATTERNS_EN = [
  // Ausstiege
  [/^Stop-Loss$/, "Stop-loss"], [/^Break-even-Stop$/, "Break-even stop"], [/^Trailing-Stop$/, "Trailing stop"],
  [/^Zeitlimit$/, "Time limit"], [/^Liquidität eingebrochen$/, "Liquidity collapsed"],
  [/^Manuell geschlossen$/, "Closed manually"], [/^Einstieg$/, "Entry"],
  [/^Take-Profit (\d+) \((.+)\)$/, "Take-profit $1 ($2)"], [/^Take-Profit \((.+)\)$/, "Take-profit ($1)"],
  [/^Take-Profit (\d+)$/, "Take-profit $1"], [/^Take-Profit$/, "Take-profit"],
  // Stratege
  [new RegExp(`^zu alt \\(${N} h\\)$`), "too old ($1 h)"], [/^zu alt \(…\s*\)$/, "too old (… h)"],
  [/^eingeschlafen \(kaum Liquidität\/Volumen\)$/, "dead (hardly any liquidity/volume)"],
  [new RegExp(`^zu jung \\(${N} min\\)$`), "too young ($1 min)"], [/^zu jung \(…\s*\)$/, "too young (… min)"],
  [/^kein Preis$/, "no price"],
  [/^Liquidität (.+) zu gering$/, "liquidity $1 too low"],
  [/^Liquidität zu groß \(kein Meme-Momentum\)$/, "liquidity too large (no meme momentum)"],
  [/^Bewertung (.+) zu hoch$/, "valuation $1 too high"],
  [/^Volumen 1h (.+) zu gering$/, "volume 1h $1 too low"], [/^Volumen (.+) zu gering$/, "volume $1 too low"],
  [/^zu wenig Trades \(5 min\)$/, "too few trades (5 min)"],
  [/^Abwärtstrend 6 h \((.+)\) – Hype vorbei$/, "downtrend 6 h ($1) – hype is over"],
  [/^Abverkauf 1 h \((.+)\)$/, "sell-off 1 h ($1)"],
  [/^FOMO-Spitze \((.+) in 5 min\)$/, "FOMO spike ($1 in 5 min)"], [/^FOMO-Spitze (.+)$/, "FOMO spike $1"],
  [/^kein Momentum$/, "no momentum"],
  [/^Kauf\/Verkauf (.+) zu schwach$/, "buy/sell ratio $1 too weak"],
  [/^Filter bestanden$/, "filters passed"],
  // Orchestrator / Risk
  [/^noch keine Marktdaten$/, "no market data yet"],
  [/^Score (.+) unter (.+)$/, "score $1 below $2"], [/^gekauft$/, "bought"],
  [/^Kauf fehlgeschlagen$/, "buy failed"], [/^Fehler: (.+)$/, "error: $1"],
  [/^Not-Aus aktiv$/, "kill switch active"], [/^pausiert$/, "paused"],
  [/^max\. offene Positionen erreicht$/, "max. open positions reached"],
  [/^Tagesverlust-Limit erreicht$/, "daily loss limit reached"], [/^bereits im Bestand$/, "already held"],
  [/^Cooldown für diesen Coin$/, "cooldown for this coin"],
  [/^zu wenig Kapital \(verfügbar (.+) SOL\)$/, "not enough capital (available $1 SOL)"],
  [/^zu wenig Kapital(.*)$/, "not enough capital$1"],
  // Rug-Analyst
  [/^als rugged markiert$/, "flagged as rugged"],
  [/^Mint-Authority aktiv \(Dev kann nachdrucken\)$/, "mint authority active (dev can mint more)"],
  [/^Freeze-Authority aktiv \(Dev kann sperren\)$/, "freeze authority active (dev can freeze)"],
  [/^Liquidität nur (.+) gelockt$/, "only $1 of liquidity locked"],
  [/^Top-10-Holder halten (.+)$/, "top 10 holders own $1"], [/^Ein Holder hält (.+)$/, "one holder owns $1"],
  [/^Risiken: (.+)$/, "risks: $1"], [/^RugCheck-Score (.+) zu hoch$/, "RugCheck score $1 too high"],
  [/^RugCheck nicht erreichbar$/, "RugCheck unreachable"],
  [/^Verkaufstest: keine Route \(möglicher Honeypot\)$/, "sell test: no route (possible honeypot)"],
  [/^Verkaufstest: Preiseinfluss (.+)$/, "sell test: price impact $1"],
  [/^Verkaufstest: nur (.+) SOL zurück \(Steuer\/Honeypot\?\)$/, "sell test: only $1 SOL back (tax/honeypot?)"],
  [/^Verkaufstest ok$/, "sell test ok"], [/^Sicherheitsprüfung bestanden$/, "security check passed"],
  // Phasen-Analyst
  [/^keine Kursdaten für den Phasen-Check$/, "no price history for the phase check"],
  [/^(.+) unter dem Hoch – Hype vorbei$/, "$1 below the high – hype is over"],
  [/^Hoch vor (.+) h, (.+) darunter – Hype vorbei$/, "high $1 h ago, $2 below – hype is over"],
  [/^Kerzen nicht abrufbar \(GeckoTerminal-Limit\)$/, "candles unavailable (GeckoTerminal limit)"],
  [/^Phase ok \((.+) vom Hoch\)$/, "phase ok ($1 from high)"],
  // Scout / Kursquelle
  [/^kein SOL-Paar gefunden$/, "no SOL pair found"], [/^Watchlist voll$/, "watchlist full"],
  [/^Live-Kurse aus \(kein Helius-Key\)$/, "live prices off (no Helius key)"],
  [/^Pool-Typ nicht unterstützt \(z\. B\. Raydium\)$/, "pool type not supported (e.g. Raydium)"],
  [/^Pool-Preis weicht (.+) ab$/, "pool price deviates $1"], [/^Live-Abo wird aufgebaut$/, "live subscription starting"],
  [/^Pool nicht lesbar (.+)$/, "pool not readable $1"],
  // Log
  [/^KAUF (.+?): (.+) SOL, Score (.+)$/, "BUY $1: $2 SOL, score $3"],
  [/^VERKAUF (.+?) \((.+)\): (.+)$/, (m, a, b, c) => `SELL ${a} (${tr(b)}): ${c}`],
  [/^TEILVERKAUF (.+?) \((.+)\): (.+)$/, (m, a, b, c) => `PARTIAL SELL ${a} (${tr(b)}): ${c}`],
  [/^(.+?): Kauf fehlgeschlagen – (.+)$/, "$1: buy failed – $2"],
  [/^(.+?): Verkauf fehlgeschlagen \((.+)x\) – (.+)$/, "$1: sell failed ($2x) – $3"],
  [/^(.+?): abgelehnt – (.+)$/, (m, a, b) => `${a}: rejected – ${tr(b)}`],
  [/^(.+?): Signal Score (.+)$/, "$1: signal score $2"],
  [/^Neue Graduation entdeckt$/, "New graduation detected"],
  [/^(\d+) neue Kandidaten von (.+)$/, "$1 new candidates from $2"],
  [/^Bot gestartet im Modus (.+)$/, "Bot started in $1 mode"],
  [/^PumpPortal verbunden$/, "PumpPortal connected"],
  [/^PumpPortal wieder verbunden (.+)$/, "PumpPortal reconnected $1"],
  [/^PumpPortal nicht erreichbar \((.+)\) – Scout nutzt weiter DexScreener$/, "PumpPortal unreachable ($1) – scout keeps using DexScreener"],
  [/^Helius-Graduations verbunden(.*)$/, "Helius graduations connected$1"],
  [/^Helius-Graduations getrennt (.+)$/, "Helius graduations disconnected $1"],
  [/^Live-Kurse wieder verbunden (.+)$/, "Live prices reconnected $1"],
  [/^Live-Kurse getrennt \((.+)\) – nutze DexScreener$/, "Live prices disconnected ($1) – using DexScreener"],
  [/^Steuerung: (.+)$/, "Control: $1"], [/^Einstellungen geändert: (.+)$/, "Settings changed: $1"],
  [/^Manuelles Schließen angefordert: (.+)$/, "Manual close requested: $1"],
  [/^NOT-AUS: Drawdown (.+) vom Höchststand (.+) SOL\. Keine neuen Käufe\.$/, "KILL SWITCH: drawdown $1 from peak $2 SOL. No new buys."],
  [/^Ausstiegsregeln auf v2 umgestellt .*$/, "Exit rules switched to v2 (break-even from +25 %, TP1 +30 %, trailing from +50 %, TP2 +80 %, time limit 2 h)"],
  [/^Takte umgestellt: .*$/, "Timing changed: watchlist every 30 s, main cycle 5 s, max. age 8 h"],
  [/^Zyklus-Fehler: (.+)$/, "Cycle error: $1"],
  // KI-Analyst
  [/^KI-Score (\d+) unter (\d+)$/, "AI score $1 below $2"],
  [/^(.+?): KI-Score (\d+) – (.*) \((.+) ct\)$/, "$1: AI score $2 – $3 ($4 ct)"],
  [/^(.+?): KI nicht erreichbar \((.+)\) – Entscheidung ohne KI$/, "$1: AI unreachable ($2) – deciding without AI"],
  [/^(.+?): KI-Antwort nicht lesbar – Entscheidung ohne KI$/, "$1: AI answer unreadable – deciding without AI"],
  [/^Tagesbudget von (.+) \$ erreicht – KI pausiert bis Mitternacht \(UTC\)$/, "Daily budget of $1 $ reached – AI paused until midnight (UTC)"],
  [/^KI-Einstellungen geändert: (.+)$/, "AI settings changed: $1"],
  [/^KI aus$/, "AI analyst switched off"], [/^KI ohne API-Key$/, "no API key set"],
  [/^KI-Budget erreicht$/, "daily budget reached"], [/^KI-Guthaben aufgebraucht$/, "API credit used up"], [/^keine KI-Prüfungen aktiv$/, "no AI checks enabled"],
  [/^KI nicht erreichbar$/, "AI unreachable"], [/^KI-Antwort nicht lesbar$/, "AI answer unreadable"],
  [/^ohne KI$/, "without AI"], [/^KI (.+)$/, "AI $1"],
  // Analyst
  [/^Noch zu wenig Daten \((\d+) Trades\)\. Aussagekräftig wird es ab ca\. 30–50 Trades\.$/, "Not enough data yet ($1 trades). Meaningful from about 30–50 trades."],
  [/^Profit-Faktor (.+) < 1: Die Strategie verliert gerade Geld\. Nicht live schalten\.$/, "Profit factor $1 < 1: the strategy is losing money. Do not go live."],
  [/^Profit-Faktor (.+) über (\d+) Trades: (.+)$/, "Profit factor $1 over $2 trades: candidate for a careful live test with a small wallet."],
  [/^Über die Hälfte der Trades endet im Stop-Loss(.+)$/, "More than half of the trades end in the stop-loss: make entries stricter (raise min_score, raise min_buy_sell_ratio_m5 or lower max_change_m5_pct)."],
  [/^Trades, die ins Zeitlimit laufen, verlieren im Schnitt(.+)$/, "Trades that hit the time limit lose on average: shorten max_hold_min."],
  [/^Trailing-Stops sichern kaum Gewinn(.+)$/, "Trailing stops lock in little profit: widen trailing_stop_pct or raise trailing_activate_pct."],
  [/^Score-Bereich (.+) verliert, höhere Scores gewinnen: min_score auf (.+) anheben\.$/, "Score range $1 loses, higher scores win: raise min_score to $2."],
  [/^Keine Auffälligkeiten – weiter Daten sammeln\.$/, "Nothing unusual – keep collecting data."],
];
function tr(s) {
  if (LANG !== "en" || !s) return s;
  for (const [re, rep] of PATTERNS_EN) if (re.test(s)) return s.replace(re, rep);
  return s;
}
const agentName = (a) => (LANG === "en" ? (AGENT_EN[a] ?? a) : a);

// Statische Texte im HTML: Element mit data-i18n="<deutscher Text>"
function applyStaticLang() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
}
