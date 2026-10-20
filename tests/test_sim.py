"""The simulator is deterministic and runs the real engine without errors."""
from bluff.config import GameConfig
from bluff.sim.bots import STRATEGIES
from bluff.sim.runner import mixed_schedule, play_game, run, summarize

NAMES = ["passive", "random", "trend", "pump", "insider", "order"]


def test_same_seed_same_game():
    lineup = [STRATEGIES[n] for n in ("random", "trend", "insider", "order")]
    for cfg in (GameConfig(), GameConfig(rules="classic")):
        a, b = play_game(cfg, 42, lineup), play_game(cfg, 42, lineup)
        assert a.ranking == b.ranking and a.prices_by_round == b.prices_by_round and a.card_plays == b.card_plays


def test_mixed_schedule_is_balanced():
    lineups = mixed_schedule(NAMES[:5], 100)
    counts = {n: sum(lu.count(n) for lu in lineups) for n in NAMES[:5]}
    assert len(set(counts.values())) == 1
    seats = {n: [sum(1 for lu in lineups if lu[s] == n) for s in range(4)] for n in NAMES[:5]}
    assert all(max(v) - min(v) <= 5 for v in seats.values())


def test_simulations_have_no_invariant_errors():
    for cfg in (GameConfig(), GameConfig(rules="classic")):
        s = summarize(run(cfg, mixed_schedule(NAMES, 120), STRATEGIES))
        assert s["invariant_errors"] == []
        assert s["strategies"]["passive"]["mean"] <= s["strategies"]["trend"]["mean"]


def test_breakdown_sources_reconcile_with_final_worth():
    res = play_game(GameConfig(rules="classic"), 7, [STRATEGIES["trend"]] * 4)
    for row in res.ranking:
        src = res.sources[row["seat"] - 1]
        assert row["net_worth"] - 100 == sum(src.values())
