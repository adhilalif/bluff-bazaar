# Bluff Bazaar — Game Rules (MVP)

These rules are the specification for `bluff/engine.py`. Every rule below has a
unit test in `tests/test_engine.py`. All defaults live in `bluff/config.py`
(`GameConfig`) and are copied into each room when it is created, so a room's
rules never change mid-game.

## Setup

| Setting | Default |
|---|---|
| Players per room | exactly 4 |
| Rounds | 8 |
| Starting coins | 100 |
| Inventory limit | 10 units total (all commodities combined) |
| Max units per trade | 5 |
| Cards per hand | 3 (dealt from a 12-card deck: 2 copies of each card) |
| Turn timer | 90 seconds per round |

Commodities and default starting prices (configurable):

| | Commodity | Start price | Event move (±) |
|---|---|---|---|
| 💎 | Crystals | 20 | 5 |
| 🌶️ | Spices | 10 | 3 |
| ⚡ | Energy | 15 | 4 |
| ⚙️ | Metals | 12 | 3 |
| 🏺 | Relics | 25 | 6 |

Event move = `max(2, round(start_price × 25%))`.

When the game starts, the server (never a browser) uses the room's random seed to
shuffle and deal the hands and to draw the 8 market events (without replacement)
from the 13-event deck. The schedule is stored in a private table and is never
sent to any browser before it is revealed.

## Market events

Each round has one hidden event. At the start of the round every player sees
only its **rumor**; the true event is revealed at resolution (or earlier, to a
player who plays Insider Tip). Rumors are deliberately ambiguous:

| Event | Rumor | Effect at resolution |
|---|---|---|
| *X* Boom (one per commodity) | "Traders are whispering about *X*…" | *X* price + event move |
| *X* Bust (one per commodity) | same rumor as Boom | *X* price − event move |
| Calm Day | "The bazaar feels oddly quiet…" | nothing |
| Trade Festival | same | +4 coins to every player whose trade executed |
| Customs Inspection | same | −1 coin per unit held above 6 (after trading); Shield blocks |

## A round

1. **Event phase.** Round opens; rumor is shown; the timer starts.
2. **Secret selection.** Each player privately submits exactly one decision:
   `BUY q × commodity`, `SELL q × commodity` or `HOLD`, plus optionally one card.
   Submissions are final (locked) and idempotent.
3. **Card use.** At most one card per player per round. Insider Tip is the only
   card used *immediately* (before submitting); all other cards are attached to
   the submission and take effect during resolution.
4. **Resolution.** Happens exactly once, as soon as all 4 players have a
   submission. If the timer expires, every missing player gets an automatic
   `HOLD` with no card and the round resolves.

### Submission validation (server-side, at submit time)

* action ∈ {buy, sell, hold}; buy/sell need a valid commodity and an integer
  quantity 1–5; hold has neither.
* **Buy:** `quantity × current price ≤ coins` (no credit, no negative balance)
  and `units held + quantity ≤ 10`.
* **Sell:** you must hold at least `quantity` units.
* Card must be in your hand and unplayed; only one card per round.
* Market Pump / Crash need a target commodity; Tax Collector needs an opponent;
  Double Down needs a buy or sell (not hold); Insider Tip cannot be attached to
  a submission (use it first).
* Submissions for any round other than the current open round are rejected
  (stale browser tab).

### Resolution order (exact)

Let `P0` be the prices shown during the round.

1. **Trade settlement** (seat order). Every trade settles with the bank at `P0`.
   Trades are re-validated; a trade that is no longer valid becomes a failed
   trade (no effect). Players do not trade with each other, so order does not
   change outcomes.
2. **Price movement**, per commodity:
   * event delta (Boom/Bust),
   * card delta: each Market Pump `+max(2, round(P0 × 25%))`, each Market Crash
     `−max(2, round(P0 × 25%))`,
   * demand delta: `net = units bought − units sold` (executed trades only);
     `sign(net) × min(4, |net| // 2)`.
   * The summed delta is clamped to `± max(3, P0 × 40%)` (bounded movement),
     then the new price `P1` is clamped to `[2, 99]`.
3. **Double Down.** If the player's trade executed: `swing = (P1 − P0) × quantity`,
   positive for buys, negated for sells. Gains are paid by the bank; losses are
   taken from coins but never below 0. If the trade failed, the card is spent
   with no effect.
4. **Shield compensation.** For each commodity targeted by a Market Crash card,
   a shielded player receives `units held × min(actual drop, crash amount)`.
5. **Event coin effects.** Trade Festival bonus; Customs Inspection fee (capped
   at the player's coins; Shield blocks).
6. **Tax Collector** (in the collector's seat order): takes
   `min(15, target coins × 15%)` (rounded down) from the target and gives it to
   the collector. Shield blocks it. You cannot target yourself.
7. **Net worth snapshot:** `coins + Σ units × P1`. `P1` becomes the next
   round's price. The event and every submission become public in the round
   summary (Insider Tip is shown as used, but not what it revealed).

## The six cards

| Card | When | Effect |
|---|---|---|
| 📈 Market Pump | with submission, target commodity | +max(2, 25% of price) |
| 📉 Market Crash | with submission, target commodity | −max(2, 25% of price) |
| 🛡️ Shield | with submission | Immune to Tax Collector and Customs; compensated for crash losses |
| 🔍 Insider Tip | immediately | Privately reveals this round's true event |
| 💰 Tax Collector | with submission, target opponent | Steal min(15, 15% of their coins) |
| 🎲 Double Down | with submission (needs a trade) | Your trade's price swing is paid/charged again |

Unplayed cards are worth nothing at the end.

## Final scoring

After round 8 resolves: `net worth = coins + Σ units × final price`.
Ranking: net worth (desc), then coins (desc). Players still tied share the rank
(1, 1, 3, 4) — all players at rank 1 are winners.

## Disconnects, timeouts and inactive rooms

* Every client polls every 2 s and sends a heartbeat at most every 10 s.
  A player not seen for 30 s is shown as *offline* to others.
* **Lobby:** a player not seen for 2 minutes is removed from the lobby so the
  seat can be filled. If the host leaves, the lowest seat becomes host.
* **In game:** seats are never taken away. A disconnected player keeps coins,
  inventory and cards. When the 90 s timer expires, any connected client
  triggers resolution and missing players get an automatic HOLD.
* **Rejoin:** the browser URL carries `?room=CODE&t=TOKEN`. Reopening that URL
  (same or another device) resumes the same seat. Without the token you cannot
  act as that player.
* **Inactive rooms:** a room in the lobby or in progress with no activity for
  30 minutes is marked *abandoned* and becomes read-only.
