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
