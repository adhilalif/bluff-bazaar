"""Bluff Bazaar — Streamlit UI.

The UI never computes authoritative state. It renders `service.get_view()` and
sends player intent to `service.*`, which validates everything server-side.
All player-facing names come from bluff/theme.py.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Callable, Optional

import altair as alt
import pandas as pd
import psycopg
import streamlit as st
from psycopg_pool import PoolTimeout

from bluff import service, theme, ui
from bluff.config import CARD_TARGETS, DEFAULT_STARTING_PRICES, GameConfig
from bluff.db import Database, apply_migrations, resolve_database_url
from bluff.engine import CardPlay, InvalidAction, PlayerState, Submission, validate_submission
from bluff.service import AuthError, GameError, StaleError
from bluff.theme import DISPLAY_ORDER

POLL_SECONDS = 2
HEARTBEAT_SECONDS = 10
NARROW, WIDE = "680px", "1200px"
CONNECTION_ERRORS = (psycopg.OperationalError, PoolTimeout)

PRESETS: dict[str, dict[str, int]] = {
    "Classic": dict(DEFAULT_STARTING_PRICES),
    "Bargain Bazaar": {"crystals": 12, "spices": 6, "energy": 9, "metals": 8, "relics": 15},
    "High Rollers": {"crystals": 30, "spices": 16, "energy": 22, "metals": 18, "relics": 40},
}

st.set_page_config(page_title="Bluff Bazaar · The Malabar Trade Game", page_icon="⛵", layout="wide",
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


# ------------------------------------------------------------ preferences

PREF_PARAMS = {"show_en": "en", "reactions": "rx"}


def init_prefs() -> None:
    """Display preferences: per browser, kept in the URL so they survive refreshes and rejoins."""
    for key, param in PREF_PARAMS.items():
        if key not in ss:
            ss[key] = st.query_params.get(param, "1") != "0"


def _save_prefs() -> None:
    for key, param in PREF_PARAMS.items():
        st.query_params[param] = "1" if ss[key] else "0"


def english() -> bool:
    return bool(ss.get("show_en", True))


def settings_menu() -> None:
    with st.popover("⚙️ Display"):
        st.toggle("Show English translations", key="show_en", on_change=_save_prefs,
                  help=None)
        st.caption("Manglish names stay visible; this adds English meanings under them.")
        st.toggle("Fun reactions", key="reactions", on_change=_save_prefs)
        st.caption("Short Manglish cheers after notable rounds. Never more than one per round.")


def header(big: bool = False) -> None:
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        st.markdown(ui.brand_html(big=big), unsafe_allow_html=True)
    with right:
        settings_menu()


# ---------------------------------------------------------------- session

def _pref_params() -> dict[str, str]:
    return {p: st.query_params[p] for p in PREF_PARAMS.values() if p in st.query_params}


def current_session() -> tuple[Optional[str], Optional[str]]:
    qp = st.query_params
    if qp.get("room") and qp.get("t"):
        ss["code"], ss["token"] = service.normalize_code(qp["room"]), qp["t"]
    return ss.get("code"), ss.get("token")


def enter_room(code: str, token: str) -> None:
    ss["code"], ss["token"] = code, token
    prefs = _pref_params()
    st.query_params.clear()
    st.query_params.update({"room": code, "t": token, **prefs})
    st.rerun()


def exit_room() -> None:
    for key in ("code", "token", "last_touch", "seen_round", "celebrated", "flash"):
        ss.pop(key, None)
    prefs = _pref_params()
    st.query_params.clear()
    st.query_params.update(prefs)
    st.rerun()


def request_id(kind: str, code: str, round_no: int) -> str:
    """One id per (kind, room, round): retries and double clicks reuse it, so the server applies it once."""
    key = f"req:{kind}:{code}:{round_no}"
    if key not in ss:
        ss[key] = str(uuid.uuid4())
    return ss[key]


def heartbeat(db: Database, code: str, token: str) -> None:
    now = time.time()
    if now - ss.get("last_touch", 0) >= HEARTBEAT_SECONDS:
        ss["last_touch"] = now
        service.touch(db, code, token)


def act(fn: Callable[[], object], flash: Optional[str] = None) -> None:
    """Run a service call, show friendly errors, and rerun on success."""
    try:
        fn()
    except StaleError as exc:
        st.toast(str(exc), icon="🔄")
        st.rerun()
    except GameError as exc:
        st.error(str(exc))
    except CONNECTION_ERRORS:
        st.error("Connection problem — nothing was lost on your side. It is safe to tap the button again.")
    else:
        if flash:
            ss["flash"] = flash
        st.rerun()


# ------------------------------------------------------------------- home

def home(db: Database) -> None:
    st.markdown(ui.page_width_css(NARROW), unsafe_allow_html=True)
    header(big=True)
    st.markdown('<div class="bb-intro">Trade Thenga, Kurumulaku and Elakka on the old Malabar coast. '
                'Read the rumors, keep your moves secret, and finish richest.</div>', unsafe_allow_html=True)
    join_code = service.normalize_code(st.query_params.get("join", ""))
    if join_code:
        st.info(f"Invited to room **{join_code}** — open the *Join room* tab.")
    create_tab, join_tab = st.tabs(["⛵ Create room", "🔑 Join room"])
    with create_tab:
        with st.form("create"):
            name = st.text_input("Your name", max_chars=20, placeholder="e.g. Kunjali")
            labels = list(theme.MARKET_PRESETS)
            preset = st.selectbox("Market", labels)
            st.caption("Sets the five starting prices. Classic Kadavu is the balanced default.")
            timer = st.select_slider("Turn timer", options=[45, 60, 90, 120, 180], value=90,
                                     format_func=lambda s: f"{s} s")
            if st.form_submit_button("Create room", type="primary", use_container_width=True):
                try:
                    prices = PRESETS[theme.MARKET_PRESETS[preset]]
                    cfg = GameConfig(starting_prices=dict(prices), turn_seconds=int(timer))
                    code, token = service.create_room(db, name, cfg)
                except (GameError, ValueError) as exc:
                    st.error(str(exc))
                except CONNECTION_ERRORS:
                    st.error("Could not reach the bazaar server. Please try again.")
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
                except CONNECTION_ERRORS:
                    st.error("Could not reach the bazaar server. Please try again.")
                else:
                    enter_room(service.normalize_code(code), token)
    with st.expander("📜 How to play"):
        goods = ", ".join(theme.commodity_label(c, english=True) for c in DISPLAY_ORDER)
        cards = " · ".join(theme.card_label(t, english=english()) for t in theme.CARD_DISPLAY)
        st.markdown(
            "- **4 traders, 8 rounds**, everyone starts with **100 coins** and 3 secret cards.\n"
            f"- Goods: {goods}.\n"
            "- Each round a **rumor** hints at a hidden market event. Secretly choose **one** trade "
            "(buy, sell or hold) and optionally **one card**. Trades settle at the shown price, "
            "then prices move from the event, cards and total demand.\n"
            "- You can hold up to **10 units**. Highest **net worth** (coins + goods) after round 8 wins.\n"
            f"- Cards: {cards}.")


# ------------------------------------------------------------ live status

@st.fragment(run_every=POLL_SECONDS)
def live_status(db: Database, code: str, token: str, round_no: int, num_rounds: int,
                players: list[dict], my_seat: int, i_submitted: bool) -> None:
    """Polled every 2 s. Updates timer and lock markers in place; reruns the page only on a phase change."""
    try:
        heartbeat(db, code, token)
        pulse = service.get_pulse(db, code, token)
        if pulse["status"] == "active" and pulse["seconds_left"] <= 0:
            service.resolve_if_expired(db, code)
            pulse = service.get_pulse(db, code, token)
    except CONNECTION_ERRORS:
        st.markdown('<div class="bb-row"><span class="bb-chip warn">📡 Reconnecting to the bazaar…</span>'
                    '<span class="bb-muted">Your choices are kept.</span></div>', unsafe_allow_html=True)
        return
    except GameError:
        st.rerun(scope="app")
        return
    if (pulse["status"] != "active" or pulse["round"] != round_no
            or (my_seat in pulse["submitted"]) != i_submitted):
        st.rerun(scope="app")
    left = pulse["seconds_left"]
    n_locked = len(pulse["submitted"])
    timer_cls = "warn" if left <= 15 else "gold"
    st.markdown(
        f'<div class="bb-row"><span class="bb-chip gold">Round {round_no}/{num_rounds}</span>'
        f'<span class="bb-chip">Room {ui.esc(code)}</span>'
        f'<span class="bb-chip green">{n_locked}/4 locked in</span>'
        f'<span class="bb-chip {timer_cls}" role="timer">⏱️ {left // 60}:{left % 60:02d}</span></div>'
        + ui.progress_html((round_no - 1 + n_locked / 4) / num_rounds)
        + ui.lock_strip_html(players, pulse["submitted"], pulse["online"]),
        unsafe_allow_html=True)


@st.fragment(run_every=POLL_SECONDS)
def lobby_poller(db: Database, code: str, token: str, version: int) -> None:
    try:
        heartbeat(db, code, token)
        latest = service.get_version(db, code)
    except CONNECTION_ERRORS:
        st.markdown('<span class="bb-chip warn">📡 Reconnecting…</span>', unsafe_allow_html=True)
        return
    except GameError:
        latest = None
    if latest is not None and latest != version:
        st.rerun(scope="app")


# ------------------------------------------------------------------ lobby

def lobby(db: Database, code: str, token: str, view: dict) -> None:
    st.markdown(ui.page_width_css(NARROW), unsafe_allow_html=True)
    header()
    st.markdown(f'<div class="bb-panel kasavu"><div class="bb-muted" style="text-align:center">ROOM CODE</div>'
                f'<div class="bb-code">{ui.esc(code)}</div><div class="bb-muted" style="text-align:center">'
                f'Share the code (or the link <code>?join={ui.esc(code)}</code>). Never share your own page URL — '
                f'it contains your private seat key.</div></div>', unsafe_allow_html=True)
    players = view["players"]
    st.markdown(f'<div class="bb-h">Traders {len(players)}/4</div>', unsafe_allow_html=True)
    st.markdown(ui.players_html(players, {}, lobby=True), unsafe_allow_html=True)
    for _ in range(4 - len(players)):
        st.markdown('<div class="bb-player" style="margin-top:.45rem"><div class="st">Waiting for a trader…</div></div>',
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
    chips = "".join(f'<span class="bb-chip">{ui.esc(theme.commodity_name(c))} <b>{cfg["starting_prices"][c]}</b></span>'
                    for c in DISPLAY_ORDER)
    st.markdown(f'<div class="bb-panel"><div class="bb-muted" style="margin-bottom:.3rem">Starting prices · '
                f'{cfg["turn_seconds"]} s turns · {cfg["num_rounds"]} rounds</div><div class="bb-row">{chips}</div></div>',
                unsafe_allow_html=True)
    lobby_poller(db, code, token, view["room"]["version"])


# ------------------------------------------------------------------- game

def round_feedback(view: dict) -> None:
    """One toast per resolved round (plus the lock-in confirmation), never more."""
    me, r = view["me"], view["room"]["round"]
    result = ui.my_round_result(view["summaries"], me["player_id"], view["config"]["starting_coins"])
    flash = ss.pop("flash", None)
    if ss.get("seen_round") not in (None, r) and result:
        text = f"Round {result['round']} settled: {theme.event_title(result['event'])}"
        reaction = ui.round_reaction(result) if ss.get("reactions", True) else None
        if reaction:
            text = f"{theme.reaction(reaction, english())} · {text}"
        st.toast(text, icon="📣")
    elif flash:
        st.toast(flash, icon="🔒")
    ss["seen_round"] = r


def game(db: Database, code: str, token: str, view: dict) -> None:
    st.markdown(ui.page_width_css(WIDE), unsafe_allow_html=True)
    me, r, n = view["me"], view["room"]["round"], view["room"]["num_rounds"]
    cur = view["current"]
    en = english()
    round_feedback(view)
    header()
    live_status(db, code, token, r, n, view["players"], me["seat"], bool(me["submission"]))

    left, right = st.columns([7, 5], gap="medium")
    with left:
        result = ui.my_round_result(view["summaries"], me["player_id"], view["config"]["starting_coins"])
        if result:
            d = result["delta"]
            sign = "up" if d > 0 else ("down" if d < 0 else "flat")
            note = ('<div class="bb-muted">⏱️ Time ran out last round, so you automatically held.</div>'
                    if result["auto"] else "")
            st.markdown(f'<div class="bb-result"><b>Round {result["round"]} settled</b> · '
                        f'{ui.esc(theme.event_title(result["event"], en))}<br>Your net worth {result["before"]} → '
                        f'<b>{result["after"]}</b> <span class="{sign}">({d:+d})</span>{note}</div>',
                        unsafe_allow_html=True)
        subject = cur.get("rumor_subject")
        gloss = f' <span class="bb-en">({ui.esc(theme.commodity_en(subject).lower())})</span>' if subject and en else ""
        st.markdown(f'<div class="bb-rumor"><b>🌧️ Rumor:</b> {ui.esc(cur["rumor"])}{gloss}</div>',
                    unsafe_allow_html=True)
        tip = next((x for x in me["private_log"] if x["kind"] == "insider_tip" and x["round"] == r), None)
        if tip:
            ev = theme.event_display(tip["event"])
            st.markdown(f'<div class="bb-rumor insider"><b>🔍 {ui.esc(theme.card_name("insider_tip"))}</b> '
                        f'The real event is <b>{ui.esc(theme.event_title(tip["event"], en))}</b> — '
                        f'{ui.esc(ev["text"])}</div>', unsafe_allow_html=True)

        history = view["price_history"]
        previous = history[-2] if len(history) >= 2 else None
        st.markdown(ui.market_html(view["prices"], previous, me["inventory"], en), unsafe_allow_html=True)
        units = sum(me["inventory"].values())
        st.markdown(ui.stats_html([(me["coins"], "Coins"), (me["net_worth"], "Net worth"),
                                   (f"{units}/{view['config']['inventory_limit']}", "Goods held")]),
                    unsafe_allow_html=True)
        if me["submission"]:
            locked_panel(view)
        else:
            action_panel(db, code, token, view)

    with right:
        names = {p["player_id"]: p["name"] for p in view["players"]}
        scores = {row["player_id"]: row["net_worth"] for row in (view["summaries"][-1]["players"]
                                                                 if view["summaries"] else [])}
        st.markdown('<div class="bb-h">Traders</div>', unsafe_allow_html=True)
        st.markdown(ui.players_html(view["players"], scores, lobby=False), unsafe_allow_html=True)
        t1, t2, t3 = st.tabs(["📣 Last round", "📈 Prices", "🃏 My cards"])
        with t1:
            if view["summaries"]:
                st.markdown(ui.summary_html(view["summaries"][-1], names, en), unsafe_allow_html=True)
                if len(view["summaries"]) > 1:
                    with st.expander("Earlier rounds"):
                        for s in reversed(view["summaries"][:-1]):
                            st.markdown(f"**Round {s['round']}**")
                            st.markdown(ui.summary_html(s, names, en), unsafe_allow_html=True)
            else:
                st.caption("Round results appear here after each round settles.")
        with t2:
            price_chart(history)
        with t3:
            st.markdown(ui.cards_html(me["hand"], en), unsafe_allow_html=True)


def locked_panel(view: dict) -> None:
    sub = view["me"]["submission"]
    names = {p["player_id"]: p["name"] for p in view["players"]}
    if sub["action"] == "hold":
        text = "Hold"
    else:
        text = f'{sub["action"].title()} {sub["quantity"]} × {theme.commodity_name(sub["commodity"])}'
    if sub["card_type"]:
        target = (theme.commodity_name(sub["target_commodity"]) if sub["target_commodity"]
                  else names.get(sub["target_player_id"], ""))
        text += f' + {theme.card_label(sub["card_type"])}' + (f" → {target}" if target else "")
    st.markdown(f'<div class="bb-locked" role="status"><div style="font-size:1.1rem">🔒 Move accepted: <b>{ui.esc(text)}</b></div>'
                f'<div class="bb-muted">Waiting for the other traders — the round settles as soon as all four '
                f'are locked in or the timer ends.</div></div>', unsafe_allow_html=True)


def action_panel(db: Database, code: str, token: str, view: dict) -> None:
    me, r, cfg = view["me"], view["room"]["round"], view["config"]
    prices, en = view["prices"], english()
    st.markdown('<div class="bb-h">Your secret move</div>', unsafe_allow_html=True)

    # Widget keys include the round number: choices survive polling and reruns, and reset each round.
    action = st.segmented_control("Action", ["buy", "sell", "hold"], default="hold", key=f"act_{r}",
                                  format_func=lambda a: theme.ACTION_LABELS[a]) or "hold"
    commodity, qty = None, 0
    if action != "hold":
        options = list(DISPLAY_ORDER) if action == "buy" else [c for c in DISPLAY_ORDER if me["inventory"][c] > 0]
        if not options:
            st.info("You have nothing to sell yet.")
        else:
            # Pills wrap onto several lines on narrow phones (a segmented control would scroll sideways).
            commodity = st.pills("Goods", options, default=options[0], key=f"com_{r}_{action}",
                                 format_func=theme.commodity_name)
            if commodity and en:
                st.caption(f"{theme.commodity_name(commodity)} = {theme.commodity_en(commodity).lower()}")
            qty = st.segmented_control("Quantity", list(range(1, cfg["max_trade_qty"] + 1)), default=1,
                                       key=f"qty_{r}_{action}") or 1

    card_id, target_commodity, target_seat = None, None, None
    playable = [c for c in me["hand"] if c["card_type"] != "insider_tip"]
    insider = next((c for c in me["hand"] if c["card_type"] == "insider_tip"), None)
    if me["card_played_this_round"]:
        st.caption(f"You already used {theme.card_label(me['card_played_this_round'])} this round.")
    else:
        if insider:
            if st.button(f"🔍 Use {theme.card_name('insider_tip')} now", use_container_width=True, key=f"tip_{r}"):
                act(lambda: service.use_insider_tip(db, code, token, r, insider["card_id"],
                                                    request_id("tip", code, r)))
            st.caption("Reveals this round's true event to you only. Counts as your card for this round.")
        if playable:
            by_id: dict[str, dict] = {}
            for c in playable:  # one option per card type (duplicates are interchangeable)
                if c["card_type"] not in {x["card_type"] for x in by_id.values()}:
                    by_id[c["card_id"]] = c
            card_id = st.radio("Play a card (optional)", [None] + list(by_id), key=f"card_{r}", horizontal=True,
                               format_func=lambda cid: "No card" if cid is None else theme.card_label(by_id[cid]["card_type"]))
            if card_id:
                ctype = by_id[card_id]["card_type"]
                d = theme.CARD_DISPLAY[ctype]
                st.caption((f"{d['en']} — " if en else "") + d["text"])
                if CARD_TARGETS[ctype] == "commodity":
                    target_commodity = st.pills("Target goods", list(DISPLAY_ORDER),
                                                default=DISPLAY_ORDER[0], key=f"tc_{r}",
                                                format_func=theme.commodity_name)
                elif CARD_TARGETS[ctype] == "player":
                    opponents = {p["seat"]: p["name"] for p in view["players"] if not p["is_me"]}
                    target_seat = st.radio("Target trader", list(opponents), key=f"ts_{r}", horizontal=True,
                                           format_func=lambda s: opponents[s])

    # Client-side preview only; the server re-validates everything.
    error = preview(view, action, commodity, qty, card_id, target_commodity, target_seat)
    if action != "hold" and commodity and not error:
        total = qty * prices[commodity]
        after = me["coins"] - total if action == "buy" else me["coins"] + total
        st.markdown(f'<div class="bb-muted">{qty} × {prices[commodity]} = <b>{total} coins</b> · '
                    f'coins after trade: <b>{after}</b></div>', unsafe_allow_html=True)
    if error:
        st.warning(error)
    if st.button("🔒 Lock in move", type="primary", use_container_width=True, disabled=bool(error), key=f"lock_{r}"):
        risky = card_id is not None and by_id[card_id]["card_type"] == "double_down"
        flash = "Move accepted — locked in ✓"
        if risky and ss.get("reactions", True):
            flash = f"{theme.reaction('risk', en)} · {flash}"
        act(lambda: service.submit_action(
            db, code, token, r, request_id("move", code, r), action, commodity, qty, card_id,
            target_commodity, target_seat), flash=flash)


def preview(view, action, commodity, qty, card_id, target_commodity, target_seat) -> Optional[str]:
    me = view["me"]
    if action != "hold" and not commodity:
        return "Choose what to trade."
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


def line_chart(df: pd.DataFrame, x: str, y: str, series: str, domain: list[str], colors: list[str]) -> None:
    chart = (alt.Chart(df).mark_line(point=True, strokeWidth=2.5)
             .encode(x=alt.X(f"{x}:O", title="Round", axis=alt.Axis(labelAngle=0)),
                     y=alt.Y(f"{y}:Q", title=None, scale=alt.Scale(zero=False)),
                     color=alt.Color(f"{series}:N", title=None, legend=alt.Legend(orient="bottom"),
                                     scale=alt.Scale(domain=domain, range=colors)),
                     tooltip=[x, series, y])
             .properties(height=250)
             .configure(background="transparent"))
    st.altair_chart(chart, use_container_width=True)


def price_chart(history: list[dict]) -> None:
    if len(history) < 2:
        st.caption("The price history builds up as rounds settle.")
        return
    df = pd.DataFrame(history).melt(id_vars="round", var_name="commodity", value_name="price")
    df["commodity"] = df["commodity"].map(theme.commodity_name)
    line_chart(df, "round", "price", "commodity", [theme.commodity_name(c) for c in DISPLAY_ORDER],
               [theme.COMMODITY_DISPLAY[c]["color"] for c in DISPLAY_ORDER])


# ---------------------------------------------------------------- results

def results(view: dict) -> None:
    st.markdown(ui.page_width_css(WIDE), unsafe_allow_html=True)
    res = view["results"]
    winners = [r for r in res if r["rank"] == 1]
    me = view["me"]
    if not ss.get("celebrated"):
        st.balloons()
        ss["celebrated"] = True
    header()
    left, right = st.columns([6, 5], gap="medium")
    with left:
        names = " & ".join(w["name"] for w in winners)
        st.markdown(f'<div class="bb-winner">🏆 {ui.esc(names)} {"win" if len(winners) > 1 else "wins"} the bazaar!</div>',
                    unsafe_allow_html=True)
        if ss.get("reactions", True):
            st.markdown(f'<div class="bb-react">{ui.esc(theme.reaction("winner", english()))}</div>',
                        unsafe_allow_html=True)
        if any(w["player_id"] == me["player_id"] for w in winners):
            st.success("That's you! Masterful trading. 🎉")
        st.markdown(ui.podium_html(res), unsafe_allow_html=True)
        st.markdown(ui.results_table_html(res), unsafe_allow_html=True)
        st.write("")
        if st.button("⛵ Back to the bazaar", type="primary", use_container_width=True):
            exit_room()
    with right:
        if view["summaries"]:
            rows = [{"round": s["round"], "trader": p["name"], "net worth": p["net_worth"]}
                    for s in view["summaries"] for p in s["players"]]
            st.markdown('<div class="bb-h">Net worth by round</div>', unsafe_allow_html=True)
            traders = [p["name"] for p in view["players"]]
            line_chart(pd.DataFrame(rows), "round", "net worth", "trader", traders,
                       ["#1E4D36", "#B08A3E", "#6B4226", "#5E8A40"][:len(traders)])
        with st.expander("📈 Market history"):
            price_chart(view["price_history"])
        with st.expander("📣 Every round"):
            pnames = {p["player_id"]: p["name"] for p in view["players"]}
            for s in view["summaries"]:
                st.markdown(f"**Round {s['round']}**")
                st.markdown(ui.summary_html(s, pnames, english()), unsafe_allow_html=True)


# ------------------------------------------------------------------- main

@st.fragment(run_every=3)
def reconnect_probe(db: Database, code: str, token: str) -> None:
    try:
        service.get_pulse(db, code, token)
    except CONNECTION_ERRORS:
        return
    except GameError:
        pass
    st.rerun(scope="app")


def message_page(text: str, kind: str = "info") -> None:
    st.markdown(ui.page_width_css(NARROW), unsafe_allow_html=True)
    header()
    getattr(st, kind)(text)
    if st.button("Back to the bazaar", type="primary"):
        exit_room()


def room_page(db: Database, code: str, token: str) -> None:
    try:
        heartbeat(db, code, token)
        view = service.get_view(db, code, token)
    except AuthError:
        message_page("This browser session is no longer valid for that room (you left, were removed after "
                     "being idle in the lobby, or the link is wrong).", "warning")
        return
    except CONNECTION_ERRORS:
        st.markdown(ui.page_width_css(NARROW), unsafe_allow_html=True)
        header()
        st.warning("📡 Reconnecting to the bazaar… Your seat and any locked-in move are safe on the server.")
        reconnect_probe(db, code, token)
        return
    except GameError as exc:
        message_page(str(exc), "error")
        return

    status = view["room"]["status"]
    if status == "lobby":
        lobby(db, code, token, view)
    elif status == "active":
        game(db, code, token, view)
    elif status == "finished":
        results(view)
    else:
        message_page("This room was closed after 30 minutes of inactivity.")


def main() -> None:
    init_prefs()
    try:
        db = get_db()
    except Exception as exc:  # noqa: BLE001 - show setup help instead of a stack trace
        st.markdown(ui.page_width_css(NARROW), unsafe_allow_html=True)
        st.markdown(ui.brand_html(big=True), unsafe_allow_html=True)
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
