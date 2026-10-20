# ⛵ Bluff Bazaar — The Malabar Trade Game

A four-player online market-bluffing game built with **Python + Streamlit + Supabase Postgres**, themed on
Kerala's historic Malabar spice-trade harbours. Eight rounds, 15–25 minutes. Read the rumor, trade in secret,
play sneaky cards, and finish with the highest net worth.

* Exactly 4 players per room, joined with a six-character room code
* 5 goods — Thenga (coconut), Kurumulaku (black pepper), Elakka (cardamom), Chakka (jackfruit),
  Nendran (Kerala banana) — with configurable starting prices, bounded price movement, 10-unit inventory limit
* 6 cards: 📈 Vila Keri!, 📉 Vila Idinju!, 🛡️ Raksha, 🔍 Oru Karyam Parayam…, 💰 Chungam, 🎲 Iratti Labham
* Simultaneous secret moves, resolved together once all four are locked in (or the timer runs out)
* Server-authoritative engine, atomic transactions, idempotent submissions, hidden information never
  sent to other players' browsers
* Mobile-first kasavu-ivory / forest-green / antique-gold / teak UI for iPhone, iPad and desktop
* Optional English translations under every Manglish name (⚙️ Display menu; on by default)

Full rules: [docs/RULES.md](docs/RULES.md) · Architecture and security model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Repository layout

```
app.py                      Streamlit UI (home, lobby, game, results)
bluff/config.py             GameConfig and internal ids (commodities, cards, events)
bluff/theme.py              ALL player-facing names, translations, event titles, rumors, reactions
bluff/icons/                Original lightweight SVG illustrations (each under 2 KB)
bluff/engine.py             Pure, deterministic rules (no I/O)
bluff/service.py            Trusted server-side operations (auth, locking, validation, persistence, views)
bluff/db.py                 Connection pool, transactions, migration runner
bluff/ui.py                 Kerala CSS theme + HTML snippets (all user text escaped)
migrations/001_init.sql     Schema (idempotent)
migrations/optional/        Optional least-privilege database role
scripts/migrate.py          Apply migrations from the command line
tests/                      Engine, theme, DB integration and Streamlit AppTest UI tests
tests/e2e/                  Opt-in real-browser tests (Playwright + Chromium, 4 sessions, device emulation)
.streamlit/config.toml      Light Kerala theme colours + Google Fonts (DM Sans, Fraunces)
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
| `tests/test_engine.py` | 59 | every rule: validation, funds, inventory, price bounds (incl. fuzz), each card, events, ordering, scoring, ties, a full scripted game |
| `tests/test_theme.py` | 22 | every id has display text, requested names/titles, rumors stay ambiguous, ids/prices unchanged, icons valid, escaping, reactions logic |
| `tests/test_service.py` | 61 | lobby, auth, idempotency, duplicate & concurrent submissions, stale rounds, invalid trades, secret isolation, RLS, timeouts, disconnect/rejoin, inactivity, live-status pulse, full 8-round game |
| `tests/test_app.py` | 11 | the real `app.py` via Streamlit `AppTest`: create/join, a full round, funds check, hidden opponent moves, stale links, rejoin, translation toggle, timeout notice, one toast per round, final screen |
| `tests/e2e/` (opt-in) | 10 | real Chromium against a running server: 4 simultaneous browser sessions play a full game (double-tap, reconnect, secrecy, selections surviving polls); no horizontal overflow, no hidden sideways scrollers, ≥44 px touch targets and no mid-word wrapping on iPhone 13 mini / 13 / 17 Pro Max (portrait + landscape), iPad (both orientations) and desktop; 16 px inputs; visible keyboard focus |

Browser tests (slow; need Playwright and a Chromium):

```bash
pip install playwright && python -m playwright install chromium
BLUFF_E2E=1 python -m pytest tests/e2e -q        # add BLUFF_E2E_SHOTS=shots/ to save screenshots
```

These emulate iPhone/iPad screen size, pixel ratio, touch and user agent **in Chromium**. They are not a
substitute for real Safari — see "Verifying on iPhone and iPad" below.

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

## Theme and display strings

Every name players read lives in `bluff/theme.py`; edit it to rename anything. Internal ids, database values,
card effects, event probabilities and prices are unchanged — the theme is purely cosmetic.

| Internal id | Shown as | English |
|---|---|---|
| `spices` | Thenga | Coconut |
| `relics` | Kurumulaku | Black pepper |
| `crystals` | Elakka | Cardamom |
| `energy` | Chakka | Jackfruit |
| `metals` | Nendran | Kerala banana |
| `market_pump` / `market_crash` | Vila Keri! / Vila Idinju! | Price up! / Price crash! |
| `shield` / `insider_tip` | Raksha / Oru Karyam Parayam… | Protection / Let me tell you something… |
| `tax_collector` / `double_down` | Chungam / Iratti Labham | Toll / Double profit |
| `calm` | Mazha Thudangi! | Monsoon Delays |
| `festival` | Onam Vishesham | Harbour Festival |
| `customs` | Kadavil Parishodhana | Harbour Customs |
| `relics_boom` | Kurumulaku Season! | Pepper Harvest |
| other `*_boom` | Nalla Kachavadam! | Spice Route Opens |
| any `*_bust` | Vila Poyi! | Price Slump |

Reactions (at most one toast per round; can be switched off under ⚙️ Display): *Entha oru labham!* after a
gain of 10+ in one round, *Kallan pidichu!* when your Raksha blocks a Chungam, *Risk edukkua alle?* when you
lock in with Iratti Labham, and *Adipoli! Bazaarile Raja!* on the winner screen.

## Verifying on iPhone and iPad

Automated tests ran in emulated Chromium only. Please check the deployed app on real devices:

1. **Four sessions.** Open the app URL on your iPhone (Safari), iPad (Safari) and two more browsers or private
   windows. Create a room on the iPhone; join the others with the code (or the `?join=CODE` link).
2. **Lobby.** All four tap *I'm ready*; every device should switch to *Round 1/8* within about 2 seconds.
3. **iPhone portrait.** No sideways scrolling anywhere; all five goods buttons visible without swiping;
   tapping the name field does **not** zoom the page; the bottom of the page clears the home indicator.
4. **Secrecy + live status.** Lock in a Buy on the iPhone. The iPad should show your seat as ✓ within ~2 s
   while its own selections stay put, and must **not** show what you bought.
5. **Double tap.** Double-tap *Lock in move* on one device; you should see one *Move accepted* message, and the
   round summary should list one trade for that player.
6. **Rotate** the iPhone and iPad to landscape and back mid-round: layout reflows, choices are kept.
7. **Reconnect.** On the iPad, swipe Safari away (or turn on Airplane Mode for 20 s), then reopen the same tab:
   it returns to your seat with any locked-in move shown.
8. **Timeout.** Leave one player idle for the whole timer: the round settles and that player sees
   *Time ran out last round, so you automatically held*.
9. **Settings.** ⚙️ Display → turn off *Show English translations*: English lines disappear; refresh — the
   choice is kept.
10. **Finish** all 8 rounds: every device shows the same winner, *Adipoli! Bazaarile Raja!*, and on the iPhone
    the results table appears as stacked cards.

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
* **Polling, not push.** Updates arrive within ~2 s. Opponents locking in update the status strip in place;
  the whole page reruns only when the round or game state changes. Supabase Realtime would need a browser-side client and
  anon key, which conflicts with keeping all data server-side.
* **No rate limiting or abuse protection** beyond Streamlit Cloud's defaults; room creation is unthrottled.
* Streamlit Community Cloud apps sleep when unused and have modest resources — fine for casual play,
  not for many concurrent rooms.
* Timeouts are enforced lazily (by polling clients), not by a background job.
* Responsive behaviour was verified in emulated Chromium, not in Safari/WebKit. A few CSS rules target
  Streamlit's `data-testid` hooks (button groups, radios, main container); a future Streamlit release could
  change them, which would degrade spacing but not functionality. Re-run `tests/e2e` after upgrading Streamlit.
