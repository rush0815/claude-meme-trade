# claude-meme-trade

Solana-Meme-Coin-Bot mit mehreren zusammenarbeitenden Agenten und einer Web-Oberfläche.

> **Ohne Garantie.** Meme-Coins sind extrem riskant, die meisten Trader verlieren dort Geld.
> Der Bot startet im **Papiermodus** (Spielgeld, echte Kurse). Schalte erst auf Live, wenn
> die Statistik über viele Trades positiv ist – und dann nur mit einer eigenen, kleinen Wallet.

## Strategie: Momentum nach der Graduation

| Baustein | Regel |
|---|---|
| Universum | Coins, die von pump.fun auf PumpSwap/Raydium gewechselt sind (+ neue DexScreener-Listings), Alter 15 min – 24 h |
| Sicherheit | Mint- und Freeze-Authority entzogen, ≥ 80 % LP gelockt/verbrannt, Top-10-Holder ≤ 35 %, keine „danger“-Risiken bei RugCheck, Verkaufstest über Jupiter |
| Einstieg | Score ≥ 65 aus Kaufdruck, Volumen-Beschleunigung, Momentum (ohne FOMO-Spitzen), Trend, Beteiligung, Liquidität/Bewertung |
| Größe | 3 % des Kapitals (0,75×–1,25× je nach Score), max. 4 Positionen |
| Ausstieg | Stop-Loss −20 %, Teilverkäufe bei +40 % und +100 %, Rest mit Trailing-Stop (−18 % vom Hoch, ab +30 %), Zeitlimit 3 h, sofort raus bei Liquiditätseinbruch |
| Schutz | Tagesverlust-Limit 10 %, automatischer Not-Aus bei 30 % Drawdown |

Alle Werte stehen in `config.toml` und lassen sich anpassen.

## Agenten

| Agent | Aufgabe |
|---|---|
| **Scout** | findet Kandidaten (PumpPortal-WebSocket: Graduations, DexScreener: neue Profile/Boosts), führt die Watchlist |
| **Stratege** | Filter + Bewertung 0–100 |
| **Rug-Analyst** | RugCheck-Bericht auswerten, Verkaufstest (Honeypot-Schutz) |
| **Risk-Manager** | Positionsgröße, Limits, Not-Aus – hat Vetorecht |
| **Executor** | Paper: echte Jupiter-Quotes + Sicherheitsabschlag · Live: Jupiter-Swap, lokal signiert, über eigenen RPC gesendet |
| **Position-Manager** | überwacht offene Positionen, löst Ausstiege aus |
| **Analyst** | Statistik nach Ausstiegsgrund und Score, Vorschläge für die Einstellungen |

## Web-Oberfläche (Port 8080)

* **Übersicht:** Kapital, Rendite, Trefferquote, Profit-Faktor, Drawdown, Kapitalverlauf, offene Positionen
* **Trades:** alle abgeschlossenen Trades; Antippen zeigt, *warum* gekauft wurde, Sicherheitswerte und alle Ausführungen
* **Agenten:** Watchlist mit Entscheidung und Grund je Coin, Log aller Agenten
* **Analyse:** Auswertung + Vorschläge
* **Steuerung:** Pausieren, Fortsetzen, Not-Aus (alles verkaufen)

Login über `WEB_USER`/`WEB_PASSWORD`. Die Oberfläche nicht offen ins Internet stellen – Zugriff am besten über Tailscale/WireGuard.

## Installation im Proxmox-LXC (systemd, ohne Docker)

Debian 12 oder Ubuntu 22.04+ (Python ≥ 3.11). Als root:

```bash
apt-get install -y git
git clone https://github.com/rush0815/claude-meme-trade /opt/memebot   # privates Repo: vorher gh auth login oder Deploy-Key
bash /opt/memebot/deploy/install.sh
nano /etc/memebot.env          # Schlüssel + Web-Passwort eintragen
systemctl restart memebot
journalctl -u memebot -f       # Logs
```

Update: `bash /opt/memebot/deploy/update.sh`

## APIs

| Dienst | Wofür | Kosten |
|---|---|---|
| DexScreener | Kurse, Liquidität, Volumen, neue Listings | kostenlos |
| RugCheck | Sicherheitsbericht | kostenlos |
| PumpPortal (WebSocket) | pump.fun-Graduations | kostenlos |
| Jupiter | Quotes, Verkaufstest, Swaps | Free-Key reicht (portal.jup.ag) |
| Helius (RPC) | Dezimalstellen, Kontostand, Transaktionen senden | Free für Paper, **Developer (49 $/Monat)** für Live |

## Live schalten (erst nach erfolgreichem Papiermodus)

1. Neue Wallet nur für den Bot anlegen, nur so viel SOL einzahlen, wie du verlieren kannst.
2. `SOLANA_PRIVATE_KEY` (Base58), `HELIUS_API_KEY` und `JUPITER_API_KEY` in `/etc/memebot.env` eintragen.
3. In `config.toml` `mode = "live"` setzen, `systemctl restart memebot`.

Papier- und Live-Trades werden getrennt gespeichert.

## Entwickeln / Testen

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                       # Tests (laufen auch automatisch per GitHub Actions)
python -m memebot --simulate              # Demo mit simuliertem Markt, ohne Internet
python -m memebot                         # Papiermodus mit echten Daten
```

**GitHub Codespaces:** Repo öffnen → *Code → Codespaces → Create*. Die Umgebung installiert alles
automatisch; `python -m memebot` starten und den weitergeleiteten Port 8080 öffnen.
Der Simulationsmodus zeigt nur, dass alles funktioniert – über die echte Profitabilität sagt er nichts.
