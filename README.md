# 🏺 Bluff Bazaar

A four-player online market-bluffing game built with **Python + Streamlit + Supabase Postgres**.
Eight rounds, 15–25 minutes. Read the rumor, trade in secret, play sneaky cards, and finish with the
highest net worth.

* Exactly 4 players per room, joined with a six-character room code
* 5 commodities (💎 crystals, 🌶️ spices, ⚡ energy, ⚙️ metals, 🏺 relics), configurable starting prices,
  bounded price movement, 10-unit inventory limit
* 6 cards: 📈 Market Pump, 📉 Market Crash, 🛡️ Shield, 🔍 Insider Tip, 💰 Tax Collector, 🎲 Double Down
* Simultaneous secret moves, resolved together once all four are locked in (or the timer runs out)
* Server-authoritative engine, atomic transactions, idempotent submissions, hidden information never
  sent to other players' browsers
* Mobile-first dark navy / teal / gold UI

Full rules: [docs/RULES.md](docs/RULES.md) · Architecture and security model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Repository layout

```
app.py                      Streamlit UI (home, lobby, game, results)
bluff/config.py             GameConfig, commodities, cards, events
bluff/engine.py             Pure, deterministic rules (no I/O)
bluff/service.py            Trusted server-side operations (auth, locking, validation, persistence, views)
bluff/db.py                 Connection pool, transactions, migration runner
bluff/ui.py                 CSS theme + HTML snippets (all user text escaped)
migrations/001_init.sql     Schema (idempotent)
migrations/optional/        Optional least-privilege database role
scripts/migrate.py          Apply migrations from the command line
tests/                      Engine unit tests, DB integration tests, Streamlit AppTest UI tests
.streamlit/config.toml      Theme
.streamlit/secrets.toml.example
.github/workflows/tests.yml CI: runs every test against Postgres 16
```

## 1. Create the Supabase project

1. Sign in at <https://supabase.com> → **New project**. Pick a region near your players and save the
   **database password** somewhere safe (a password manager).
2. When the project is ready, click **Connect** (top bar) → **Connection string** → choose
   **Session pooler**. Copy the URI. It looks like
   `postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Replace `[YOUR-PASSWORD]` and append `?sslmode=require`.
   *Use the pooler, not the "Direct connection": the direct host is IPv6-only and Streamlit Community
   Cloud needs IPv4.*
3. You do **not** need the anon key or the service-role key — the app never uses the Supabase REST API.

## 2. Apply the schema

Pick one:

* **Automatic (default):** the app runs pending migrations on startup (`AUTO_MIGRATE = "true"`).
* **SQL editor:** Supabase dashboard → **SQL Editor** → paste `migrations/001_init.sql` → **Run**.
* **Command line:** `DATABASE_URL="postgresql://…" python scripts/migrate.py`

The tables live in a private schema `bluff`. **Do not** add `bluff` to *Project Settings → Data API →
Exposed schemas*; leaving it out means the anon key cannot reach the game data at all. RLS is also
enabled on every table with no policies for `anon` / `authenticated`.

## 3. Configure secrets

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # git-ignored
# edit DATABASE_URL in .streamlit/secrets.toml
```

`DATABASE_URL` can also be supplied as an environment variable. Optional: `AUTO_MIGRATE = "false"`.

## 4. Run locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
streamlit run app.py
```

Open <http://localhost:8501>. To try a full game alone, open four browser windows (use private/incognito
windows or different browsers so each gets its own session), create a room in one and join with the
code in the others.

## 5. Run the tests

Engine unit tests need nothing:

```bash
python -m pytest
```

Integration and UI tests need a **disposable** Postgres (they truncate the game tables — never point them
at your real Supabase database):

```bash
docker run -d --name bluff-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=bluff_test -p 5432:5432 postgres:16
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bluff_test
python -m pytest
```

| Suite | Count | Covers |
|---|---|---|
| `tests/test_engine.py` | 61 | every rule: validation, funds, inventory, price bounds (incl. fuzz), each card, events, ordering, scoring, ties, a full scripted game |
| `tests/test_service.py` | 58 | lobby, auth, idempotency, duplicate & concurrent submissions, stale rounds, invalid trades, secret isolation, RLS, timeouts, disconnect/rejoin, inactivity, full 8-round game |
| `tests/test_app.py` | 8 | the real `app.py` driven by Streamlit `AppTest`: create/join, a full round, funds check, hidden opponent moves, stale links, rejoin, final screen |

## 6. Push to GitHub

```bash
git init -b main                 # already done if you cloned this repo
git add -A
git status                       # confirm .streamlit/secrets.toml is NOT listed
git commit -m "Bluff Bazaar MVP"
git remote add origin https://github.com/<you>/bluff-bazaar.git   # create the empty repo on GitHub first
git push -u origin main
```

The included GitHub Actions workflow runs the full test suite against Postgres 16 on every push.

## 7. Deploy to Streamlit Community Cloud

1. <https://share.streamlit.io> → **Create app** → choose your GitHub repo, branch `main`, main file `app.py`.
2. **Advanced settings → Secrets:** paste the contents of your `secrets.toml` (the `DATABASE_URL` line).
3. **Deploy.** Share the app URL; players create or join rooms from the home screen
   (`https://<your-app>.streamlit.app/?join=ABC123` pre-fills a room code).

### Optional: least-privilege database user

`migrations/optional/least_privilege_role.sql` creates `bluff_app`, which can only read/write rows in
schema `bluff` (no DDL, no other schemas). Run it in the SQL editor after setting a password, then use
`postgresql://bluff_app.<project-ref>:<password>@…pooler.supabase.com:5432/postgres?sslmode=require` as
`DATABASE_URL` and set `AUTO_MIGRATE = "false"` (run future migrations with the owner credentials).
The integration and UI suites pass when run as this role.

## Disconnects, timeouts and inactive rooms

* Clients poll a single version number every 2 s and send a heartbeat every 10 s. Players unseen for 30 s
  show as offline.
* **Lobby:** players unseen for 2 minutes are removed so the seat can be refilled; host passes to the
  lowest seat. The game starts automatically when all 4 are ready.
* **In game:** seats are never lost. When the turn timer (45–180 s, default 90) expires, any connected
  client triggers resolution and missing players automatically **hold** with no card.
* **Rejoin:** your page URL (`?room=CODE&t=TOKEN`) restores your seat on any device. Keep it private.
* **Inactive rooms:** no heartbeat or action for 30 minutes → the room is marked *abandoned* (read-only).
  If every player disconnects mid-game, the game simply waits; the first returning player's client
  resolves the overdue round.

## Security model and known limitations

What is enforced:

* All game logic runs on the Streamlit server; browsers send only intent. Prices, balances, scores,
  player identity and permissions are always re-read from Postgres and validated by the engine.
* Every mutation is one transaction that locks the room row first; round resolution happens exactly once;
  submissions are idempotent via a per-round `request_id`; cards are spent with a guarded `UPDATE` plus a
  unique index (one card per player per round). `CHECK` constraints forbid negative coins/inventory.
* Per-player views include only that player's hand, pending move and Insider Tip reveals. Tests assert an
  opponent's view is unchanged by your pending move except for the "locked in" flag.
* Credentials exist only in Streamlit secrets; nothing Supabase-related is sent to browsers. Game tables
  are in an unexposed schema with RLS enabled and no API-role policies.

What this MVP does **not** provide (be aware before calling it production-ready):

* **No user accounts.** A seat is protected by a random bearer token in the page URL. Anyone who gets
  that URL (shared screenshot, browser history on a shared device) can play that seat. Supabase Auth
  could be added, but Streamlit has no first-class integration for it.
* **The Streamlit server is fully trusted** and holds a database credential that bypasses RLS (or the
  `bluff_app` role, which is limited to game rows). A compromise of the app host is a compromise of the game data.
* **Polling, not push.** Updates arrive within ~2 s. Supabase Realtime would need a browser-side client and
  anon key, which conflicts with keeping all data server-side.
* **No rate limiting or abuse protection** beyond Streamlit Cloud's defaults; room creation is unthrottled.
* Streamlit Community Cloud apps sleep when unused and have modest resources — fine for casual play,
  not for many concurrent rooms.
* Timeouts are enforced lazily (by polling clients), not by a background job.
