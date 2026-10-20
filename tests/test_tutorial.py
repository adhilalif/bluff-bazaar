"""Guided tutorial: completable, skippable, restartable; explanations match the engine."""
import re

import pytest

from bluff import tutorial
from bluff.engine import CardPlay, InvalidAction, Submission
from bluff.tutorial import LESSONS, advance, check, new_tutorial, play, skip, standings, use_tip


def complete_all(state):
    while True:
        lesson = state.lesson
        if lesson.kind == "act":
            if lesson.needs_tip:
                use_tip(state)
            play(state, lesson.expected(state))
        if state.finished:
            return state
        advance(state)


def test_completes_with_taught_moves():
    state = complete_all(new_tutorial())
    assert state.finished and state.skipped == []
    assert state.lesson.key == "score"
    ranking = standings(state)
    assert {r["player_id"] for r in ranking} == {"you", "guide_a", "guide_b"}


def test_every_taught_move_is_legal_and_accepted():
    state = new_tutorial()
    for _ in LESSONS:
        lesson = state.lesson
        if lesson.kind == "act":
            if lesson.needs_tip:
                use_tip(state)
            assert check(state, lesson.expected(state)) is None, lesson.key
            play(state, lesson.expected(state))
        if state.finished:
            break
        advance(state)


def test_explanations_match_engine_outcome():
    state = new_tutorial()
    advance(state)
    advance(state)  # -> buy lesson
    out = play(state, Submission("buy", "spices", 2))
    summary = out["summary"]
    m = summary["movement"]["spices"]
    text = " ".join(out["lines"])
    assert f"moved {m['before']} → {m['after']}" in text
    assert f"event {m['event']:+d}" in text and f"demand {m['demand']:+d}" in text
    me = next(r for r in summary["players"] if r["player_id"] == "you")
    assert out["after"] == me["net_worth"] and f"→ {me['net_worth']}" in text
    assert out["after"] - out["before"] == sum(out["breakdown"][k] for k in ("price_moves", "cards_events", "cargo", "spoiled"))
    nums = [int(x) for x in re.findall(r"at (\d+) each", text)]
    assert nums == [me["trade"]["price"]]


def test_wrong_and_illegal_moves_get_hints_not_applied():
    state = new_tutorial()
    advance(state)
    advance(state)
    assert "asks you to" in check(state, Submission("buy", "relics", 1))
    assert "Insufficient" in check(state, Submission("buy", "relics", 5))
    with pytest.raises(InvalidAction):
        play(state, Submission.hold())
    assert state.outcome is None and state.me.coins == 100
    with pytest.raises(InvalidAction, match="Finish or skip"):
        advance(state)


def test_card_lesson_requires_the_card():
    state = new_tutorial()
    for _ in range(4):
        skip(state)
    assert state.lesson.key == "card"
    assert check(state, Submission("buy", "relics", 1)) is not None
    assert check(state, Submission("buy", "relics", 1, CardPlay("market_pump", "relics"))) is None


def test_insider_lesson_requires_tip_first():
    state = new_tutorial()
    while state.lesson.key != "insider":
        skip(state)
    assert "use your tip" in check(state, Submission("sell", "relics", 1))
    title = use_tip(state)
    assert "Vila Poyi" in title
    out = play(state, Submission("sell", "relics", 1))
    assert out["summary"]["movement"]["relics"]["event"] < 0


def test_skip_every_lesson_keeps_state_legal():
    state = new_tutorial()
    while not state.finished:
        skip(state)
    assert state.lesson.key == "score"
    assert set(state.skipped) == {l.key for l in LESSONS if l.kind == "act"}
    assert all(p.coins >= 0 for p in state.players)


def test_restart_is_a_fresh_state():
    state = complete_all(new_tutorial())
    fresh = new_tutorial()
    assert fresh.step == 0 and fresh.me.coins == 100 and fresh.round_no == 1 and state.round_no > 1


def test_cargo_lesson_wins_the_lot():
    state = new_tutorial()
    while state.lesson.key != "cargo":
        skip(state)
    sub = state.lesson.expected(state)
    out = play(state, sub)
    assert out["summary"]["auction"]["winner"] == "you" and out["summary"]["auction"]["price"] == sub.bid
    assert any("You won" in line for line in out["lines"])


def test_tutorial_never_touches_the_database():
    """Tutorial games are not stored, so they can never count as results."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(tutorial.__file__).read_text())
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"service", "db", "psycopg", "bluff.service", "bluff.db"}
