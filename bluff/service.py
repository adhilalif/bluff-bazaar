"""Trusted, server-side game operations.

Every public function:
  * identifies the acting player from a secret token (never from a client-supplied id),
  * runs in a single transaction that first locks the room row (`FOR UPDATE`),
  * reloads all authoritative state (coins, prices, inventory, hand) from the database,
  * validates with the pure engine before writing anything.

Callers (the Streamlit UI) only pass *intent*: action, commodity, quantity,
the id of one of their own cards, and a target seat.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from typing import Any, Optional

import psycopg
from psycopg.types.json import Jsonb

from . import engine
from .config import COMMODITIES, GameConfig
from .theme import rumor_for, rumor_subject
from .db import Database
from .engine import CardPlay, InvalidAction, PlayerState, Submission

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
CODE_LENGTH = 6
OFFLINE_AFTER_SECONDS = 30
LOBBY_TIMEOUT_SECONDS = 120
INACTIVE_ROOM_MINUTES = 30


class GameError(Exception):
    """An error whose message is safe to show to the player."""


class AuthError(GameError):
    """The token does not belong to a player in this room (stale or forged session)."""


class StaleError(GameError):
    """The client acted on an out-of-date round or room state."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", "".join(ch for ch in (name or "") if ch.isprintable())).strip()
    if not 1 <= len(name) <= 20:
        raise GameError("Name must be 1–20 characters.")
    return name


def _parse_uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise GameError(f"Invalid {what}.") from None


def _lock_room(conn: psycopg.Connection, code: str) -> dict:
    room = conn.execute("select * from bluff.rooms where code = %s for update",
                        (normalize_code(code),)).fetchone()
    if room is None:
        raise GameError("Room not found. Check the code.")
    return room


def _auth(conn: psycopg.Connection, room: dict, token: Optional[str]) -> dict:
    if not token:
        raise AuthError("You are not a player in this room.")
    player = conn.execute("select * from bluff.players where room_id = %s and token_hash = %s",
                          (room["id"], hash_token(token))).fetchone()
    if player is None:
        raise AuthError("Your session for this room has expired or is invalid.")
    return player


def _bump(conn: psycopg.Connection, room_id: uuid.UUID) -> None:
    conn.execute("update bluff.rooms set version = version + 1, last_activity_at = now() where id = %s",
                 (room_id,))


def _log(conn: psycopg.Connection, room_id: uuid.UUID, round_no: int, kind: str, payload: dict) -> None:
    conn.execute("insert into bluff.event_log (room_id, round_no, kind, payload) values (%s, %s, %s, %s)",
                 (room_id, round_no, kind, Jsonb(payload)))


def _config(room: dict) -> GameConfig:
    return GameConfig.from_dict(room["config"])


def _require_status(room: dict, *statuses: str) -> None:
    if room["status"] not in statuses:
        messages = {"abandoned": "This room was closed after inactivity.",
                    "finished": "This game has already finished.",
                    "active": "This game has already started.",
                    "lobby": "The game has not started yet."}
        raise StaleError(messages.get(room["status"], "Room is not available."))


def expire_inactive_rooms(conn: psycopg.Connection) -> int:
    cur = conn.execute(
        "update bluff.rooms set status = 'abandoned', version = version + 1, round_deadline = null "
        "where status in ('lobby', 'active') and last_activity_at < now() - make_interval(mins => %s)",
        (INACTIVE_ROOM_MINUTES,))
    return cur.rowcount


def _prune_lobby(conn: psycopg.Connection, room: dict, abandon_if_empty: bool = True) -> bool:
    """Drop lobby players who stopped polling; reassign host. Returns True if anything changed."""
    removed = conn.execute(
        "delete from bluff.players where room_id = %s and last_seen_at < now() - make_interval(secs => %s) "
        "returning seat, display_name",
        (room["id"], LOBBY_TIMEOUT_SECONDS)).fetchall()
    for r in removed:
        _log(conn, room["id"], 0, "player_timed_out", {"seat": r["seat"], "name": r["display_name"]})
    if removed:
        _fix_host(conn, room, abandon_if_empty)
    return bool(removed)


def _fix_host(conn: psycopg.Connection, room: dict, abandon_if_empty: bool = True) -> None:
    rows = conn.execute("select id, is_host from bluff.players where room_id = %s order by seat",
                        (room["id"],)).fetchall()
    if not rows:
        if abandon_if_empty:
            conn.execute("update bluff.rooms set status = 'abandoned' where id = %s", (room["id"],))
    elif not any(r["is_host"] for r in rows):
        conn.execute("update bluff.players set is_host = true where id = %s", (rows[0]["id"],))


# ---------------------------------------------------------------------------
# lobby
# ---------------------------------------------------------------------------

def create_room(db: Database, name: str, config: Optional[GameConfig] = None) -> tuple[str, str]:
    """Create a room with the caller as host in seat 1. Returns (room code, player token)."""
    name = clean_name(name)
    config = config or GameConfig()
    token = secrets.token_urlsafe(24)
    with db.tx() as conn:
        expire_inactive_rooms(conn)
        for _ in range(10):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            try:
                with conn.transaction():  # savepoint: retry on code collision
                    room = conn.execute(
                        "insert into bluff.rooms (code, config, seed) values (%s, %s, %s) returning *",
                        (code, Jsonb(config.to_dict()), secrets.randbits(62))).fetchone()
                break
            except psycopg.errors.UniqueViolation:
                continue
        else:  # pragma: no cover - astronomically unlikely
            raise GameError("Could not allocate a room code, please retry.")
        conn.execute(
            "insert into bluff.players (room_id, seat, display_name, token_hash, is_host, coins) "
            "values (%s, 1, %s, %s, true, %s)",
            (room["id"], name, hash_token(token), config.starting_coins))
        _log(conn, room["id"], 0, "room_created", {"name": name})
    return code, token


def join_room(db: Database, code: str, name: str) -> str:
    """Take the lowest free seat in a lobby. Returns the new player's token."""
    name = clean_name(name)
    token = secrets.token_urlsafe(24)
    with db.tx() as conn:
        room = _lock_room(conn, code)
        _require_status(room, "lobby")
        _prune_lobby(conn, room, abandon_if_empty=False)
        taken = conn.execute("select seat, lower(display_name) as n from bluff.players where room_id = %s",
                             (room["id"],)).fetchall()
        cfg = _config(room)
        if len(taken) >= cfg.num_players:
            raise GameError(f"This room is full ({cfg.num_players}/{cfg.num_players} players).")
        if name.lower() in {t["n"] for t in taken}:
            raise GameError("That name is already taken in this room.")
        seat = min(set(range(1, cfg.num_players + 1)) - {t["seat"] for t in taken})
        conn.execute(
            "insert into bluff.players (room_id, seat, display_name, token_hash, coins) values (%s, %s, %s, %s, %s)",
            (room["id"], seat, name, hash_token(token), cfg.starting_coins))
        _log(conn, room["id"], 0, "player_joined", {"seat": seat, "name": name})
        _fix_host(conn, room)
        _bump(conn, room["id"])
    return token


def leave_room(db: Database, code: str, token: str) -> None:
    """Leave a lobby (frees the seat). Seats are kept once the game has started."""
    with db.tx() as conn:
        room = _lock_room(conn, code)
        me = _auth(conn, room, token)
        _require_status(room, "lobby")
        conn.execute("delete from bluff.players where id = %s", (me["id"],))
        _log(conn, room["id"], 0, "player_left", {"seat": me["seat"], "name": me["display_name"]})
        _fix_host(conn, room)
        _bump(conn, room["id"])


def set_ready(db: Database, code: str, token: str, ready: bool) -> bool:
    """Toggle readiness. Starts the game atomically when 4 players are ready. Returns True if started."""
    with db.tx() as conn:
        room = _lock_room(conn, code)
        me = _auth(conn, room, token)
        _require_status(room, "lobby")
        conn.execute("update bluff.players set is_ready = %s, last_seen_at = now() where id = %s",
                     (bool(ready), me["id"]))
        _prune_lobby(conn, room)
        players = conn.execute("select * from bluff.players where room_id = %s order by seat",
                               (room["id"],)).fetchall()
        started = False
        if len(players) == _config(room).num_players and all(p["is_ready"] for p in players):
            _start_game(conn, room, players)
            started = True
        _bump(conn, room["id"])
    return started


def _start_game(conn: psycopg.Connection, room: dict, players: list[dict]) -> None:
    cfg = _config(room)
    ids = [str(p["id"]) for p in players]
    hands = engine.deal_hands(room["seed"], ids, cfg)
    for pid, hand in hands.items():
        for card_type in hand:
            conn.execute("insert into bluff.player_cards (room_id, player_id, card_type) values (%s, %s, %s)",
                         (room["id"], pid, card_type))
        for c in COMMODITIES:
            conn.execute("insert into bluff.inventories (player_id, commodity, quantity) values (%s, %s, 0) "
                         "on conflict do nothing", (pid, c))
    if cfg.full:
        for pid, order_key in engine.deal_orders(room["seed"], ids).items():
            conn.execute("insert into bluff.player_orders (player_id, room_id, order_key) values (%s, %s, %s)",
                         (pid, room["id"], order_key))
    for r, event_key in enumerate(engine.schedule_events(room["seed"], cfg), start=1):
        conn.execute("insert into bluff.round_events (room_id, round_no, event_key) values (%s, %s, %s)",
                     (room["id"], r, event_key))
    _insert_prices(conn, room["id"], 1, cfg.starting_prices)
    conn.execute("update bluff.players set coins = %s where room_id = %s", (cfg.starting_coins, room["id"]))
    _open_round(conn, room, 1)
    conn.execute("update bluff.rooms set status = 'active' where id = %s", (room["id"],))
    _log(conn, room["id"], 1, "game_started", {})


def _insert_prices(conn: psycopg.Connection, room_id: uuid.UUID, round_no: int, prices: dict[str, int]) -> None:
    for c in COMMODITIES:
        conn.execute("insert into bluff.market_prices (room_id, round_no, commodity, price) values (%s, %s, %s, %s)",
                     (room_id, round_no, c, prices[c]))


def _open_round(conn: psycopg.Connection, room: dict, round_no: int) -> None:
    secs = _config(room).turn_seconds
    conn.execute("insert into bluff.rounds (room_id, round_no, deadline_at) "
                 "values (%s, %s, now() + make_interval(secs => %s))", (room["id"], round_no, secs))
    conn.execute("update bluff.rooms set current_round = %s, round_deadline = now() + make_interval(secs => %s) "
                 "where id = %s", (round_no, secs, room["id"]))


# ---------------------------------------------------------------------------
# heartbeat / timeouts
# ---------------------------------------------------------------------------

def touch(db: Database, code: str, token: str) -> None:
    """Heartbeat: mark the player online and run room housekeeping (lobby pruning, timeouts)."""
    with db.tx() as conn:
        expire_inactive_rooms(conn)
        room = _lock_room(conn, code)
        me = _auth(conn, room, token)
        conn.execute("update bluff.players set last_seen_at = now() where id = %s", (me["id"],))
        if room["status"] in ("lobby", "active"):
            conn.execute("update bluff.rooms set last_activity_at = now() where id = %s", (room["id"],))
        if room["status"] == "lobby" and _prune_lobby(conn, room):
            _bump(conn, room["id"])
        elif room["status"] == "active":
            _resolve_if_expired(conn, room)


def resolve_if_expired(db: Database, code: str) -> bool:
    """If the round timer has run out, auto-HOLD missing players and resolve. Safe to call from any client."""
    with db.tx() as conn:
        room = _lock_room(conn, code)
        return _resolve_if_expired(conn, room)


def _resolve_if_expired(conn: psycopg.Connection, room: dict) -> bool:
    if room["status"] != "active":
        return False
    expired = conn.execute("select now() >= round_deadline as e from bluff.rooms where id = %s",
                           (room["id"],)).fetchone()["e"]
    if not expired:
        return False
    missing = conn.execute(
        "select p.id, p.seat, p.display_name from bluff.players p where p.room_id = %s and not exists "
        "(select 1 from bluff.submissions s where s.player_id = p.id and s.round_no = %s)",
        (room["id"], room["current_round"])).fetchall()
    for p in missing:
        conn.execute("insert into bluff.submissions (room_id, round_no, player_id, request_id, action, is_auto) "
                     "values (%s, %s, %s, %s, 'hold', true)", (room["id"], room["current_round"], p["id"], uuid.uuid4()))
        _log(conn, room["id"], room["current_round"], "auto_hold", {"seat": p["seat"], "name": p["display_name"]})
    _resolve_round(conn, room)
    return True


# ---------------------------------------------------------------------------
# turns
# ---------------------------------------------------------------------------

def _player_state(conn: psycopg.Connection, player: dict) -> PlayerState:
    inv = {r["commodity"]: r["quantity"] for r in conn.execute(
        "select commodity, quantity from bluff.inventories where player_id = %s", (player["id"],))}
    return PlayerState(str(player["id"]), player["seat"], player["display_name"], player["coins"], inv)


def _prices(conn: psycopg.Connection, room_id: uuid.UUID, round_no: int) -> dict[str, int]:
    return {r["commodity"]: r["price"] for r in conn.execute(
        "select commodity, price from bluff.market_prices where room_id = %s and round_no = %s",
        (room_id, round_no))}


def _check_round(conn: psycopg.Connection, room: dict, round_no: int) -> None:
    _require_status(room, "active")
    if int(round_no) != room["current_round"]:
        raise StaleError("That round is already over — your screen has been refreshed.")
    status = conn.execute("select status from bluff.rounds where room_id = %s and round_no = %s",
                          (room["id"], room["current_round"])).fetchone()
    if not status or status["status"] != "open":
        raise StaleError("This round is being resolved.")


def use_insider_tip(db: Database, code: str, token: str, round_no: int, card_id: str, request_id: str) -> dict:
    """Spend an Insider Tip now and privately learn this round's true event. Idempotent per request_id."""
    card_uuid = _parse_uuid(card_id, "card")
    req = _parse_uuid(request_id, "request")
    with db.tx() as conn:
        room = _lock_room(conn, code)
        me = _auth(conn, room, token)
        prior = conn.execute("select player_id, payload from bluff.private_log where request_id = %s",
                             (req,)).fetchone()
        if prior:
            if prior["player_id"] != me["id"]:
                raise GameError("Duplicate request id.")
            return prior["payload"]
        _check_round(conn, room, round_no)
        r = room["current_round"]
        if conn.execute("select 1 from bluff.submissions where player_id = %s and round_no = %s",
                        (me["id"], r)).fetchone():
            raise GameError("You already locked in this round.")
        if conn.execute("select 1 from bluff.player_cards where player_id = %s and played_round = %s",
                        (me["id"], r)).fetchone():
            raise GameError("You already played a card this round.")
        card = conn.execute(
            "update bluff.player_cards set played_round = %s where id = %s and player_id = %s "
            "and card_type = 'insider_tip' and played_round is null returning id",
            (r, card_uuid, me["id"])).fetchone()
        if card is None:
            raise GameError("You don't have that Insider Tip.")
        event_key = conn.execute("select event_key from bluff.round_events where room_id = %s and round_no = %s",
                                 (room["id"], r)).fetchone()["event_key"]
        payload = {"round": r, "event": event_key}
        conn.execute("insert into bluff.private_log (room_id, player_id, round_no, kind, payload, request_id) "
                     "values (%s, %s, %s, 'insider_tip', %s, %s)", (room["id"], me["id"], r, Jsonb(payload), req))
        conn.execute("update bluff.rooms set last_activity_at = now() where id = %s", (room["id"],))
    return payload


def submit_action(
    db: Database,
    code: str,
    token: str,
    round_no: int,
    request_id: str,
    action: str,
    commodity: Optional[str] = None,
    quantity: int = 0,
    card_id: Optional[str] = None,
    target_commodity: Optional[str] = None,
    target_seat: Optional[int] = None,
    bid: int = 0,
) -> dict:
    """Lock in this round's secret decision (trade, optional card, optional cargo bid).

    Idempotent per request_id.

    Returns {"duplicate": bool, "resolved": bool}. Resolves the round in the same
    transaction when this is the last missing submission.
    """
    req = _parse_uuid(request_id, "request")
    with db.tx() as conn:
        room = _lock_room(conn, code)
        me = _auth(conn, room, token)
        prior = conn.execute("select player_id from bluff.submissions where request_id = %s", (req,)).fetchone()
        if prior:
            if prior["player_id"] != me["id"]:
                raise GameError("Duplicate request id.")
            return {"duplicate": True, "resolved": False}
        _check_round(conn, room, round_no)
        r = room["current_round"]
        if conn.execute("select 1 from bluff.submissions where player_id = %s and round_no = %s",
                        (me["id"], r)).fetchone():
            raise GameError("You already locked in this round.")

        cfg = _config(room)
        state = _player_state(conn, me)
        prices = _prices(conn, room["id"], r)
        hand_rows = conn.execute("select id, card_type from bluff.player_cards where player_id = %s "
                                 "and played_round is null", (me["id"],)).fetchall()
        played_now = conn.execute("select 1 from bluff.player_cards where player_id = %s and played_round = %s",
                                  (me["id"], r)).fetchone() is not None
        seats = {p["seat"]: str(p["id"]) for p in conn.execute(
            "select id, seat from bluff.players where room_id = %s", (room["id"],))}

        card_row = None
        card_play = None
        if card_id:
            card_uuid = _parse_uuid(card_id, "card")
            card_row = next((c for c in hand_rows if c["id"] == card_uuid), None)
            if card_row is None:
                raise GameError("That card is not in your hand.")
            target_player = None
            if target_seat is not None:
                try:
                    target_player = seats.get(int(target_seat), "__invalid__")
                except (TypeError, ValueError):
                    target_player = "__invalid__"
            card_play = CardPlay(card_row["card_type"], target_commodity or None, target_player)

        if action == "hold":
            commodity, quantity = None, 0
        try:
            qty = int(quantity) if not isinstance(quantity, bool) and float(quantity).is_integer() else quantity
        except (TypeError, ValueError):
            raise GameError("Quantity must be a whole number.") from None
        try:
            bid_value = int(bid or 0) if not isinstance(bid, bool) and float(bid or 0).is_integer() else -1
        except (TypeError, ValueError):
            raise GameError("Bid must be a whole number of coins.") from None
        sub = Submission(action=action, commodity=commodity, quantity=qty, card=card_play, bid=bid_value)
        rules = engine.round_rules(cfg, room["seed"], r)
        try:
            engine.validate_submission(cfg, state, prices, sub, [c["card_type"] for c in hand_rows],
                                       [pid for pid in seats.values() if pid != str(me["id"])], played_now, rules)
        except InvalidAction as exc:
            raise GameError(str(exc)) from None

        if card_row is not None:
            spent = conn.execute("update bluff.player_cards set played_round = %s "
                                 "where id = %s and played_round is null returning id", (r, card_row["id"])).fetchone()
            if spent is None:  # pragma: no cover - guarded by the room lock
                raise GameError("That card was already used.")
        conn.execute(
            "insert into bluff.submissions (room_id, round_no, player_id, request_id, action, commodity, quantity, "
            "card_id, card_type, target_commodity, target_player_id, bid) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (room["id"], r, me["id"], req, sub.action, sub.commodity, sub.quantity,
             card_row["id"] if card_row else None, card_play.card_type if card_play else None,
             card_play.target_commodity if card_play else None,
             card_play.target_player if card_play else None, sub.bid))
        _bump(conn, room["id"])

        count = conn.execute("select count(*) as n from bluff.submissions where room_id = %s and round_no = %s",
                             (room["id"], r)).fetchone()["n"]
        resolved = False
        if count >= cfg.num_players:
            _resolve_round(conn, room)
            resolved = True
    return {"duplicate": False, "resolved": resolved}


def _resolve_round(conn: psycopg.Connection, room: dict) -> None:
    """Resolve the current round exactly once. Caller holds the room lock."""
    cfg = _config(room)
    r = room["current_round"]
    flipped = conn.execute("update bluff.rounds set status = 'resolved', resolved_at = now() "
                           "where room_id = %s and round_no = %s and status = 'open' returning round_no",
                           (room["id"], r)).fetchone()
    if flipped is None:
        raise StaleError("Round already resolved.")

    rows = conn.execute("select * from bluff.players where room_id = %s order by seat", (room["id"],)).fetchall()
    players = [_player_state(conn, p) for p in rows]
    prices = _prices(conn, room["id"], r)
    subs: dict[str, Submission] = {}
    for s in conn.execute("select * from bluff.submissions where room_id = %s and round_no = %s", (room["id"], r)):
        card = None
        if s["card_type"]:
            card = CardPlay(s["card_type"], s["target_commodity"],
                            str(s["target_player_id"]) if s["target_player_id"] else None)
        subs[str(s["player_id"])] = Submission(s["action"], s["commodity"], s["quantity"], card, s["is_auto"],
                                               bid=s["bid"])
    event_key = conn.execute("select event_key from bluff.round_events where room_id = %s and round_no = %s",
                             (room["id"], r)).fetchone()["event_key"]

    outcome = engine.resolve_round(cfg, r, prices, players, subs, event_key,
                                   engine.round_rules(cfg, room["seed"], r))

    # Insider Tip use is public after resolution (but not what it revealed).
    insiders = {str(x["player_id"]) for x in conn.execute(
        "select player_id from bluff.private_log where room_id = %s and round_no = %s and kind = 'insider_tip'",
        (room["id"], r))}
    for row in outcome.summary["players"]:
        row["insider_tip"] = row["player_id"] in insiders

    for p in outcome.players:
        conn.execute("update bluff.players set coins = %s where id = %s", (p.coins, p.player_id))
        for c in COMMODITIES:
            conn.execute("insert into bluff.inventories (player_id, commodity, quantity) values (%s, %s, %s) "
                         "on conflict (player_id, commodity) do update set quantity = excluded.quantity",
                         (p.player_id, c, p.inventory.get(c, 0)))
    _insert_prices(conn, room["id"], r + 1, outcome.prices)
    conn.execute("update bluff.rounds set summary = %s where room_id = %s and round_no = %s",
                 (Jsonb(outcome.summary), room["id"], r))
    _log(conn, room["id"], r, "round_resolved", {"event": event_key})

    if r >= cfg.num_rounds:
        bonuses = _order_bonuses(conn, room, outcome) if cfg.full else None
        for row in engine.final_ranking(outcome.players, outcome.prices, bonuses):
            conn.execute("insert into bluff.results (room_id, player_id, rank, net_worth, coins, inventory_value, "
                         "order_bonus) values (%s, %s, %s, %s, %s, %s, %s)",
                         (room["id"], row["player_id"], row["rank"], row["net_worth"], row["coins"],
                          row["inventory_value"], row["order_bonus"]))
        conn.execute("update bluff.rooms set status = 'finished', finished_at = now(), round_deadline = null "
                     "where id = %s", (room["id"],))
        _log(conn, room["id"], r, "game_finished", {})
    else:
        _open_round(conn, room, r + 1)
    _bump(conn, room["id"])


def _order_bonuses(conn: psycopg.Connection, room: dict, outcome: engine.RoundOutcome) -> dict[str, int]:
    """End-of-game merchant order bonuses. Cargo lots won are counted from the round summaries.

    Called after the final round's summary has been saved, so every round (including the last) is read
    from the database exactly once.
    """
    lots_won: dict[str, int] = {}
    summaries = [x["summary"] for x in conn.execute(
        "select summary from bluff.rounds where room_id = %s and summary is not null", (room["id"],))]
    for summary in summaries:
        auction = (summary or {}).get("auction") or {}
        if auction.get("winner"):
            lots_won[auction["winner"]] = lots_won.get(auction["winner"], 0) + 1
    orders = {str(x["player_id"]): x["order_key"] for x in conn.execute(
        "select player_id, order_key from bluff.player_orders where room_id = %s", (room["id"],))}
    return {p.player_id: engine.order_bonus(orders.get(p.player_id), p, lots_won.get(p.player_id, 0))
            for p in outcome.players}


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def get_version(db: Database, code: str) -> Optional[int]:
    """Cheap change detector used by the 2-second poll."""
    with db.pool.connection() as conn:
        row = conn.execute("select version from bluff.rooms where code = %s", (normalize_code(code),)).fetchone()
    return row["version"] if row else None


def get_pulse(db: Database, code: str, token: str) -> dict:
    """Small public status read for the 2-second poll.

    Lets the UI update "locked in" / online markers and the timer in place, and only rerun the
    whole page when the phase (status or round) changes. Contains no private data.
    """
    with db.pool.connection() as conn:
        room = conn.execute(
            "select id, status, current_round, version, "
            "greatest(0, extract(epoch from (round_deadline - now())))::int as secs_left "
            "from bluff.rooms where code = %s", (normalize_code(code),)).fetchone()
        if room is None:
            raise GameError("Room not found. Check the code.")
        _auth(conn, room, token)
        rows = conn.execute(
            "select p.seat, p.last_seen_at > now() - make_interval(secs => %s) as online, "
            "exists (select 1 from bluff.submissions s where s.player_id = p.id and s.round_no = %s) as submitted "
            "from bluff.players p where p.room_id = %s order by p.seat",
            (OFFLINE_AFTER_SECONDS, room["current_round"], room["id"])).fetchall()
    return {
        "status": room["status"],
        "round": room["current_round"],
        "version": room["version"],
        "seconds_left": room["secs_left"],
        "submitted": [r["seat"] for r in rows if r["submitted"]],
        "online": [r["seat"] for r in rows if r["online"]],
    }


def get_view(db: Database, code: str, token: str) -> dict:
    """Everything one player is allowed to see, and nothing else.

    Opponents' hands, pending submissions, the event schedule and other
    players' Insider Tip reveals are never loaded into this structure.
    """
    with db.read() as conn:
        room = conn.execute("select * from bluff.rooms where code = %s", (normalize_code(code),)).fetchone()
        if room is None:
            raise GameError("Room not found. Check the code.")
        me = _auth(conn, room, token)
        cfg = _config(room)
        r = room["current_round"]
        now_info = conn.execute(
            "select greatest(0, extract(epoch from (round_deadline - now())))::int as secs_left "
            "from bluff.rooms where id = %s", (room["id"],)).fetchone()

        submitted = {x["player_id"] for x in conn.execute(
            "select player_id from bluff.submissions where room_id = %s and round_no = %s", (room["id"], r))}
        players = [
            {
                "player_id": str(p["id"]),
                "seat": p["seat"],
                "name": p["display_name"],
                "is_host": p["is_host"],
                "is_ready": p["is_ready"],
                "online": p["online"],
                "submitted": p["id"] in submitted,
                "is_me": p["id"] == me["id"],
            }
            for p in conn.execute(
                "select id, seat, display_name, is_host, is_ready, "
                "last_seen_at > now() - make_interval(secs => %s) as online "
                "from bluff.players where room_id = %s order by seat", (OFFLINE_AFTER_SECONDS, room["id"]))
        ]

        state = _player_state(conn, me)
        hand = [{"card_id": str(c["id"]), "card_type": c["card_type"]}
                for c in conn.execute("select id, card_type from bluff.player_cards where player_id = %s "
                                      "and played_round is null order by card_type, id", (me["id"],))]
        played_now = conn.execute("select card_type from bluff.player_cards where player_id = %s "
                                  "and played_round = %s", (me["id"], r)).fetchone()
        my_sub = conn.execute(
            "select action, commodity, quantity, card_type, target_commodity, target_player_id, is_auto, bid "
            "from bluff.submissions where player_id = %s and round_no = %s", (me["id"], r)).fetchone()
        my_order = conn.execute("select order_key from bluff.player_orders where player_id = %s",
                                (me["id"],)).fetchone()
        if my_sub and my_sub["target_player_id"]:
            my_sub["target_player_id"] = str(my_sub["target_player_id"])
        private = [{"round": x["round_no"], "kind": x["kind"], **x["payload"]} for x in conn.execute(
            "select round_no, kind, payload from bluff.private_log where player_id = %s order by id", (me["id"],))]

        history: dict[int, dict[str, int]] = {}
        for x in conn.execute("select round_no, commodity, price from bluff.market_prices where room_id = %s "
                              "order by round_no", (room["id"],)):
            history.setdefault(x["round_no"], {})[x["commodity"]] = x["price"]
        summaries = [x["summary"] for x in conn.execute(
            "select summary from bluff.rounds where room_id = %s and status = 'resolved' order by round_no",
            (room["id"],))]

        current = None
        if room["status"] == "active":
            ev = conn.execute("select event_key from bluff.round_events where room_id = %s and round_no = %s",
                              (room["id"], r)).fetchone()
            rules = engine.round_rules(cfg, room["seed"], r)
            public_rules = rules.to_public()
            if rules.lot:
                public_rules["lot"]["reserve"] = engine.lot_reserve(cfg, rules.lot, history.get(r) or cfg.starting_prices)
            current = {"round": r, "rumor": rumor_for(ev["event_key"]),
                       "rumor_subject": rumor_subject(ev["event_key"]),
                       "seconds_left": now_info["secs_left"], "submitted_count": len(submitted),
                       "rules": public_rules}

        results = [
            {"player_id": str(x["player_id"]), "name": x["display_name"], "seat": x["seat"], "rank": x["rank"],
             "net_worth": x["net_worth"], "coins": x["coins"], "inventory_value": x["inventory_value"],
             "order_bonus": x["order_bonus"], "order": x["order_key"]}  # orders are revealed once the game ends
            for x in conn.execute(
                "select r.*, p.display_name, p.seat, o.order_key from bluff.results r "
                "join bluff.players p on p.id = r.player_id left join bluff.player_orders o on o.player_id = r.player_id "
                "where r.room_id = %s order by r.rank, p.seat", (room["id"],))
        ]

    prices = history.get(r) if room["status"] == "active" else (history[max(history)] if history else None)
    prices = prices or dict(cfg.starting_prices)
    return {
        "room": {"code": room["code"], "status": room["status"], "round": r, "num_rounds": cfg.num_rounds,
                 "version": room["version"]},
        "config": {**{k: getattr(cfg, k) for k in ("starting_coins", "inventory_limit", "max_trade_qty",
                                                   "num_rounds", "turn_seconds", "starting_prices", "rules",
                                                   "num_players",
                                                   "season_start", "season_end", "spoil_threshold")},
                   "perishables": list(cfg.perishables),
                   # The monsoon effect is public from the start so players can plan for it.
                   "season": engine.schedule_season(room["seed"]) if cfg.full else None},
        "me": {
            "player_id": str(me["id"]), "seat": me["seat"], "name": me["display_name"], "is_host": me["is_host"],
            "is_ready": me["is_ready"], "coins": state.coins,
            "inventory": {c: state.held(c) for c in COMMODITIES},
            "net_worth": engine.net_worth(state, prices),
            "hand": hand,
            "card_played_this_round": played_now["card_type"] if played_now else None,
            "submission": my_sub,
            "private_log": private,
            "order": my_order["order_key"] if my_order else None,
        },
        "players": players,
        "prices": prices,
        "price_history": [{"round": k, **v} for k, v in sorted(history.items())],
        "current": current,
        "summaries": summaries,
        "results": results,
    }


def view_json(view: dict) -> str:
    """Serialized view (used by tests to assert nothing secret leaks)."""
    return json.dumps(view, default=str, sort_keys=True)
