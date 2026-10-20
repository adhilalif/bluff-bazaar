"""Bluff Bazaar — Streamlit UI.

The UI never computes authoritative state. It renders `service.get_view()` and
sends player intent to `service.*`, which validates everything server-side.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

from bluff import service, ui
from bluff.config import CARD_INFO, COMMODITIES, COMMODITY_INFO, DEFAULT_STARTING_PRICES, GameConfig, event_info
from bluff.db import Database, apply_migrations, resolve_database_url
from bluff.engine import CardPlay, InvalidAction, PlayerState, Submission, validate_submission
from bluff.service import AuthError, GameError, StaleError

POLL_SECONDS = 2
HEARTBEAT_SECONDS = 10

PRESETS: dict[str, dict[str, int]] = {
    "Classic": dict(DEFAULT_STARTING_PRICES),
    "Bargain Bazaar": {"crystals": 12, "spices": 6, "energy": 9, "metals": 8, "relics": 15},
    "High Rollers": {"crystals": 30, "spices": 16, "energy": 22, "metals": 18, "relics": 40},
}

st.set_page_config(page_title="Bluff Bazaar", page_icon="🏺", layout="centered",
                   initial_sidebar_state="collapsed")
st.markdown(ui.CSS, unsafe_allow_html=True)
ss = st.session_state


def _secret(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        try:
            value = st.secrets.get(name)
        except Exception:  # noqa: BLE001 - no secrets file
            value = None
    return str(value) if value is not None else default


@st.cache_resource(show_spinner="Connecting to the bazaar…")
def get_db() -> Database:
    url = resolve_database_url()
    if _secret("AUTO_MIGRATE", "true").lower() == "true":
        apply_migrations(url)
    return Database(url)


# ---------------------------------------------------------------- session

def current_session() -> tuple[Optional[str], Optional[str]]:
    qp = st.query_params
    if qp.get("room") and qp.get("t"):
        ss["code"], ss["token"] = service.normalize_code(qp["room"]), qp["t"]
    return ss.get("code"), ss.get("token")


def enter_room(code: str, token: str) -> None:
    ss["code"], ss["token"] = code, token
    st.query_params.clear()
    st.query_params.update({"room": code, "t": token})
    st.rerun()


def exit_room() -> None:
    for key in ("code", "token", "last_touch", "seen_round", "celebrated"):
        ss.pop(key, None)
    st.query_params.clear()
    st.rerun()


def request_id(kind: str, code: str, round_no: int) -> str:
    """One id per (kind, room, round): retries and double clicks reuse it, so the server applies it once."""
    key = f"req:{kind}:{code}:{round_no}"
    if key not in ss:
        ss[key] = str(uuid.uuid4())
    return ss[key]


def heartbeat(db: Database, code: str, token: str, force: bool = False) -> None:
    now = time.time()
    if force or now - ss.get("last_touch", 0) >= HEARTBEAT_SECONDS:
        ss["last_touch"] = now
        service.touch(db, code, token)


# ------------------------------------------------------------------- home

def home(db: Database) -> None:
    st.markdown(ui.title_html(), unsafe_allow_html=True)
    join_code = service.normalize_code(st.query_params.get("join", ""))
    create_tab, join_tab = st.tabs(["🎪 Create room", "🔑 Join room"])
    if join_code:
        st.info(f"Invited to room **{join_code}** — open the *Join room* tab.")
    with create_tab:
        with st.form("create"):
            name = st.text_input("Your name", max_chars=20, placeholder="e.g. Saffron Sam")
            preset = st.selectbox("Market", list(PRESETS), help="Starting prices for the five commodities.")
            timer = st.select_slider("Turn timer", options=[45, 60, 90, 120, 180], value=90,
                                     format_func=lambda s: f"{s} s")
            if st.form_submit_button("Create room", type="primary", use_container_width=True):
                try:
                    cfg = GameConfig(starting_prices=dict(PRESETS[preset]), turn_seconds=int(timer))
                    code, token = service.create_room(db, name, cfg)
                except (GameError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    enter_room(code, token)
    with join_tab:
        with st.form("join"):
            code = st.text_input("Room code", value=join_code, max_chars=6, placeholder="ABC123")
            name = st.text_input("Your name", max_chars=20, key="join_name")
            if st.form_submit_button("Join room", type="primary", use_container_width=True):
                try:
                    token = service.join_room(db, code, name)
                except GameError as exc:
                    st.error(str(exc))
                else:
                    enter_room(service.normalize_code(code), token)
    with st.expander("📜 How to play"):
        st.markdown(
            "- **4 players, 8 rounds**, everyone starts with **100 🪙** and 3 secret action cards.\n"
            "- Each round a **rumor** hints at a hidden market event. Secretly choose **one** trade "
            "(buy, sell or hold) and optionally **one card**. Trades settle at the shown price, "
            "then prices move from the event, cards and total demand.\n"
            "- Inventory holds up to **10 units**. Highest **net worth** (coins + goods) after round 8 wins.\n"
            "- Cards: 📈 Pump · 📉 Crash · 🛡️ Shield · 🔍 Insider Tip · 💰 Tax Collector · 🎲 Double Down.")


# ----------------------------------------------------------------- poller

@st.fragment(run_every=POLL_SECONDS)
def poller(db: Database, code: str, token: str, version: int, deadline_ts: Optional[float] = None,
           chips: str = "", progress: Optional[float] = None) -> None:
    """Short-interval polling: heartbeat, timeout resolution, and rerun on any room change.

    Also renders the round header so the countdown ticks without rerunning the whole page.
    """
    try:
        if deadline_ts and time.time() >= deadline_ts:
            service.resolve_if_expired(db, code)
        heartbeat(db, code, token)
        latest = service.get_version(db, code)
    except GameError:
        latest = None
    if latest is not None and latest != version:
        st.rerun(scope="app")
    if deadline_ts:
        left = max(0, int(deadline_ts - time.time()))
        urgent = " gold" if left <= 15 else ""
        chips += f'<span class="bb-chip{urgent}">⏱️ {left // 60}:{left % 60:02d}</span>'
    if chips:
        st.markdown(f'<div class="bb-row" style="justify-content:flex-start">{chips}</div>', unsafe_allow_html=True)
    if progress is not None:
        st.progress(min(1.0, progress))


# ------------------------------------------------------------------ lobby

def lobby(db: Database, code: str, token: str, view: dict) -> None:
    st.markdown(ui.title_html(), unsafe_allow_html=True)
    st.markdown(f'<div class="bb-panel"><div class="bb-muted" style="text-align:center">ROOM CODE</div>'
                f'<div class="bb-code">{ui.esc(code)}</div><div class="bb-muted" style="text-align:center">'
                f'Share the code (or <code>?join={ui.esc(code)}</code>). Never share your own page URL — '
                f'it contains your private seat key.</div></div>', unsafe_allow_html=True)
    players = view["players"]
    st.markdown(f"**Players {len(players)}/4**")
    st.markdown(ui.players_html(players, {}, in_game=False), unsafe_allow_html=True)
    for _ in range(4 - len(players)):
        st.markdown('<div class="bb-player"><div class="st">Waiting for a trader…</div></div>',
                    unsafe_allow_html=True)
    me = view["me"]
    c1, c2 = st.columns([2, 1])
    with c1:
        label = "✋ Not ready" if me["is_ready"] else "✅ I'm ready"
        if st.button(label, type="secondary" if me["is_ready"] else "primary", use_container_width=True):
            act(lambda: service.set_ready(db, code, token, not me["is_ready"]))
    with c2:
        if st.button("Leave", use_container_width=True):
            try:
                service.leave_room(db, code, token)
            except GameError as exc:
                st.error(str(exc))
            else:
                exit_room()
    st.caption("The game starts automatically when all four traders are ready.")
    cfg = view["config"]
    st.markdown(
        '<div class="bb-panel"><div class="bb-muted">Starting prices · '
        f'{cfg["turn_seconds"]} s turns · {cfg["num_rounds"]} rounds</div>'
        + "".join(f'<span class="bb-chip">{COMMODITY_INFO[c]["icon"]} {cfg["starting_prices"][c]}</span>'
                  for c in COMMODITIES) + "</div>", unsafe_allow_html=True)


def act(fn) -> None:
    """Run a service call, show friendly errors, and rerun on success."""
    try:
        fn()
    except StaleError as exc:
        st.toast(str(exc), icon="🔄")
        st.rerun()
    except GameError as exc:
        st.error(str(exc))
    else:
        st.rerun()


# ------------------------------------------------------------------- game

def game(db: Database, code: str, token: str, view: dict) -> None:
    me, r, n = view["me"], view["room"]["round"], view["room"]["num_rounds"]
    cur = view["current"]
    names = {p["player_id"]: p["name"] for p in view["players"]}
    scores = {row["player_id"]: row["net_worth"] for row in (view["summaries"][-1]["players"]
                                                             if view["summaries"] else [])}

    if ss.get("seen_round") not in (None, r) and view["summaries"]:
        ev = event_info(view["summaries"][-1]["event"])
        st.toast(f"Round {view['summaries'][-1]['round']} resolved: {ev['icon']} {ev['name']}", icon="📣")
    ss["seen_round"] = r

    chips = (f'<span class="bb-chip gold">Round {r}/{n}</span>'
             f'<span class="bb-chip">Room {ui.esc(code)}</span>'
             f'<span class="bb-chip teal">{cur["submitted_count"]}/4 locked</span>')
    poller(db, code, token, view["room"]["version"], time.time() + cur["seconds_left"], chips,
           (r - 1 + cur["submitted_count"] / 4) / n)

    st.markdown(f'<div class="bb-rumor"><b>📰 Rumor:</b> {ui.esc(cur["rumor"])}</div>', unsafe_allow_html=True)
    tip = next((x for x in me["private_log"] if x["kind"] == "insider_tip" and x["round"] == r), None)
    if tip:
        ev = event_info(tip["event"])
        st.markdown(f'<div class="bb-rumor" style="border-color:var(--bb-teal)"><b style="color:var(--bb-teal)">'
                    f'🔍 Insider:</b> the real event is <b>{ev["icon"]} {ui.esc(ev["name"])}</b> — '
                    f'{ui.esc(ev["text"])}</div>', unsafe_allow_html=True)

    history = view["price_history"]
    previous = history[-2] if len(history) >= 2 else None
    st.markdown(ui.market_html(view["prices"], previous, me["inventory"]), unsafe_allow_html=True)

    units = sum(me["inventory"].values())
    limit = view["config"]["inventory_limit"]
    st.markdown(ui.stats_html([(f"{me['coins']} 🪙", "Coins"), (me["net_worth"], "Net worth"),
                               (f"{units}/{limit}", "Inventory")]), unsafe_allow_html=True)

    if me["submission"]:
        locked_panel(view)
    else:
        action_panel(db, code, token, view)

    st.markdown("**Traders**")
    st.markdown(ui.players_html(view["players"], scores, in_game=True), unsafe_allow_html=True)

    t1, t2, t3 = st.tabs(["📣 Last round", "📈 Market history", "🃏 My cards"])
    with t1:
        if view["summaries"]:
            st.markdown(ui.summary_html(view["summaries"][-1], names), unsafe_allow_html=True)
            if len(view["summaries"]) > 1:
                with st.expander("Earlier rounds"):
                    for s in reversed(view["summaries"][:-1]):
                        st.markdown(f"**Round {s['round']}**")
                        st.markdown(ui.summary_html(s, names), unsafe_allow_html=True)
        else:
            st.caption("Round summaries appear here after each resolution.")
    with t2:
        price_chart(history)
    with t3:
        st.markdown(ui.cards_html(me["hand"]), unsafe_allow_html=True)


def locked_panel(view: dict) -> None:
    sub = view["me"]["submission"]
    names = {p["player_id"]: p["name"] for p in view["players"]}
    if sub["action"] == "hold":
        text = "Hold"
    else:
        text = f'{sub["action"].title()} {sub["quantity"]} × {ui.commodity_label(sub["commodity"])}'
    if sub["card_type"]:
        target = ui.commodity_label(sub["target_commodity"]) or names.get(sub["target_player_id"], "")
        text += f' + {ui.card_label(sub["card_type"])}' + (f" → {ui.esc(target)}" if target else "")
    waiting = [p["name"] for p in view["players"] if not p["submitted"]]
    st.markdown(f'<div class="bb-locked"><div style="font-size:1.1rem">🔒 Locked in: <b>{ui.esc(text)}</b></div>'
                f'<div class="bb-muted">Waiting for {ui.esc(", ".join(waiting)) or "resolution"}…</div></div>',
                unsafe_allow_html=True)


def action_panel(db: Database, code: str, token: str, view: dict) -> None:
    me, r, cfg = view["me"], view["room"]["round"], view["config"]
    prices = view["prices"]
    st.markdown("#### Your secret move")

    action = st.segmented_control(
        "Action", ["buy", "sell", "hold"], default="hold", key=f"act_{r}",
        format_func=lambda a: {"buy": "🛒 Buy", "sell": "💸 Sell", "hold": "✋ Hold"}[a]) or "hold"
    commodity, qty = None, 0
    if action != "hold":
        options = list(COMMODITIES) if action == "buy" else [c for c in COMMODITIES if me["inventory"][c] > 0]
        if not options:
            st.info("You have nothing to sell yet.")
        else:
            commodity = st.segmented_control(
                "Commodity", options, default=options[0], key=f"com_{r}_{action}",
                format_func=lambda c: f'{COMMODITY_INFO[c]["icon"]} {COMMODITY_INFO[c]["label"]}')
            qty = st.segmented_control("Quantity", list(range(1, cfg["max_trade_qty"] + 1)), default=1,
                                       key=f"qty_{r}_{action}") or 1

    # --- cards
    card_id, target_commodity, target_seat = None, None, None
    playable = [c for c in me["hand"] if c["card_type"] != "insider_tip"]
    insider = next((c for c in me["hand"] if c["card_type"] == "insider_tip"), None)
    if me["card_played_this_round"]:
        st.caption(f"You already used {ui.card_label(me['card_played_this_round'])} this round.")
    else:
        if insider:
            if st.button("🔍 Use Insider Tip now (reveals the true event to you only)", use_container_width=True):
                act(lambda: service.use_insider_tip(db, code, token, r, insider["card_id"],
                                                    request_id("tip", code, r)))
        if playable:
            by_id: dict[str, dict] = {}
            for c in playable:  # one option per card type (duplicates are interchangeable)
                if c["card_type"] not in {x["card_type"] for x in by_id.values()}:
                    by_id[c["card_id"]] = c
            card_id = st.radio("Play a card (optional)", [None] + list(by_id), key=f"card_{r}", horizontal=True,
                               format_func=lambda cid: "No card" if cid is None else ui.card_label(by_id[cid]["card_type"]))
            if card_id:
                info = CARD_INFO[by_id[card_id]["card_type"]]
                st.caption(f'{info["icon"]} {info["text"]}')
                if info["target"] == "commodity":
                    target_commodity = st.segmented_control(
                        "Target commodity", list(COMMODITIES), default=COMMODITIES[0], key=f"tc_{r}",
                        format_func=lambda c: f'{COMMODITY_INFO[c]["icon"]} {COMMODITY_INFO[c]["label"]}')
                elif info["target"] == "player":
                    opponents = {p["seat"]: p["name"] for p in view["players"] if not p["is_me"]}
                    target_seat = st.radio("Target trader", list(opponents), key=f"ts_{r}", horizontal=True,
                                           format_func=lambda s: opponents[s])

    # --- client-side preview (the server re-validates everything)
    error = preview(view, action, commodity, qty, card_id, target_commodity, target_seat)
    if action != "hold" and commodity and not error:
        total = qty * prices[commodity]
        after = me["coins"] - total if action == "buy" else me["coins"] + total
        st.markdown(f'<div class="bb-muted">{qty} × {prices[commodity]} = <b>{total} 🪙</b> · '
                    f'coins after trade: <b>{after}</b></div>', unsafe_allow_html=True)
    if error:
        st.warning(error)
    if st.button("🔒 Lock in move", type="primary", use_container_width=True, disabled=bool(error)):
        act(lambda: service.submit_action(
            db, code, token, r, request_id("move", code, r), action, commodity, qty, card_id,
            target_commodity, target_seat))


def preview(view, action, commodity, qty, card_id, target_commodity, target_seat) -> Optional[str]:
    me = view["me"]
    if action != "hold" and not commodity:
        return "Choose a commodity."
    player = PlayerState(me["player_id"], me["seat"], me["name"], me["coins"], me["inventory"])
    by_id = {c["card_id"]: c for c in me["hand"]}
    seats = {p["seat"]: p["player_id"] for p in view["players"]}
    card = None
    if card_id:
        card = CardPlay(by_id[card_id]["card_type"], target_commodity,
                        seats.get(target_seat) if target_seat is not None else None)
    sub = Submission(action, commodity if action != "hold" else None, qty if action != "hold" else 0, card)
    cfg = GameConfig(starting_prices=view["config"]["starting_prices"], inventory_limit=view["config"]["inventory_limit"],
              max_trade_qty=view["config"]["max_trade_qty"])
    try:
        validate_submission(cfg, player, view["prices"], sub, [c["card_type"] for c in me["hand"]],
                            [pid for pid in seats.values() if pid != me["player_id"]],
                            bool(me["card_played_this_round"]))
    except InvalidAction as exc:
        return str(exc)
    return None


def price_chart(history: list[dict]) -> None:
    if len(history) < 2:
        st.caption("Price history builds up as rounds resolve.")
        return
    df = pd.DataFrame(history).melt(id_vars="round", var_name="commodity", value_name="price")
    df["commodity"] = df["commodity"].map(lambda c: COMMODITY_INFO[c]["label"])
    line_chart(df, "round", "price", "commodity")


def line_chart(df: pd.DataFrame, x: str, y: str, series: str) -> None:
    chart = (alt.Chart(df).mark_line(point=True, strokeWidth=2.5)
             .encode(x=alt.X(f"{x}:O", title="Round", axis=alt.Axis(labelAngle=0)),
                     y=alt.Y(f"{y}:Q", title=None, scale=alt.Scale(zero=False)),
                     color=alt.Color(f"{series}:N", title=None, legend=alt.Legend(orient="bottom")),
                     tooltip=[x, series, y])
             .properties(height=260))
    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------------- results

def results(view: dict) -> None:
    res = view["results"]
    winners = [r for r in res if r["rank"] == 1]
    me = view["me"]
    if not ss.get("celebrated"):
        st.balloons()
        ss["celebrated"] = True
    st.markdown(ui.title_html(), unsafe_allow_html=True)
    names = " & ".join(w["name"] for w in winners)
    st.markdown(f'<div class="bb-winner">🏆 {ui.esc(names)} {"win" if len(winners) > 1 else "wins"} the bazaar!</div>',
                unsafe_allow_html=True)
    if any(w["player_id"] == me["player_id"] for w in winners):
        st.success("That's you! Masterful bluffing. 🎉")
    st.markdown(ui.podium_html(res), unsafe_allow_html=True)
    st.dataframe(
        pd.DataFrame([{"Rank": r["rank"], "Trader": r["name"], "Net worth": r["net_worth"], "Coins": r["coins"],
                       "Goods value": r["inventory_value"]} for r in res]),
        hide_index=True, use_container_width=True)

    if view["summaries"]:
        rows = [{"round": s["round"], "trader": p["name"], "net worth": p["net_worth"]}
                for s in view["summaries"] for p in s["players"]]
        st.markdown("**Net worth by round**")
        line_chart(pd.DataFrame(rows), "round", "net worth", "trader")
    with st.expander("📈 Market history"):
        price_chart(view["price_history"])
    with st.expander("📣 Every round"):
        pnames = {p["player_id"]: p["name"] for p in view["players"]}
        for s in view["summaries"]:
            st.markdown(f"**Round {s['round']}**")
            st.markdown(ui.summary_html(s, pnames), unsafe_allow_html=True)
    if st.button("🎪 Back to the bazaar", type="primary", use_container_width=True):
        exit_room()


# ------------------------------------------------------------------- main

def room_page(db: Database, code: str, token: str) -> None:
    try:
        heartbeat(db, code, token)
        view = service.get_view(db, code, token)
    except AuthError:
        st.markdown(ui.title_html(), unsafe_allow_html=True)
        st.warning("This browser session is no longer valid for that room (you left, were removed after "
                   "being idle in the lobby, or the link is wrong).")
        if st.button("Back to the bazaar", type="primary"):
            exit_room()
        return
    except GameError as exc:
        st.error(str(exc))
        if st.button("Back to the bazaar"):
            exit_room()
        return

    status = view["room"]["status"]
    if status == "lobby":
        lobby(db, code, token, view)
        poller(db, code, token, view["room"]["version"], None)
    elif status == "active":
        game(db, code, token, view)
    elif status == "finished":
        results(view)
    else:
        st.markdown(ui.title_html(), unsafe_allow_html=True)
        st.info("This room was closed after 30 minutes of inactivity.")
        if st.button("Back to the bazaar", type="primary"):
            exit_room()


def main() -> None:
    try:
        db = get_db()
    except Exception as exc:  # noqa: BLE001 - show setup help instead of a stack trace
        st.markdown(ui.title_html(), unsafe_allow_html=True)
        st.error("Could not connect to the database. Set `DATABASE_URL` in `.streamlit/secrets.toml` "
                 "(see README).")
        st.caption(type(exc).__name__)
        return
    code, token = current_session()
    if code and token:
        room_page(db, code, token)
    else:
        home(db)


main()
