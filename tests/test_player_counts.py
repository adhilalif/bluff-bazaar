"""Engine behaviour at 2, 3 and 4 players (no database)."""
from collections import Counter

import pytest

from bluff.config import CARD_TYPES, COMMODITIES, DEFAULT_STARTING_PRICES, GameConfig
from bluff.engine import (
    CardPlay, InvalidAction, PlayerState, Submission, deal_hands, deal_orders, final_ranking, resolve_round,
    round_rules, validate_submission,
)
from bluff.sim.bots import STRATEGIES
from bluff.sim.runner import play_game

PRICES = dict(DEFAULT_STARTING_PRICES)


def players(n, coins=100):
    return [PlayerState(f"p{i}", i, f"P{i}", coins, {c: 0 for c in COMMODITIES}) for i in range(1, n + 1)]


@pytest.mark.parametrize("n", [2, 3, 4])
def test_deal_scales_with_players(n):
    cfg = GameConfig(num_players=n)
    ids = [f"p{i}" for i in range(n)]
    hands = deal_hands(9, ids, cfg)
    assert len(hands) == n and all(len(h) == cfg.hand_size for h in hands.values())
    dealt = Counter(c for h in hands.values() for c in h)
    assert all(v <= cfg.deck_copies for v in dealt.values()) and set(dealt) <= set(CARD_TYPES)
    assert len(set(deal_orders(9, ids).values())) == n
    with pytest.raises(InvalidAction):
        deal_hands(9, ids[:-1], cfg)


@pytest.mark.parametrize("n", [2, 3])
def test_resolution_with_fewer_players(n):
    cfg = GameConfig(num_players=n)
    ps = players(n)
    subs = {p.player_id: Submission("buy", "spices", 2) for p in ps}
    out = resolve_round(cfg, 1, PRICES, ps, subs, "calm", round_rules(cfg, 3, 1))
    assert len(out.players) == n and all(p.inventory["spices"] == 2 for p in out.players)
    assert out.summary["movement"]["spices"]["net_units"] == 2 * n


def test_resolution_requires_every_seated_player():
    cfg = GameConfig(num_players=2)
    with pytest.raises(InvalidAction):
        resolve_round(cfg, 1, PRICES, players(2), {"p1": Submission.hold()}, "calm")


def test_tax_collector_with_a_single_opponent():
    cfg = GameConfig(num_players=2)
    ps = players(2)
    validate_submission(cfg, ps[0], PRICES, Submission(card=CardPlay("tax_collector", target_player="p2")),
                        ["tax_collector"], ["p2"])
    with pytest.raises(InvalidAction, match="yourself"):
        validate_submission(cfg, ps[0], PRICES, Submission(card=CardPlay("tax_collector", target_player="p1")),
                            ["tax_collector"], ["p2"])
    with pytest.raises(InvalidAction, match="opponent"):  # a seat that does not exist in a 2-player game
        validate_submission(cfg, ps[0], PRICES, Submission(card=CardPlay("tax_collector", target_player="p3")),
                            ["tax_collector"], ["p2"])
    out = resolve_round(cfg, 1, PRICES, ps, {"p1": Submission(card=CardPlay("tax_collector", target_player="p2")),
                                             "p2": Submission.hold()}, "calm")
    assert [p.coins for p in out.players] == [115, 85]


def test_invalid_tax_target_at_resolution_is_harmless():
    out = resolve_round(GameConfig(num_players=2), 1, PRICES, players(2),
                        {"p1": Submission(card=CardPlay("tax_collector", target_player="ghost")),
                         "p2": Submission.hold()}, "calm")
    assert [p.coins for p in out.players] == [100, 100]


@pytest.mark.parametrize("n", [2, 3])
def test_two_way_cargo_tie_and_ranking(n):
    cfg = GameConfig(num_players=n)
    rules = round_rules(cfg, 5, 1)
    reserve = -(-rules.lot.quantity * PRICES[rules.lot.commodity] // 2)
    subs = {p.player_id: Submission(bid=reserve) for p in players(n)}
    out = resolve_round(cfg, 1, PRICES, players(n), subs, "calm", rules)
    assert out.summary["auction"]["outcome"] == "tie"
    ranking = final_ranking(out.players, out.prices)
    assert [r["rank"] for r in ranking] == [1] * n  # identical players share first place


@pytest.mark.parametrize("n", [2, 3, 4])
@pytest.mark.parametrize("rules", ["full", "classic"])
def test_full_games_at_every_count(n, rules):
    cfg = GameConfig(num_players=n, rules=rules)
    lineup = [STRATEGIES[s] for s in ("trend", "random", "insider", "pump")[:n]]
    res = play_game(cfg, 77, lineup)
    assert len(res.ranking) == n and res.invariant_errors == []
    assert play_game(cfg, 77, lineup).ranking == res.ranking  # deterministic
