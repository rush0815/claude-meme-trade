# Idea: Whale experiment (status: brainstorming – not implemented)

## The idea

Coins where a few wallets hold a large share of the supply (e.g. top 5 own 70 %) are normally rejected by the
rug analyst. The hypothesis: such holders want to make money, so they will push the price before they sell
("they don't rug after the first 20 %"). If the bot rides along early, this could be profitable.

## Why it is risky

- **We would be their exit liquidity.** The large holders decide when the coin dies. They need buyers – like the
  bot – to sell into, and may dump the moment volume appears, which is exactly when the bot buys.
- **The stop-loss does not protect against a block dump.** A wallet with 15–30 % of the supply selling at once
  moves the price in a single block. Normal coins already showed −41 % with a −20 % stop; with concentrated
  holders −60 % to −90 % is realistic.
- **Two look-alike cases:** an insider group that pumps first vs. bundled sniper/dev wallets that sell into the
  first wave. From the outside they are hard to tell apart.
- **Liquidity:** coins that graduate from pump.fun to PumpSwap have burned liquidity – it cannot be pulled; the
  risk is token dumping. Coins launched directly on Raydium can have liquidity pulled unless locked (the rug
  check already requires locked/burned liquidity).

## Proposed safeguards

### 1. Strict isolation (limits the damage)

| Rule | Proposal |
|---|---|
| Budget | max. 10 % of capital in whale trades in total |
| Open positions | max. 1 whale position at a time |
| Position size | half of the normal size |
| Slots | own slot – does not block the normal strategy |
| Mode | paper only; live locked until the user unlocks it after reaching a threshold (e.g. 30 trades, profit factor > 1.3) |
| Auto-off | after 3 losses in a row or −5 % of capital from whale trades; reason written to the log |

### 2. Stricter entry (only "good" concentration)

- Top 10 holders up to 70 % allowed, **but no single wallet above 25 %**.
- The **creator/dev wallet** must not be among the large holders.
- **No insider cluster:** skip if RugCheck flags the large wallets as connected (same funding source).
- **Holders must not be selling:** top-holder balances unchanged or growing in the last ~10 minutes
  (e.g. via RPC `getTokenLargestAccounts`, checked twice).
- Unchanged, non-negotiable: liquidity locked/burned, mint and freeze authority revoked, sell test passed.

### 3. Whale watch after the buy (the core of the idea)

- Subscribe via Helius WebSocket (`accountSubscribe`) to the token accounts of the top 5 holders – same
  technique as the live pool prices.
- **If any of them sells more than ~10 % of their balance → exit immediately**, without waiting for the stop-loss.
- In addition: tighter stop-loss (~ −12 %), early partial profit (sell half at +20 %), short time limit (~30 min).

**Limitation:** a single full dump in one transaction is only seen afterwards. The whale watch helps when
holders sell in steps (common, because a single large dump destroys their own exit price). The small budget
and half size are therefore the most important protection.

## Evaluation

- Tag every whale trade; own row in the analysis: number of trades, win rate, average result,
  **worst trade**, average loss on "whale sold" exits.
- Decision rule: the idea only works if the occasional −80 % trade is paid for by enough winners –
  judge after 20–30 whale trades, not earlier.

## Open questions

1. Limits OK? (10 % budget, 1 position, half size, auto-off after 3 losses)
2. Thresholds OK? (top 10 ≤ 70 %, single wallet ≤ 25 %, exit when a whale sells 10 %)
3. Live: always locked until manually unlocked (proposal), or unlocked automatically after the threshold?
4. Which data source for "connected wallets"? (RugCheck insider flags vs. own funding-source check via Helius)
