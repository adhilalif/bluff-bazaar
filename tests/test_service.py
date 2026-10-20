"""Integration tests: service layer against a real Postgres (see conftest.py)."""
import threading
from collections import Counter

import psycopg
import pytest

from bluff import service
from bluff.config import COMMODITIES, GameConfig, event_keys
from bluff.service import AuthError, GameError, StaleError, view_json

from .conftest import rid


def view(db, code, token):
    return service.get_view(db, code, token)


def hand(db, code, token, card_type=None):
    cards = view(db, code, token)["me"]["hand"]
    return [c for c in cards if card_type is None or c["card_type"] == card_type]


def give_card(db, code, token, card_type):
    """Force a specific card into a player's hand (test setup only)."""
    with db.tx() as conn:
        pid = conn.execute("select p.id from bluff.players p join bluff.rooms r on r.id = p.room_id "
                           "where r.code = %s and p.token_hash = %s", (code, service.hash_token(token))).fetchone()["id"]
        room_id = conn.execute("select id from bluff.rooms where code = %s", (code,)).fetchone()["id"]
        cid = conn.execute("insert into bluff.player_cards (room_id, player_id, card_type) values (%s, %s, %s) "
                           "returning id", (room_id, pid, card_type)).fetchone()["id"]
    return str(cid)


def set_state(db, code, token, coins=None, inventory=None):
    with db.tx() as conn:
        pid = conn.execute("select p.id from bluff.players p join bluff.rooms r on r.id = p.room_id "
                           "where r.code = %s and p.token_hash = %s", (code, service.hash_token(token))).fetchone()["id"]
        if coins is not None:
            conn.execute("update bluff.players set coins = %s where id = %s", (coins, pid))
        for c, q in (inventory or {}).items():
            conn.execute("update bluff.inventories set quantity = %s where player_id = %s and commodity = %s",
                         (q, pid, c))


def submit(db, code, token, round_no=1, request_id=None, **kw):
    kw.setdefault("action", "hold")
    return service.submit_action(db, code, token, round_no, request_id or rid(), **kw)


def all_hold(db, code, tokens, round_no):
    for t in tokens:
        submit(db, code, t, round_no)


# ----------------------------------------------------------------- lobby

class TestLobby:
    def test_create_and_join(self, db, room4):
        code, tokens = room4
        assert len(code) == 6 and code.isalnum() and code.isupper()
        v = view(db, code, tokens[2])
        assert v["room"]["status"] == "lobby"
        assert [p["seat"] for p in v["players"]] == [1, 2, 3, 4]
        assert v["me"]["seat"] == 3 and not v["me"]["is_host"]
        assert view(db, code, tokens[0])["me"]["is_host"]

    def test_code_is_case_insensitive(self, db, room4):
        code, _ = room4
        token = None
        with pytest.raises(GameError, match="full"):
            token = service.join_room(db, f" {code.lower()} ", "Eve")
        assert token is None

    def test_fifth_player_rejected(self, db, room4):
        code, _ = room4
        with pytest.raises(GameError, match="full"):
            service.join_room(db, code, "Eve")

    def test_duplicate_name_rejected(self, db):
        code, _ = service.create_room(db, "Ada")
        with pytest.raises(GameError, match="taken"):
            service.join_room(db, code, "ada")

    @pytest.mark.parametrize("name", ["", "   ", "x" * 21])
    def test_bad_names(self, db, name):
        with pytest.raises(GameError):
            service.create_room(db, name)

    def test_unknown_room(self, db):
        with pytest.raises(GameError, match="not found"):
            service.join_room(db, "ZZZZZZ", "Eve")

    def test_game_starts_only_when_all_four_ready(self, db, room4):
        code, tokens = room4
        for t in tokens[:3]:
            assert service.set_ready(db, code, t, True) is False
        service.set_ready(db, code, tokens[0], False)
        assert service.set_ready(db, code, tokens[3], True) is False
        assert service.set_ready(db, code, tokens[0], True) is True
        v = view(db, code, tokens[0])
        assert v["room"]["status"] == "active" and v["room"]["round"] == 1
        assert v["current"]["rumor"] and v["current"]["seconds_left"] > 0
        assert v["prices"] == GameConfig().starting_prices
        assert v["me"]["coins"] == 100 and sum(v["me"]["inventory"].values()) == 0

    def test_three_ready_players_cannot_start(self, db):
        code, t1 = service.create_room(db, "Ada")
        tokens = [t1, service.join_room(db, code, "Bo"), service.join_room(db, code, "Cy")]
        for t in tokens:
            assert service.set_ready(db, code, t, True) is False
        assert view(db, code, t1)["room"]["status"] == "lobby"

    def test_deal_and_schedule_persisted(self, db, room4):
        code, tokens = room4
        for t in tokens:
            service.set_ready(db, code, t, True)
        counts = Counter()
        for t in tokens:
            h = hand(db, code, t)
            assert len(h) == 3
            counts.update(c["card_type"] for c in h)
        assert set(counts.values()) == {2} and len(counts) == 6
        with db.tx() as conn:
            events = [r["event_key"] for r in conn.execute(
                "select e.event_key from bluff.round_events e join bluff.rooms r on r.id = e.room_id "
                "where r.code = %s order by round_no", (code,))]
        assert len(events) == 8 and len(set(events)) == 8 and set(events) <= set(event_keys())

    def test_cannot_join_started_game(self, db, game):
        code, _ = game
        with pytest.raises(StaleError, match="started"):
            service.join_room(db, code, "Late")

    def test_leave_lobby_frees_seat_and_moves_host(self, db, room4):
        code, tokens = room4
        service.leave_room(db, code, tokens[0])
        v = view(db, code, tokens[1])
        assert [p["seat"] for p in v["players"]] == [2, 3, 4]
        assert v["me"]["is_host"]
        new = service.join_room(db, code, "Eve")
        assert view(db, code, new)["me"]["seat"] == 1
        with pytest.raises(AuthError):
            view(db, code, tokens[0])  # stale session

    def test_last_player_leaving_abandons_room(self, db):
        code, t = service.create_room(db, "Solo")
        service.leave_room(db, code, t)
        with pytest.raises(GameError):
            service.join_room(db, code, "Other")

    def test_stale_lobby_players_pruned(self, db, room4):
        code, tokens = room4
        with db.tx() as conn:
            conn.execute("update bluff.players set last_seen_at = now() - interval '10 minutes' "
                         "where token_hash = %s", (service.hash_token(tokens[3]),))
        service.touch(db, code, tokens[0])
        assert len(view(db, code, tokens[0])["players"]) == 3

    def test_cannot_leave_active_game(self, db, game):
        code, tokens = game
        with pytest.raises(StaleError):
            service.leave_room(db, code, tokens[0])


# ------------------------------------------------------------------- auth

class TestAuth:
    def test_wrong_token(self, db, game):
        code, _ = game
        with pytest.raises(AuthError):
            view(db, code, "forged-token")
        with pytest.raises(AuthError):
            submit(db, code, "forged-token")
        with pytest.raises(AuthError):
            service.touch(db, code, "")

    def test_token_from_other_room(self, db, game):
        code, tokens = game
        other_code, other_token = service.create_room(db, "Zed")
        with pytest.raises(AuthError):
            submit(db, code, other_token)
        with pytest.raises(AuthError):
            view(db, other_code, tokens[0])

    def test_tokens_stored_hashed(self, db, game):
        code, tokens = game
        with db.tx() as conn:
            hashes = [r["token_hash"] for r in conn.execute("select token_hash from bluff.players")]
        assert all(t not in hashes for t in tokens)


# ------------------------------------------------------------- submissions

class TestSubmissions:
    def test_valid_buy_is_locked_until_resolution(self, db, game):
        code, tokens = game
        r = submit(db, code, tokens[0], action="buy", commodity="spices", quantity=3)
        assert r == {"duplicate": False, "resolved": False}
        me = view(db, code, tokens[0])["me"]
        assert me["submission"]["action"] == "buy"
        assert me["coins"] == 100  # nothing settles before resolution

    def test_duplicate_request_is_idempotent(self, db, game):
        code, tokens = game
        req = rid()
        submit(db, code, tokens[0], request_id=req, action="buy", commodity="spices", quantity=1)
        again = submit(db, code, tokens[0], request_id=req, action="buy", commodity="spices", quantity=1)
        assert again["duplicate"] is True
        with db.tx() as conn:
            assert conn.execute("select count(*) n from bluff.submissions").fetchone()["n"] == 1

    def test_second_different_submission_rejected(self, db, game):
        code, tokens = game
        submit(db, code, tokens[0])
        with pytest.raises(GameError, match="already locked"):
            submit(db, code, tokens[0], action="buy", commodity="spices", quantity=1)

    def test_request_id_cannot_be_replayed_by_another_player(self, db, game):
        code, tokens = game
        req = rid()
        submit(db, code, tokens[0], request_id=req)
        with pytest.raises(GameError):
            submit(db, code, tokens[1], request_id=req)

    def test_stale_round_rejected(self, db, game):
        code, tokens = game
        with pytest.raises(StaleError):
            submit(db, code, tokens[0], round_no=2)
        all_hold(db, code, tokens, 1)
        with pytest.raises(StaleError):
            submit(db, code, tokens[0], round_no=1)

    def test_insufficient_funds(self, db, game):
        code, tokens = game
        with pytest.raises(GameError, match="Insufficient"):
            submit(db, code, tokens[0], action="buy", commodity="relics", quantity=5)  # 125 > 100

    def test_sell_without_holding(self, db, game):
        code, tokens = game
        with pytest.raises(GameError, match="only hold"):
            submit(db, code, tokens[0], action="sell", commodity="relics", quantity=1)

    def test_inventory_full(self, db, game):
        code, tokens = game
        set_state(db, code, tokens[0], coins=1000, inventory={"spices": 9})
        with pytest.raises(GameError, match="Inventory full"):
            submit(db, code, tokens[0], action="buy", commodity="metals", quantity=2)

    @pytest.mark.parametrize("kw", [
        {"action": "buy", "commodity": "gold", "quantity": 1},
        {"action": "buy", "commodity": "spices", "quantity": 0},
        {"action": "buy", "commodity": "spices", "quantity": 9},
        {"action": "buy", "commodity": "spices", "quantity": "abc"},
        {"action": "buy", "commodity": "spices", "quantity": 1.5},
        {"action": "nuke"},
    ])
    def test_invalid_trades(self, db, game, kw):
        code, tokens = game
        with pytest.raises(GameError):
            submit(db, code, tokens[0], **kw)
        assert view(db, code, tokens[0])["me"]["submission"] is None

    def test_client_cannot_supply_price_or_balance(self, db, game):
        code, tokens = game
        with pytest.raises(TypeError):
            service.submit_action(db, code, tokens[0], 1, rid(), "buy", "spices", 1, price=1)  # type: ignore

    def test_cannot_play_someone_elses_card(self, db, game):
        code, tokens = game
        theirs = hand(db, code, tokens[1])[0]["card_id"]
        with pytest.raises(GameError, match="not in your hand"):
            submit(db, code, tokens[0], card_id=theirs)

    @pytest.mark.parametrize("bad", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_bogus_card_id(self, db, game, bad):
        code, tokens = game
        with pytest.raises(GameError):
            submit(db, code, tokens[0], card_id=bad)

    def test_card_spent_once(self, db, game):
        code, tokens = game
        cid = give_card(db, code, tokens[0], "shield")
        before = len(hand(db, code, tokens[0]))
        submit(db, code, tokens[0], card_id=cid)
        assert len(hand(db, code, tokens[0])) == before - 1
        for t in tokens[1:]:
            submit(db, code, t)
        with pytest.raises(GameError, match="not in your hand"):
            submit(db, code, tokens[0], round_no=2, card_id=cid)

    def test_card_targets_validated(self, db, game):
        code, tokens = game
        tax = give_card(db, code, tokens[0], "tax_collector")
        pump = give_card(db, code, tokens[0], "market_pump")
        with pytest.raises(GameError, match="yourself"):
            submit(db, code, tokens[0], card_id=tax, target_seat=1)
        with pytest.raises(GameError, match="opponent"):
            submit(db, code, tokens[0], card_id=tax, target_seat=9)
        with pytest.raises(GameError, match="commodity"):
            submit(db, code, tokens[0], card_id=pump, target_commodity="gold")
        submit(db, code, tokens[0], card_id=tax, target_seat=2)


# ----------------------------------------------------------- insider tip

class TestInsiderTip:
    def test_reveals_event_privately_and_idempotently(self, db, game):
        code, tokens = game
        cid = give_card(db, code, tokens[0], "insider_tip")
        req = rid()
        payload = service.use_insider_tip(db, code, tokens[0], 1, cid, req)
        assert payload["event"] in event_keys()
        assert service.use_insider_tip(db, code, tokens[0], 1, cid, req) == payload
        mine = view(db, code, tokens[0])
        assert mine["me"]["private_log"][0]["event"] == payload["event"]
        assert mine["me"]["card_played_this_round"] == "insider_tip"
        for t in tokens[1:]:
            assert payload["event"] not in view_json(view(db, code, t))

    def test_counts_as_the_round_card(self, db, game):
        code, tokens = game
        tip = give_card(db, code, tokens[0], "insider_tip")
        shield = give_card(db, code, tokens[0], "shield")
        service.use_insider_tip(db, code, tokens[0], 1, tip, rid())
        with pytest.raises(GameError, match="already played"):
            submit(db, code, tokens[0], card_id=shield)
        submit(db, code, tokens[0])  # trade without card is fine

    def test_not_after_submitting(self, db, game):
        code, tokens = game
        tip = give_card(db, code, tokens[0], "insider_tip")
        submit(db, code, tokens[0])
        with pytest.raises(GameError, match="locked"):
            service.use_insider_tip(db, code, tokens[0], 1, tip, rid())

    def test_wrong_card_type(self, db, game):
        code, tokens = game
        shield = give_card(db, code, tokens[0], "shield")
        with pytest.raises(GameError):
            service.use_insider_tip(db, code, tokens[0], 1, shield, rid())


# -------------------------------------------------------- secret isolation

class TestSecrets:
    def test_opponent_pending_trade_and_hand_hidden(self, db, game):
        code, tokens = game
        crash = give_card(db, code, tokens[0], "market_crash")
        a_hand_ids = [c["card_id"] for c in hand(db, code, tokens[0])]
        before = view(db, code, tokens[1])
        submit(db, code, tokens[0], action="buy", commodity="relics", quantity=3,
               card_id=crash, target_commodity="energy")
        b = view(db, code, tokens[1])
        # The ONLY things that change for an opponent are A's "submitted" flag and the version counter.
        before["room"]["version"] = b["room"]["version"]
        before["current"]["submitted_count"] += 1
        before["current"]["seconds_left"] = b["current"]["seconds_left"]
        next(p for p in before["players"] if p["seat"] == 1)["submitted"] = True
        assert b == before
        blob = view_json(b)
        assert not any(cid in blob for cid in a_hand_ids)
        a_row = next(p for p in b["players"] if p["seat"] == 1)
        assert a_row["submitted"] is True and set(a_row) == {
            "player_id", "seat", "name", "is_host", "is_ready", "online", "submitted", "is_me"}
        assert b["summaries"] == [] and b["me"]["submission"] is None

    def test_event_schedule_hidden(self, db, room4):
        code, tokens = room4
        for t in tokens:
            service.set_ready(db, code, t, True)
        with db.tx() as conn:
            events = [r["event_key"] for r in conn.execute(
                "select e.event_key from bluff.round_events e join bluff.rooms r on r.id = e.room_id "
                "where r.code = %s", (code,))]
        blob = view_json(view(db, code, tokens[0]))
        assert not any(f'"{e}"' in blob for e in events)
        assert "seed" not in blob and "token" not in blob

    def test_trades_public_after_resolution(self, db, game):
        code, tokens = game
        submit(db, code, tokens[0], action="buy", commodity="relics", quantity=3)
        for t in tokens[1:]:
            submit(db, code, t)
        s = view(db, code, tokens[1])["summaries"][0]
        assert s["players"][0]["trade"]["commodity"] == "relics" and s["event"] in event_keys()

    def test_rls_enabled_and_api_roles_locked_out(self, db):
        with db.tx() as conn:
            rows = conn.execute("select relname, relrowsecurity from pg_class c join pg_namespace n "
                                "on n.oid = c.relnamespace where n.nspname = 'bluff' and c.relkind = 'r'").fetchall()
            assert rows and all(r["relrowsecurity"] for r in rows)
            # No policy may open anything to the Supabase API roles (an optional bluff_app policy is fine).
            exposed = conn.execute(
                "select count(*) n from pg_policies where schemaname = 'bluff' "
                "and roles && array['anon', 'authenticated', 'public']::name[]").fetchone()["n"]
            assert exposed == 0

    def test_database_rejects_negative_coins(self, db, game):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.tx() as conn:
                conn.execute("update bluff.players set coins = -1")


# -------------------------------------------------------------- resolution

class TestRounds:
    def test_round_resolves_when_all_submit(self, db, game):
        code, tokens = game
        submit(db, code, tokens[0], action="buy", commodity="spices", quantity=4)
        submit(db, code, tokens[1], action="buy", commodity="spices", quantity=2)
        submit(db, code, tokens[2])
        assert view(db, code, tokens[0])["room"]["round"] == 1
        assert submit(db, code, tokens[3])["resolved"] is True
        v = view(db, code, tokens[0])
        assert v["room"]["round"] == 2 and len(v["summaries"]) == 1
        assert v["me"]["coins"] == 60 and v["me"]["inventory"]["spices"] == 4
        assert v["prices"] == v["summaries"][0]["prices_after"]
        assert len(v["price_history"]) == 2
        assert v["me"]["submission"] is None and all(not p["submitted"] for p in v["players"])

    def test_concurrent_final_submissions_resolve_exactly_once(self, db, game):
        code, tokens = game
        submit(db, code, tokens[0])
        submit(db, code, tokens[1])
        barrier = threading.Barrier(2)
        results, errors = [], []

        def go(t, kw):
            barrier.wait()
            try:
                results.append(service.submit_action(db, code, t, 1, rid(), **kw))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(tokens[2], {"action": "buy", "commodity": "metals", "quantity": 1})),
                   threading.Thread(target=go, args=(tokens[3], {"action": "hold"}))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert sorted(r["resolved"] for r in results) == [False, True]
        with db.tx() as conn:
            assert conn.execute("select count(*) n from bluff.rounds where status = 'resolved'").fetchone()["n"] == 1
            assert conn.execute("select count(*) n from bluff.submissions where round_no = 1").fetchone()["n"] == 4
        assert view(db, code, tokens[2])["me"]["inventory"]["metals"] == 1

    def test_four_simultaneous_players_many_rounds(self, db, game):
        code, tokens = game
        for r in (1, 2, 3):
            barrier = threading.Barrier(4)
            errors = []

            def go(t):
                barrier.wait()
                try:
                    service.submit_action(db, code, t, r, rid(), "buy", "spices", 1)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=go, args=(t,)) for t in tokens]
            [t.start() for t in threads]
            [t.join() for t in threads]
            assert not errors
        v = view(db, code, tokens[0])
        assert v["room"]["round"] == 4 and len(v["summaries"]) == 3
        assert v["me"]["inventory"]["spices"] == 3

    def test_concurrent_duplicate_requests_apply_once(self, db, game):
        code, tokens = game
        req = rid()
        barrier = threading.Barrier(5)
        outcomes = []

        def go():
            barrier.wait()
            outcomes.append(service.submit_action(db, code, tokens[0], 1, req, "buy", "relics", 2))

        threads = [threading.Thread(target=go) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(o["duplicate"] for o in outcomes) == [False, True, True, True, True]
        for t in tokens[1:]:
            submit(db, code, t)
        assert view(db, code, tokens[0])["me"]["coins"] == 50

    def test_card_effects_persist(self, db, game):
        code, tokens = game
        tax = give_card(db, code, tokens[0], "tax_collector")
        pump = give_card(db, code, tokens[1], "market_pump")
        submit(db, code, tokens[0], card_id=tax, target_seat=3)
        submit(db, code, tokens[1], card_id=pump, target_commodity="crystals")
        submit(db, code, tokens[2])
        submit(db, code, tokens[3])
        v = view(db, code, tokens[0])
        s = v["summaries"][0]
        assert v["me"]["coins"] == 115
        assert view(db, code, tokens[2])["me"]["coins"] == 85
        assert s["movement"]["crystals"]["cards"] == 5
        assert s["players"][1]["card"]["type"] == "market_pump"

    def test_timeout_auto_holds_disconnected_player(self, db, game):
        code, tokens = game
        for t in tokens[:3]:
            submit(db, code, t, action="buy", commodity="spices", quantity=1)
        assert service.resolve_if_expired(db, code) is False
        with db.tx() as conn:
            conn.execute("update bluff.rooms set round_deadline = now() - interval '1 second'")
        assert service.resolve_if_expired(db, code) is True
        assert service.resolve_if_expired(db, code) is False  # next round has a fresh timer
        v = view(db, code, tokens[3])
        summary = v["summaries"][0]
        assert summary["players"][3]["trade"]["auto"] is True
        assert v["room"]["round"] == 2

    def test_heartbeat_triggers_timeout_resolution(self, db, game):
        code, tokens = game
        with db.tx() as conn:
            conn.execute("update bluff.rooms set round_deadline = now() - interval '1 second'")
        service.touch(db, code, tokens[0])
        assert view(db, code, tokens[0])["room"]["round"] == 2

    def test_disconnect_and_rejoin_keeps_seat_and_state(self, db, game):
        code, tokens = game
        submit(db, code, tokens[1], action="buy", commodity="energy", quantity=2)
        with db.tx() as conn:
            conn.execute("update bluff.players set last_seen_at = now() - interval '5 minutes' "
                         "where token_hash = %s", (service.hash_token(tokens[1]),))
        others = view(db, code, tokens[0])
        assert next(p for p in others["players"] if p["seat"] == 2)["online"] is False
        # "New browser": same token from the URL restores the seat and pending submission.
        service.touch(db, code, tokens[1])
        back = view(db, code, tokens[1])
        assert back["me"]["seat"] == 2 and back["me"]["submission"]["commodity"] == "energy"
        assert next(p for p in back["players"] if p["seat"] == 2)["online"] is True

    def test_inactive_room_is_abandoned(self, db, game):
        code, tokens = game
        with db.tx() as conn:
            conn.execute("update bluff.rooms set last_activity_at = now() - interval '2 hours'")
        service.create_room(db, "Someone")  # housekeeping runs on room creation
        assert view(db, code, tokens[0])["room"]["status"] == "abandoned"
        with pytest.raises(StaleError, match="inactivity"):
            submit(db, code, tokens[0])


# --------------------------------------------------------------- full game

class TestFullGame:
    def test_eight_rounds_to_final_results(self, db, game):
        code, tokens = game
        for r in range(1, 9):
            v = view(db, code, tokens[0])
            assert v["room"]["round"] == r
            if v["me"]["coins"] >= v["prices"]["metals"] and sum(v["me"]["inventory"].values()) < 10:
                submit(db, code, tokens[0], r, action="buy", commodity="metals", quantity=1)
            else:
                submit(db, code, tokens[0], r)
            for t in tokens[1:]:
                submit(db, code, t, r)
        v = view(db, code, tokens[2])
        assert v["room"]["status"] == "finished" and v["current"] is None
        assert len(v["summaries"]) == 8 and len(v["price_history"]) == 9
        assert len(v["results"]) == 4 and v["results"][0]["rank"] == 1
        final_prices = v["price_history"][-1]
        for res in v["results"]:
            assert res["net_worth"] == res["coins"] + res["inventory_value"] + res["order_bonus"]
            assert res["order"]  # Full Bazaar: every order is revealed at the end
        a = next(x for x in v["results"] if x["seat"] == 1)
        inv = view(db, code, tokens[0])["me"]["inventory"]
        assert a["inventory_value"] == sum(inv[c] * final_prices[c] for c in COMMODITIES)
        with pytest.raises(StaleError, match="finished"):
            submit(db, code, tokens[0], 9)


# ------------------------------------------------------------------ pulse

class TestPulse:
    def test_pulse_tracks_locks_without_leaking(self, db, game):
        code, tokens = game
        p = service.get_pulse(db, code, tokens[1])
        assert (p["status"], p["round"], p["submitted"]) == ("active", 1, [])
        assert p["seconds_left"] > 0 and set(p["online"]) == {1, 2, 3, 4}
        submit(db, code, tokens[0], action="buy", commodity="relics", quantity=2)
        p = service.get_pulse(db, code, tokens[1])
        assert p["submitted"] == [1]
        assert set(p) == {"status", "round", "version", "seconds_left", "submitted", "online"}
        assert "relics" not in view_json(p)

    def test_pulse_requires_valid_token(self, db, game):
        code, _ = game
        with pytest.raises(AuthError):
            service.get_pulse(db, code, "forged")

    def test_pulse_sees_round_change_and_offline(self, db, game):
        code, tokens = game
        with db.tx() as conn:
            conn.execute("update bluff.players set last_seen_at = now() - interval '5 minutes' "
                         "where token_hash = %s", (service.hash_token(tokens[3]),))
        assert 4 not in service.get_pulse(db, code, tokens[0])["online"]
        all_hold(db, code, tokens, 1)
        p = service.get_pulse(db, code, tokens[0])
        assert p["round"] == 2 and p["submitted"] == []


# ------------------------------------------------------------ Full Bazaar rules

def start(db, name_cfg=None):
    code, t1 = service.create_room(db, "Ada", name_cfg)
    tokens = [t1] + [service.join_room(db, code, n) for n in ("Bo", "Cy", "Di")]
    for t in tokens:
        service.set_ready(db, code, t, True)
    from .conftest import set_events
    set_events(db, code)
    return code, tokens


def pid_of(db, code, token):
    with db.tx() as conn:
        return str(conn.execute("select p.id from bluff.players p join bluff.rooms r on r.id = p.room_id "
                                "where r.code = %s and p.token_hash = %s",
                                (code, service.hash_token(token))).fetchone()["id"])


class TestFullBazaar:
    def test_orders_dealt_privately(self, db, game):
        code, tokens = game
        orders = [view(db, code, t)["me"]["order"] for t in tokens]
        assert all(orders) and len(set(orders)) == 4
        for i, t in enumerate(tokens):
            blob = view_json(view(db, code, t))
            for j, other in enumerate(orders):
                if j != i:
                    assert f'"{other}"' not in blob  # opponents' orders stay secret during the game

    def test_public_round_rules(self, db, game):
        code, tokens = game
        v = view(db, code, tokens[0])
        assert v["config"]["rules"] == "full" and v["config"]["season"] in ("ship_delay", "godown_lock", "wet_godowns")
        lot = v["current"]["rules"]["lot"]
        assert lot["quantity"] in (2, 3) and lot["reserve"] == -(-lot["quantity"] * v["prices"][lot["commodity"]] // 2)
        assert v["current"]["rules"]["season"] is None  # season is rounds 4-6

    def test_bid_validation_storage_and_idempotency(self, db, game):
        code, tokens = game
        lot = view(db, code, tokens[0])["current"]["rules"]["lot"]
        with pytest.raises(GameError, match="Minimum bid"):
            submit(db, code, tokens[0], bid=lot["reserve"] - 1)
        with pytest.raises(GameError, match="whole number"):
            submit(db, code, tokens[0], bid="lots")
        req = rid()
        submit(db, code, tokens[0], request_id=req, bid=lot["reserve"])
        assert submit(db, code, tokens[0], request_id=req, bid=lot["reserve"])["duplicate"] is True
        assert view(db, code, tokens[0])["me"]["submission"]["bid"] == lot["reserve"]
        assert '"bid"' not in view_json(view(db, code, tokens[1])).replace('"bid": 0', "")  # opponents can't see it

    def test_auction_resolves_and_persists(self, db, game):
        code, tokens = game
        lot = view(db, code, tokens[0])["current"]["rules"]["lot"]
        submit(db, code, tokens[0], bid=lot["reserve"])
        submit(db, code, tokens[1], bid=lot["reserve"] + 5)
        submit(db, code, tokens[2])
        submit(db, code, tokens[3])
        v = view(db, code, tokens[1])
        a = v["summaries"][0]["auction"]
        assert a["winner"] == pid_of(db, code, tokens[1]) and a["price"] == lot["reserve"] + 5
        assert v["me"]["coins"] == 100 - lot["reserve"] - 5
        held = v["me"]["inventory"][lot["commodity"]]
        assert held == lot["quantity"] - (1 if lot["commodity"] in ("energy", "metals") and lot["quantity"] >= 3 else 0)

    def test_spoilage_persists(self, db, game):
        code, tokens = game
        set_state(db, code, tokens[0], inventory={"energy": 3, "relics": 4})
        all_hold(db, code, tokens, 1)
        inv = view(db, code, tokens[0])["me"]["inventory"]
        assert inv["energy"] == 2 and inv["relics"] == 4
        effects = view(db, code, tokens[0])["summaries"][0]["players"][0]["effects"]
        assert any(e["kind"] == "spoiled" for e in effects)

    def test_season_applies_in_round_four(self, db, game):
        code, tokens = game
        for r in (1, 2, 3):
            all_hold(db, code, tokens, r)
        v = view(db, code, tokens[0])
        assert v["current"]["rules"]["season"] == v["config"]["season"]

    def test_order_bonus_saved_in_results(self, db, game):
        code, tokens = game
        with db.tx() as conn:
            conn.execute("update bluff.player_orders set order_key = 'pepper_exporter' where player_id = %s",
                         (pid_of(db, code, tokens[0]),))
        set_state(db, code, tokens[0], inventory={"relics": 4})
        for r in range(1, 9):
            all_hold(db, code, tokens, r)
        res = {x["seat"]: x for x in view(db, code, tokens[1])["results"]}
        assert res[1]["order"] == "pepper_exporter" and res[1]["order_bonus"] == 25
        assert res[1]["net_worth"] == res[1]["coins"] + res[1]["inventory_value"] + 25

    def test_classic_room_has_no_new_rules(self, db):
        from bluff.config import GameConfig
        code, tokens = start(db, GameConfig(rules="classic"))
        v = view(db, code, tokens[0])
        assert v["config"]["rules"] == "classic" and v["config"]["season"] is None and v["me"]["order"] is None
        assert v["current"]["rules"]["lot"] is None
        with pytest.raises(GameError, match="no cargo"):
            submit(db, code, tokens[0], bid=10)
        set_state(db, code, tokens[0], inventory={"energy": 5})
        all_hold(db, code, tokens, 1)
        assert view(db, code, tokens[0])["me"]["inventory"]["energy"] == 5  # no spoilage

    def test_legacy_room_without_rules_key_stays_classic(self, db, room4):
        code, tokens = room4
        with db.tx() as conn:  # a room created before this release
            conn.execute("update bluff.rooms set config = config - 'rules' where code = %s", (code,))
        for t in tokens:
            service.set_ready(db, code, t, True)
        v = view(db, code, tokens[0])
        assert v["config"]["rules"] == "classic" and v["me"]["order"] is None


class TestMigrations:
    def test_migration_002_is_idempotent(self, db):
        from pathlib import Path
        sql = (Path(__file__).resolve().parent.parent / "migrations" / "002_full_bazaar.sql").read_text()
        with db.tx() as conn:
            conn.execute(sql)
            conn.execute(sql)
            assert conn.execute("select relrowsecurity from pg_class where oid = 'bluff.player_orders'::regclass"
                                ).fetchone()["relrowsecurity"]
