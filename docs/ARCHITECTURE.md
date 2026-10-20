# Bluff Bazaar — Architecture

```
 Phone / desktop browser                      Streamlit Community Cloud              Supabase
 ───────────────────────                      ──────────────────────────              ────────
 Streamlit frontend (React, rendered    ─WS─▶  app.py (UI, per-session state)
 by Streamlit; no game logic, no keys)          │
                                                ▼
                                            bluff/service.py  (trusted operations,
                                                │              auth by player token)
                                                ├─ bluff/engine.py (pure, deterministic rules)
                                                ▼
                                            bluff/db.py ── psycopg pool ── TLS ──▶  Postgres
                                                                                     schema "bluff"
                                                                                     (not exposed to the
                                                                                      Data API; RLS on,
                                                                                      no policies)
```

## Trust model

* **The Streamlit server is the only database client.** Browsers talk to the
  Streamlit server over its websocket; they never receive a Supabase URL, anon
  key, service-role key or connection string. All credentials live in Streamlit
  secrets (or environment variables) on the server.
* **Server-authoritative game.** Every mutating call goes through
  `bluff/service.py`, which re-derives the acting player from a secret token,
  re-reads prices/balances/inventory from the database, validates with the pure
  engine, and writes the result. Clients never supply scores, prices, balances,
  player ids or room permissions — only *intent* (e.g. "buy 2 spices", "card
  id X", "target seat 3").
* **Identity.** There are no accounts. Joining a room issues a random 192-bit
  bearer token; only its SHA-256 hash is stored. The token is kept in the
  Streamlit session and in the page URL (so refresh / rejoin works). Anyone who
  obtains that URL can play that seat — acceptable for a casual game, and
  documented as a limitation.
* **Secret information.** Hands, pending submissions, the event schedule and
  Insider Tip reveals are stored in Postgres and only ever loaded by the service
  layer. `service.get_view()` builds a per-player view that contains only the
  requesting player's private data plus public data; opponents' pending
  submissions are reduced to a boolean "has submitted". Tests assert this.

### Supabase hardening

* All tables live in a dedicated schema `bluff`. Supabase's REST/GraphQL Data
  API only exposes schemas you list (default: `public`), so the game tables are
  not reachable through the anon key at all.
* Defense in depth: Row Level Security is **enabled on every table with no
  policies**, and all privileges are revoked from `anon`/`authenticated`. Even
  if someone added `bluff` to the exposed schemas, those roles would see
  nothing.
* The app connects as the database owner over the Supabase connection pooler
  (TLS). This role bypasses RLS by design — it is a *server-side* credential,
  equivalent to the service-role key, and never leaves the Streamlit server.
  A stricter deployment can create a dedicated `bluff_app` role with only DML
  rights on schema `bluff` (see README).

### Why not Supabase Realtime / client-side Supabase?

Streamlit renders on the server; a browser-side Supabase client would require
shipping an anon key and expressing every game rule in RLS policies / RPCs.
Instead, each session polls a single integer (`rooms.version`) every 2 s from a
`st.fragment(run_every=2)` and reruns the page only when it changes. That is
one indexed primary-key read per player per 2 s — cheap and safe for 4-player
rooms.

## Consistency: atomic transitions and idempotency

* Every mutation runs in one transaction that starts with
  `SELECT … FROM bluff.rooms WHERE code = … FOR UPDATE`. All writers to a room
  serialize on that row lock, so there are no lost updates and no deadlocks
  (a single lock order).
* **Idempotent submissions.** The UI generates one `request_id` (UUID) per
  player per round and reuses it on retries / double clicks. `submissions.request_id`
  is unique; a repeated request returns the stored submission instead of
  failing or applying twice. A *different* request for an already-submitted
  round is rejected ("already locked in").
* **No duplicate card execution.** Playing a card is
  `UPDATE player_cards SET played_round = r WHERE id = … AND player_id = … AND played_round IS NULL`;
  a partial unique index `(player_id, played_round)` makes "one card per round"
  a database invariant too.
* **Exactly-once resolution.** The submission that completes the set (or the
  deadline check) resolves the round inside the same locked transaction and
  flips `rounds.status` from `open` to `resolved`; any later caller sees the
  new state and does nothing.
* Database `CHECK` constraints back up engine invariants: `coins >= 0`,
  `quantity >= 0`, `price > 0`, seat ∈ 1..4, unique seat per room (hard cap
  of four players).

## Schema (see `migrations/001_init.sql`)

| Table | Purpose | Visible to players |
|---|---|---|
| `rooms` | code, status, config, seed, current round, deadline, `version` | public fields |
| `players` | seat, name, token hash, host/ready flags, coins, last seen | name/seat/online; own coins |
| `inventories` | (player, commodity) → quantity | own only |
| `player_cards` | dealt cards, `played_round` | own hand only; played cards after resolution |
| `round_events` | secret event schedule | after resolution (or via own Insider Tip) |
| `rounds` | status, deadline, public summary JSON | summary after resolution |
| `market_prices` | price at start of each round (round 9 = final) | yes |
| `submissions` | secret trade + card per player per round | own; others after resolution |
| `private_log` | per-player private messages (Insider Tip reveals) | own only |
| `event_log` | public room history | yes |
| `results` | final ranks and net worth | yes |
| `schema_migrations` | applied migration files | — |

## Modules

| File | Responsibility |
|---|---|
| `bluff/config.py` | `GameConfig`, commodities, cards, events |
| `bluff/engine.py` | Pure functions: dealing, scheduling, validation, resolution, ranking |
| `bluff/db.py` | Connection pool, transactions, migration runner |
| `bluff/service.py` | Room lifecycle, auth by token, locking, persistence, player views |
| `bluff/ui.py` | CSS theme and HTML snippets for Streamlit |
| `app.py` | Streamlit pages: home, lobby, game, results |
