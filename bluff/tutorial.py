"""Guided single-player tutorial ("Tutorial — Learn the Bazaar").

Pure module (no Streamlit, no database): the UI keeps a `TutorialState` in the browser session.
Every hands-on lesson is resolved by the real engine (`resolve_round`) with a scripted event and
scripted guide moves, and every explanation is computed from the engine's actual outcome, so the
text always matches what happened. Tutorial games are never stored, so they never count as results.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from . import theme
from .config import COMMODITIES, GameConfig
from .engine import (
    CardPlay, InvalidAction, Lot, PlayerState, RoundOutcome, RoundRules, Submission, final_ranking, lot_reserve,
    net_worth, resolve_round, validate_submission,
)

CONFIG = GameConfig(num_players=3)
YOU, GUIDE_A, GUIDE_B = "you", "guide_a", "guide_b"
NAMES = {YOU: "You", GUIDE_A: "Ammu (guide)", GUIDE_B: "Biju (guide)"}


@dataclass(frozen=True)
class Lesson:
    key: str
    title: str
    body: str
    kind: str = "info"                     # "info" (read and continue) or "act" (make a move)
    task: str = ""                         # what the player must do
    hint: str = ""
    event: str = "calm"                    # scripted hidden event for the round
    guides: tuple[tuple[str, Submission], ...] = ()
    hand: tuple[str, ...] = ()             # cards available in this lesson
    lot: Optional[Lot] = None
    needs_tip: bool = False                # must use Insider Tip before acting
    expected: Optional[Callable[["TutorialState"], Submission]] = None  # the move the lesson teaches


def _bid_for(state: "TutorialState", lot: Lot) -> int:
    return lot_reserve(CONFIG, lot, state.prices)


LESSONS: tuple[Lesson, ...] = (
    Lesson("goal", "Welcome to the bazaar",
           "You trade five goods against the market for 8 rounds. Everyone starts with 100 coins. "
           "Your **net worth** is your coins plus the value of your goods at current prices. "
           "Highest net worth after round 8 wins. Your two guides will play along."),
    Lesson("rumor", "Prices and rumors",
           "Each round shows the price of every good and one public **rumor**. The rumor names what the "
           "hidden market event is about, but never whether prices will go up or down. Everyone then picks "
           "one secret move at the same time; nobody sees anyone else's move until the round settles."),
    Lesson("buy", "Buying",
           "The rumor says traders are whispering about Thenga. Trades always settle at the price you see, "
           "then prices move from the event, cards and total demand.",
           kind="act", task="Buy 2 Thenga.", hint="Choose Buy → Thenga → 2, then lock in.",
           event="spices_boom", guides=((GUIDE_A, Submission("buy", "spices", 1)),),
           expected=lambda s: Submission("buy", "spices", 2)),
    Lesson("sell", "Selling",
           "Thenga went up. Selling turns goods back into coins at the current price.",
           kind="act", task="Sell your 2 Thenga.", hint="Choose Sell → Thenga → 2, then lock in.",
           expected=lambda s: Submission("sell", "spices", 2)),
    Lesson("card", "Playing a card",
           f"You hold 3 secret cards and may play one per round with your move. "
           f"{theme.card_label('market_pump')} pushes one good's price up by 25%.",
           kind="act", task=f"Buy 1 Kurumulaku and play {theme.card_name('market_pump')} on Kurumulaku.",
           hint=f"Buy → Kurumulaku → 1, choose {theme.card_name('market_pump')}, target Kurumulaku.",
           hand=("market_pump",),
           expected=lambda s: Submission("buy", "relics", 1, CardPlay("market_pump", "relics"))),
    Lesson("insider", "Inside information",
           f"{theme.card_label('insider_tip')} is used right away: it shows you this round's real event, "
           "and only you. It counts as your card for the round.",
           kind="act", task="Use the tip, then act on what it tells you: sell your Kurumulaku.",
           hint="Tap the tip button first, then Sell → Kurumulaku → 1.",
           event="relics_bust", hand=("insider_tip",), needs_tip=True,
           expected=lambda s: Submission("sell", "relics", 1)),
    Lesson("cargo", "Kappal Cargo auction",
           "In Full Bazaar games a cargo lot arrives every round. Add a secret bid to your move: the highest "
           "bid wins and pays it; exact ties mean nobody wins. The minimum bid is half the lot's value. "
           "Careful: holding 3+ Chakka or Nendran at the end of a round makes one spoil (fresh cargo is safe "
           "for the round you win it).",
           kind="act", task="Hold, and bid the minimum for the cargo.",
           hint="Keep Hold selected, enter the minimum bid shown, then lock in.",
           lot=Lot("crystals", 2, 1),
           expected=lambda s: Submission("hold", bid=_bid_for(s, Lot("crystals", 2, 1)))),
    Lesson("score", "Final scoring",
           "After round 8, net worth decides the winner (in Full Bazaar, plus your secret order's bonus if "
           "you met it). Here is how the table would rank right now."),
)


@dataclass
class TutorialState:
    step: int = 0
    prices: dict[str, int] = field(default_factory=lambda: dict(CONFIG.starting_prices))
    players: list[PlayerState] = field(default_factory=lambda: [
        PlayerState(pid, i + 1, NAMES[pid], CONFIG.starting_coins, {c: 0 for c in COMMODITIES})
        for i, pid in enumerate((YOU, GUIDE_A, GUIDE_B))])
    round_no: int = 1
    tip_used: bool = False
    outcome: Optional[dict[str, Any]] = None   # explanation of the current lesson's resolved round
    skipped: list[str] = field(default_factory=list)

    @property
    def lesson(self) -> Lesson:
        return LESSONS[self.step]

    @property
    def me(self) -> PlayerState:
        return self.players[0]

    @property
    def finished(self) -> bool:
        return self.step >= len(LESSONS) - 1 and (self.lesson.kind == "info" or self.outcome is not None)


def new_tutorial() -> TutorialState:
    return TutorialState()


def rules_for(lesson: Lesson) -> RoundRules:
    return RoundRules(lot=lesson.lot) if lesson.lot else RoundRules()


def use_tip(state: TutorialState) -> str:
    """Reveal the lesson's event (Insider Tip lesson only). Returns the event title shown to the player."""
    if not state.lesson.needs_tip:
        raise InvalidAction("There is no tip to use in this lesson.")
    state.tip_used = True
    return theme.event_title(state.lesson.event)


def check(state: TutorialState, sub: Submission) -> Optional[str]:
    """None if `sub` is what the lesson asks for and is legal; otherwise a hint for the player."""
    lesson = state.lesson
    if lesson.kind != "act" or state.outcome is not None:
        return "Nothing to do here — continue to the next lesson."
    if lesson.needs_tip and not state.tip_used:
        return "First use your tip to see the real event."
    try:
        validate_submission(CONFIG, state.me, state.prices, sub, list(lesson.hand),
                            [GUIDE_A, GUIDE_B], lesson.needs_tip and state.tip_used, rules_for(lesson))
    except InvalidAction as exc:
        return f"{exc} {lesson.hint}"
    if sub != lesson.expected(state):
        return f"This lesson asks you to: {lesson.task} {lesson.hint}"
    return None


def play(state: TutorialState, sub: Submission) -> dict[str, Any]:
    """Resolve the lesson's round with the engine and return an explanation built from the result."""
    problem = check(state, sub)
    if problem:
        raise InvalidAction(problem)
    lesson = state.lesson
    guide_moves = dict(lesson.guides)
    subs = {YOU: sub, GUIDE_A: guide_moves.get(GUIDE_A, Submission.hold()),
            GUIDE_B: guide_moves.get(GUIDE_B, Submission.hold())}
    before = net_worth(state.me, state.prices)
    out = resolve_round(CONFIG, state.round_no, state.prices, state.players, subs, lesson.event, rules_for(lesson))
    state.outcome = explain(lesson, out, before)
    state.players, state.prices = out.players, out.prices
    state.round_no += 1
    return state.outcome


def explain(lesson: Lesson, out: RoundOutcome, before: int) -> dict[str, Any]:
    """Plain-language explanation computed only from the engine's outcome."""
    row = next(r for r in out.summary["players"] if r["player_id"] == YOU)
    lines = [f"Event: {theme.event_title(out.summary['event'])} — {theme.event_display(out.summary['event'])['text']}"]
    trade = row["trade"]
    if trade["executed"]:
        verb = "bought" if trade["action"] == "buy" else "sold"
        lines.append(f"You {verb} {trade['quantity']} {theme.commodity_name(trade['commodity'])} "
                     f"at {trade['price']} each.")
    for c in COMMODITIES:
        m = out.summary["movement"][c]
        if m["before"] != m["after"]:
            parts = [f"{label} {m[k]:+d}" for label, k in (("event", "event"), ("cards", "cards"), ("demand", "demand"))
                     if m[k]]
            lines.append(f"{theme.commodity_name(c)} moved {m['before']} → {m['after']} ({', '.join(parts)}).")
    auction = out.summary.get("auction")
    if auction and auction["winner"] == YOU:
        lines.append(f"You won {auction['quantity']} {theme.commodity_name(auction['commodity'])} for "
                     f"{auction['price']} coins (worth {row['breakdown']['cargo'] + auction['price']}).")
    after = row["net_worth"]
    lines.append(f"Your net worth: {before} → {after} ({after - before:+d}).")
    return {"lines": lines, "before": before, "after": after, "summary": out.summary, "breakdown": row["breakdown"]}


def advance(state: TutorialState) -> None:
    """Go to the next lesson (hands-on lessons must be played or skipped first)."""
    if state.lesson.kind == "act" and state.outcome is None:
        raise InvalidAction("Finish or skip this lesson first.")
    if state.step < len(LESSONS) - 1:
        state.step += 1
        state.outcome = None
        state.tip_used = False


def skip(state: TutorialState) -> None:
    """Skip the current lesson. Hands-on lessons auto-play their move so later lessons stay legal."""
    lesson = state.lesson
    if lesson.kind == "act" and state.outcome is None:
        if lesson.needs_tip:
            state.tip_used = True
        play(state, lesson.expected(state))
        state.skipped.append(lesson.key)
    advance(state)


def standings(state: TutorialState) -> list[dict[str, Any]]:
    return final_ranking(state.players, state.prices)
