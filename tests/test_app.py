"""End-to-end UI tests: drive the real app.py with Streamlit's AppTest against the test database.

The other three players are driven through the service layer, exactly as their
own browser sessions would be.
"""
import os
from pathlib import Path

import pytest

from bluff import service

from .conftest import TEST_DSN, rid, set_events

APP = str(Path(__file__).resolve().parent.parent / "app.py")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def app_env(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    from streamlit.testing.v1 import AppTest
    return lambda: AppTest.from_file(APP, default_timeout=30)


def page_text(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown) + "\n".join(str(w.value) for w in at.warning)


def qp(at, key):
    v = at.query_params[key]
    return v[0] if isinstance(v, list) else v


def button(at, label_start):
    return next(b for b in at.button if b.label.startswith(label_start))


def create_game(app_env, db):
    at = app_env().run()
    assert not at.exception
    at.text_input[0].input("Ada")
    button(at, "Create room").click().run()
    code, token = qp(at, "room"), qp(at, "t")
    assert "ROOM CODE" in page_text(at) and code in page_text(at)
    bots = [service.join_room(db, code, n) for n in ("Bo", "Cy", "Di")]
    for t in bots:
        service.set_ready(db, code, t, True)
    button(at, "✅ I'm ready").click().run()
    assert not at.exception
    set_events(db, code)
    return at, code, token, bots


def test_home_renders(app_env):
    at = app_env().run()
    assert not at.exception
    assert "Bluff Bazaar" in page_text(at) and "The Malabar Trade Game" in page_text(at)
    assert [b.label for b in at.button] == ["Create room", "Join room"]


def test_join_by_code_via_ui(app_env, db):
    code, _ = service.create_room(db, "Host")
    at = app_env()
    at.query_params["join"] = code
    at.run()
    assert at.text_input[1].value == code
    at.text_input[2].input("Guest")
    button(at, "Join room").click().run()
    assert qp(at, "room") == code and "Traders 2/4" in page_text(at)


def test_full_round_through_ui(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    text = page_text(at)
    assert "Round 1/8" in text and "Rumor" in text

    at.button_group(key="act_1").set_value("buy").run()
    at.button_group(key="com_1_buy").set_value("spices").run()
    at.button_group(key="qty_1_buy").set_value(2).run()
    assert "2 × 10 = <b>20 coins</b>" in page_text(at)
    button(at, "🔒 Lock in move").click().run()
    assert not at.exception
    assert "Move accepted" in page_text(at) and "Buy 2 × Thenga" in page_text(at)

    for t in bots:
        service.submit_action(db, code, t, 1, rid(), "hold")
    at.run()
    text = page_text(at)
    assert "Round 2/8" in text
    assert "bought 2 Thenga @ 10" in text
    assert '<div class="v">80</div><div class="l">Coins</div>' in text  # bought 2 Thenga at 10
    assert "Round 1 settled" in text


def test_insufficient_funds_blocks_lock_in(app_env, db):
    at, *_ = create_game(app_env, db)
    at.button_group(key="act_1").set_value("buy").run()
    at.button_group(key="com_1_buy").set_value("relics").run()
    at.button_group(key="qty_1_buy").set_value(5).run()
    assert any("Insufficient funds" in w.value for w in at.warning)
    assert button(at, "🔒 Lock in move").disabled


def test_opponent_moves_not_shown_before_resolution(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    service.submit_action(db, code, bots[0], 1, rid(), "buy", "relics", 3)
    at.run()
    text = page_text(at)
    assert "1/4 locked in" in text
    assert "bought 3" not in text and "Buy 3" not in text


def test_stale_session_link(app_env, db):
    code, _ = service.create_room(db, "Host")
    at = app_env()
    at.query_params["room"] = code
    at.query_params["t"] = "not-a-real-token"
    at.run()
    assert not at.exception
    assert any("no longer valid" in w.value for w in at.warning)


def test_rejoin_from_url_restores_seat(app_env, db):
    _, code, token, bots = create_game(app_env, db)
    service.submit_action(db, code, bots[1], 1, rid(), "hold")
    fresh = app_env()  # a brand-new browser session that only has the URL
    fresh.query_params["room"] = code
    fresh.query_params["t"] = bots[1]
    fresh.run()
    assert "Move accepted" in page_text(fresh) and "Hold" in page_text(fresh)


def test_final_results_screen(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    for r in range(1, 9):
        for t in [token] + bots:
            service.submit_action(db, code, t, r, rid(), "hold")
    at.run()
    assert not at.exception
    text = page_text(at)
    assert "wins the bazaar" in text or "win the bazaar" in text
    assert "bb-podium" in text and "bb-table" in text
    assert "Adipoli! Bazaarile Raja!" in text


def test_translation_toggle_persists_in_url(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    visible_en = '<div class="bb-en">Coconut</div>'
    assert visible_en in page_text(at)
    at.toggle(key="show_en").set_value(False).run()
    assert not at.exception
    assert visible_en not in page_text(at) and "Thenga" in page_text(at)
    assert qp(at, "en") == "0" and qp(at, "room") == code
    # A fresh browser session opened from the same URL keeps the preference and the seat.
    fresh = app_env()
    for k in ("room", "t", "en"):
        fresh.query_params[k] = qp(at, k)
    fresh.run()
    assert visible_en not in page_text(fresh) and "Your secret move" in page_text(fresh)


def test_timeout_notice_and_one_toast_per_round(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    for t in bots:
        service.submit_action(db, code, t, 1, rid(), "hold")
    with db.tx() as conn:
        conn.execute("update bluff.rooms set round_deadline = now() - interval '1 second' where code = %s", (code,))
    service.resolve_if_expired(db, code)
    at.run()
    text = page_text(at)
    assert "Round 2/8" in text and "Time ran out last round" in text
    assert len(at.toast) == 1 and "Round 1 settled" in at.toast[0].value
    at.run()  # polling rerun: no repeated toast
    assert len(at.toast) == 0


def test_double_down_lock_in_confirms_with_reaction(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    from .test_service import give_card
    give_card(db, code, token, "double_down")
    at.run()
    at.button_group(key="act_1").set_value("buy").run()
    at.button_group(key="com_1_buy").set_value("spices").run()
    dd = next(o for o in at.radio(key="card_1").options if "Iratti Labham" in o)
    at.radio(key="card_1").set_value(next(
        v for v in service.get_view(db, code, token)["me"]["hand"] if v["card_type"] == "double_down")["card_id"]).run()
    assert dd
    button(at, "🔒 Lock in move").click().run()
    assert not at.exception
    assert any("Risk edukkua alle?" in t.value and "Move accepted" in t.value for t in at.toast)
    assert "Move accepted" in page_text(at) and "Iratti Labham" in page_text(at)


def test_full_bazaar_panels_and_cargo_bid(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    text = page_text(at)
    assert "Secret order" in text and "Only you can see this" in text
    assert "Kappal Cargo" in text and "rounds 4–6" in text
    lot = service.get_view(db, code, token)["current"]["rules"]["lot"]
    at.number_input(key="bid_1").set_value(lot["reserve"]).run()
    button(at, "🔒 Lock in move").click().run()
    assert not at.exception
    assert f'cargo bid {lot["reserve"]}' in page_text(at)
    for t in bots:
        service.submit_action(db, code, t, 1, rid(), "hold")
    at.run()
    text = page_text(at)
    assert "won by" in text and "Ada" in text  # the summary names the cargo winner
    assert "Cargo" in text and "bb-break" in text  # reconciled breakdown line


def test_bid_below_reserve_blocks_lock_in(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    lot = service.get_view(db, code, token)["current"]["rules"]["lot"]
    at.number_input(key="bid_1").set_value(max(1, lot["reserve"] - 1)).run()
    assert any("Minimum bid" in w.value for w in at.warning)
    assert button(at, "🔒 Lock in move").disabled


def test_classic_mode_from_home(app_env, db):
    at = app_env().run()
    at.text_input[0].input("Ada")
    at.radio[0].set_value("classic")  # form widgets are submitted together with the button
    button(at, "Create room").click().run()
    code, token = qp(at, "room"), qp(at, "t")
    assert "Classic" in page_text(at)
    for n in ("Bo", "Cy", "Di"):
        service.set_ready(db, code, service.join_room(db, code, n), True)
    button(at, "✅ I'm ready").click().run()
    text = page_text(at)
    assert "Your secret move" in text and "Secret order" not in text and "Kappal Cargo" not in text
    assert not [n for n in at.number_input if n.key == "bid_1"]


def test_final_screen_reveals_orders(app_env, db):
    at, code, token, bots = create_game(app_env, db)
    for r in range(1, 9):
        for t in [token] + bots:
            service.submit_action(db, code, t, r, rid(), "hold")
    at.run()
    text = page_text(at)
    assert "Secret order" in text and 'data-label="Bonus"' in text


# ------------------------------------------------------------ player modes + tutorial

def start_tutorial(app_env):
    at = app_env().run()
    at.button_group(key="home_mode").set_value("tutorial").run()
    button(at, "🎓 Start the tutorial").click().run()
    assert not at.exception and "Lesson 1/" in page_text(at)
    return at


def play_lesson_via_ui(at):
    """Drive the widgets to the move the current lesson teaches, then lock in."""
    from bluff import tutorial
    state = at.session_state["tutorial"]
    lesson, step = state.lesson, state.step
    if lesson.needs_tip:
        button(at, "🔍 Use").click().run()
    sub = lesson.expected(state)
    at.button_group(key=f"tut_act_{step}").set_value(sub.action).run()
    if sub.action != "hold":
        at.button_group(key=f"tut_com_{step}_{sub.action}").set_value(sub.commodity).run()
        at.button_group(key=f"tut_qty_{step}_{sub.action}").set_value(sub.quantity).run()
    if sub.card:
        at.radio(key=f"tut_card_{step}").set_value(sub.card.card_type).run()
        at.button_group(key=f"tut_tc_{step}").set_value(sub.card.target_commodity).run()
    if sub.bid:
        at.number_input(key=f"tut_bid_{step}").set_value(sub.bid).run()
    button(at, "🔒 Lock in move").click().run()
    assert not at.exception
    assert "What happened" in page_text(at), [w.value for w in at.warning]


def test_tutorial_completes_through_the_ui(app_env):
    at = start_tutorial(app_env)
    for _ in range(20):
        state = at.session_state["tutorial"]
        if state.lesson.key == "score":
            break
        if state.lesson.kind == "act":
            play_lesson_via_ui(at)
        button(at, "Next lesson").click().run()
    assert any("Tutorial complete" in s.value for s in at.success) and "bb-table" in page_text(at)
    assert at.session_state["tutorial"].skipped == []  # every lesson was actually played
    button(at, "⛵ Finish").click().run()
    assert "tutorial" not in at.session_state and at.button_group(key="home_mode")


def test_tutorial_wrong_move_shows_hint(app_env):
    at = start_tutorial(app_env)
    button(at, "Next lesson").click().run()
    button(at, "Next lesson").click().run()  # buy lesson
    button(at, "🔒 Lock in move").click().run()  # still "hold"
    assert any("asks you to" in w.value for w in at.warning)
    assert at.session_state["tutorial"].outcome is None


def test_tutorial_skip_restart_exit(app_env, db):
    at = start_tutorial(app_env)
    for _ in range(3):
        button(at, "Skip lesson").click().run()
    assert at.session_state["tutorial"].step == 3
    button(at, "Restart").click().run()
    assert at.session_state["tutorial"].step == 0 and "Lesson 1/" in page_text(at)
    button(at, "Exit tutorial").click().run()
    assert "tutorial" not in at.session_state
    with db.tx() as conn:  # nothing about the tutorial was stored
        assert conn.execute("select count(*) n from bluff.rooms").fetchone()["n"] == 0


def test_two_player_room_from_start_screen(app_env, db):
    at = app_env().run()
    at.button_group(key="home_mode").set_value("2").run()
    assert "Head-to-Head" in page_text(at)
    at.text_input[0].input("Ada")
    button(at, "Create room").click().run()
    code, token = qp(at, "room"), qp(at, "t")
    text = page_text(at)
    assert "Traders 1/2" in text and "2 Players — Head-to-Head" in text
    bo = service.join_room(db, code, "Bo")
    with pytest.raises(service.GameError, match="full"):
        service.join_room(db, code, "Cy")
    service.set_ready(db, code, bo, True)
    at.run()
    button(at, "✅ I'm ready").click().run()
    text = page_text(at)
    assert "0/2 locked in" in text and "2 Players · Full Bazaar" in text
    button(at, "🔒 Lock in move").click().run()
    service.submit_action(db, code, bo, 1, rid(), "hold")
    at.run()
    assert "Round 2/8" in page_text(at)
