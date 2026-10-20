"""Unit tests for every rule in docs/RULES.md (pure engine, no database)."""
from collections import Counter

import pytest

from bluff.config import CARD_TYPES, COMMODITIES, DEFAULT_STARTING_PRICES, GameConfig, event_keys
from bluff.engine import (
    CardPlay, InvalidAction, PlayerState, Submission, deal_hands, demand_delta, final_ranking,
    move_price, net_worth, resolve_round, schedule_events, validate_submission,
)

CFG = GameConfig()
PRICES = dict(DEFAULT_STARTING_PRICES)  # crystals 20, spices 10, energy 15, metals 12, relics 25


def players(coins=(100, 100, 100, 100), inventories=None):
    inventories = inventories or [{}, {}, {}, {}]
    return [PlayerState(f"p{i}", i, f"P{i}", coins[i - 1], dict(inventories[i - 1])) for i in range(1, 5)]


def holds(**overrides):
    subs = {f"p{i}": Submission.hold() for i in range(1, 5)}
    subs.update(overrides)
    return subs


def by_id(outcome):
    return {p.player_id: p for p in outcome.players}


def summary_for(outcome, pid):
    return next(r for r in outcome.summary["players"] if r["player_id"] == pid)


def validate(sub, player=None, hand=CARD_TYPES, played=False, prices=PRICES):
    player = player or players()[0]
    validate_submission(CFG, player, prices, sub, hand, ["p2", "p3", "p4"], played)


# --------------------------------------------------------------------- config

class TestConfig:
    def test_defaults(self):
        assert CFG.num_players == 4 and CFG.num_rounds == 8 and CFG.starting_coins == 100
        assert len(COMMODITIES) == 5 and len(CARD_TYPES) == 6

    def test_round_trip(self):
        cfg = GameConfig(starting_prices={**PRICES, "spices": 30})
        assert GameConfig.from_dict(cfg.to_dict()) == cfg

    def test_configurable_starting_prices_validated(self):
        with pytest.raises(ValueError):
            GameConfig(starting_prices={**PRICES, "relics": 500})
        with pytest.raises(ValueError):
            GameConfig(starting_prices={"crystals": 10})

    def test_exactly_four_players(self):
        with pytest.raises(ValueError):
            GameConfig(num_players=3)


# ---------------------------------------------------------------------- setup

class TestSetup:
    def test_schedule_is_deterministic_and_unique(self):
        a, b = schedule_events(42, CFG), schedule_events(42, CFG)
        assert a == b and len(a) == 8 and len(set(a)) == 8
        assert schedule_events(43, CFG) != a

    def test_deal_uses_whole_deck(self):
        ids = ["a", "b", "c", "d"]
        hands = deal_hands(7, ids, CFG)
        assert hands == deal_hands(7, ids, CFG)
        assert all(len(h) == 3 for h in hands.values())
        dealt = Counter(c for h in hands.values() for c in h)
        assert dealt == Counter({c: 2 for c in CARD_TYPES})

    def test_deal_requires_four(self):
        with pytest.raises(InvalidAction):
            deal_hands(1, ["a", "b"], CFG)


# ----------------------------------------------------------------- validation

class TestValidation:
    def test_hold_ok(self):
        validate(Submission.hold())

    def test_hold_with_quantity_rejected(self):
        with pytest.raises(InvalidAction):
            validate(Submission("hold", "spices", 1))

    @pytest.mark.parametrize("sub", [
        Submission("steal", "spices", 1),
        Submission("buy", "gold", 1),
        Submission("buy", None, 1),
        Submission("buy", "spices", 0),
        Submission("buy", "spices", -2),
        Submission("buy", "spices", 6),
        Submission("buy", "spices", 1.5),
        Submission("buy", "spices", True),
    ])
    def test_invalid_trades(self, sub):
        with pytest.raises(InvalidAction):
            validate(sub)

    def test_insufficient_funds(self):
        poor = PlayerState("p1", 1, "P1", 49, {})
        with pytest.raises(InvalidAction, match="Insufficient funds"):
            validate(Submission("buy", "relics", 2), poor)  # 50 > 49
        validate(Submission("buy", "relics", 1), poor)

    def test_exact_funds_allowed(self):
        validate(Submission("buy", "relics", 4), PlayerState("p1", 1, "P1", 100, {}))

    def test_inventory_full(self):
        full = PlayerState("p1", 1, "P1", 1000, {"spices": 8})
        validate(Submission("buy", "spices", 2), full)
        with pytest.raises(InvalidAction, match="Inventory full"):
            validate(Submission("buy", "spices", 3), full)

    def test_cannot_sell_what_you_do_not_hold(self):
        p = PlayerState("p1", 1, "P1", 0, {"metals": 2})
        validate(Submission("sell", "metals", 2), p)
        with pytest.raises(InvalidAction):
            validate(Submission("sell", "metals", 3), p)
        with pytest.raises(InvalidAction):
            validate(Submission("sell", "relics", 1), p)

    def test_card_must_be_in_hand(self):
        with pytest.raises(InvalidAction, match="not in your hand"):
            validate(Submission(card=CardPlay("shield")), hand=["double_down"])

    def test_one_card_per_round(self):
        with pytest.raises(InvalidAction, match="already played"):
            validate(Submission(card=CardPlay("shield")), played=True)

    def test_insider_tip_not_submittable(self):
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("insider_tip")))

    def test_pump_needs_commodity(self):
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("market_pump")))
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("market_crash", "spices", "p2")))
        validate(Submission(card=CardPlay("market_pump", "spices")))

    def test_tax_needs_opponent(self):
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("tax_collector")))
        with pytest.raises(InvalidAction, match="yourself"):
            validate(Submission(card=CardPlay("tax_collector", target_player="p1")))
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("tax_collector", target_player="ghost")))
        validate(Submission(card=CardPlay("tax_collector", target_player="p3")))

    def test_double_down_needs_trade(self):
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("double_down")))
        validate(Submission("buy", "spices", 1, CardPlay("double_down")))

    def test_shield_takes_no_target(self):
        with pytest.raises(InvalidAction):
            validate(Submission(card=CardPlay("shield", "spices")))


# --------------------------------------------------------------------- prices

class TestPrices:
    def test_demand_delta(self):
        assert [demand_delta(CFG, n) for n in (0, 1, 2, 3, -3, 7, 20, -20)] == [0, 0, 1, 1, -1, 3, 4, -4]

    def test_swing_bound(self):
        assert move_price(CFG, 20, 100) == 28  # 40% of 20
        assert move_price(CFG, 20, -100) == 12
        assert move_price(CFG, 5, 100) == 8  # min swing 3

    def test_absolute_bounds(self):
        assert move_price(CFG, 3, -100) == 2  # floor
        assert move_price(CFG, 97, 100) == 99  # ceiling

    def test_prices_always_in_bounds_fuzz(self):
        import random
        rng = random.Random(0)
        for _ in range(2000):
            p0 = rng.randint(2, 99)
            p1 = move_price(CFG, p0, rng.randint(-200, 200))
            assert 2 <= p1 <= 99 and abs(p1 - p0) <= CFG.max_swing(p0)


# ----------------------------------------------------------------- resolution

class TestResolution:
    def test_all_hold_calm_nothing_changes(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(), "calm")
        assert out.prices == PRICES
        assert all(p.coins == 100 for p in out.players)

    def test_missing_submission_rejected(self):
        subs = holds()
        del subs["p4"]
        with pytest.raises(InvalidAction):
            resolve_round(CFG, 1, PRICES, players(), subs, "calm")

    def test_buy_and_sell_settle_at_quoted_price(self):
        ps = players(inventories=[{}, {"metals": 3}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("buy", "spices", 3), p2=Submission("sell", "metals", 2)), "calm")
        p = by_id(out)
        assert p["p1"].coins == 70 and p["p1"].inventory["spices"] == 3
        assert p["p2"].coins == 124 and p["p2"].inventory["metals"] == 1

    def test_demand_moves_price(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission("buy", "spices", 3), p2=Submission("buy", "spices", 2)), "calm")
        assert out.prices["spices"] == 12  # net +5 -> +2
        assert out.summary["movement"]["spices"]["net_units"] == 5

    def test_invalid_trade_at_resolution_fails_safely(self):
        # A buy that is no longer affordable is recorded as failed, never negative.
        ps = players(coins=(10, 100, 100, 100))
        out = resolve_round(CFG, 1, PRICES, ps, holds(p1=Submission("buy", "relics", 4)), "calm")
        assert by_id(out)["p1"].coins == 10
        t = summary_for(out, "p1")["trade"]
        assert t["executed"] is False and "Insufficient" in t["reason"]

    def test_boom_and_bust_events(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(), "crystals_boom")
        assert out.prices["crystals"] == 25
        out = resolve_round(CFG, 1, PRICES, players(), holds(), "relics_bust")
        assert out.prices["relics"] == 19

    def test_market_pump_and_crash(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(card=CardPlay("market_pump", "crystals")),
            p2=Submission(card=CardPlay("market_crash", "relics"))), "calm")
        assert out.prices["crystals"] == 25 and out.prices["relics"] == 19

    def test_pump_and_crash_cancel(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(card=CardPlay("market_pump", "energy")),
            p2=Submission(card=CardPlay("market_crash", "energy"))), "calm")
        assert out.prices["energy"] == 15

    def test_stacked_effects_are_bounded(self):
        out = resolve_round(CFG, 1, PRICES, players(), {
            f"p{i}": Submission("buy", "spices", 5, CardPlay("market_pump", "spices")) for i in range(1, 5)
        }, "spices_boom")
        assert out.prices["spices"] == 14  # 10 + 40% bound

    def test_double_down_gain(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission("buy", "crystals", 2, CardPlay("double_down"))), "crystals_boom")
        # buys 2 @20 = 40; price -> 26 (event +5, demand +1); bonus (26-20)*2 = 12
        assert out.prices["crystals"] == 26
        assert by_id(out)["p1"].coins == 100 - 40 + 12

    def test_double_down_sell_profits_from_drop(self):
        ps = players(inventories=[{"relics": 2}, {}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("sell", "relics", 2, CardPlay("double_down"))), "relics_bust")
        # sells 2 @25 = 50; price 25-6-1=18; bonus +14
        assert by_id(out)["p1"].coins == 100 + 50 + 14

    def test_double_down_loss_never_negative(self):
        ps = players(coins=(40, 100, 100, 100))
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("buy", "crystals", 2, CardPlay("double_down")),
            p2=Submission(card=CardPlay("market_crash", "crystals"))), "crystals_bust")
        assert by_id(out)["p1"].coins == 0
        assert summary_for(out, "p1")["effects"][0]["amount"] == 0

    def test_double_down_on_failed_trade_is_spent_without_effect(self):
        ps = players(coins=(5, 100, 100, 100))
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("buy", "relics", 1, CardPlay("double_down"))), "relics_boom")
        assert by_id(out)["p1"].coins == 5

    def test_tax_collector(self):
        out = resolve_round(CFG, 1, PRICES, players(coins=(100, 60, 100, 100)), holds(
            p1=Submission(card=CardPlay("tax_collector", target_player="p2"))), "calm")
        assert by_id(out)["p1"].coins == 109 and by_id(out)["p2"].coins == 51

    def test_tax_is_capped(self):
        out = resolve_round(CFG, 1, PRICES, players(coins=(0, 500, 100, 100)), holds(
            p1=Submission(card=CardPlay("tax_collector", target_player="p2"))), "calm")
        assert by_id(out)["p1"].coins == 15 and by_id(out)["p2"].coins == 485

    def test_tax_after_trades(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(card=CardPlay("tax_collector", target_player="p2")),
            p2=Submission("buy", "relics", 4)), "calm")
        # p2 spends 100 first, so there is nothing left to tax
        assert by_id(out)["p1"].coins == 100 and by_id(out)["p2"].coins == 0

    def test_double_tax_sequential_in_seat_order(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(card=CardPlay("tax_collector", target_player="p4")),
            p3=Submission(card=CardPlay("tax_collector", target_player="p4"))), "calm")
        p = by_id(out)
        assert p["p1"].coins == 115 and p["p3"].coins == 112 and p["p4"].coins == 73

    def test_shield_blocks_tax(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(card=CardPlay("tax_collector", target_player="p2")),
            p2=Submission(card=CardPlay("shield"))), "calm")
        assert by_id(out)["p1"].coins == 100 and by_id(out)["p2"].coins == 100

    def test_shield_refunds_crash_losses_only(self):
        ps = players(inventories=[{}, {"relics": 2}, {"relics": 2}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission(card=CardPlay("market_crash", "relics")),
            p2=Submission(card=CardPlay("shield"))), "relics_bust")
        # relics 25 -> 25 - 6 (event) - 6 (crash) = 13, bounded at -10 -> 15.
        # Drop 10, crash part 6: shielded p2 refunded 2 * 6 = 12; p3 gets nothing.
        assert out.prices["relics"] == 15
        assert by_id(out)["p2"].coins == 112 and by_id(out)["p3"].coins == 100

    def test_festival_bonus_only_for_executed_trades(self):
        ps = players(coins=(100, 100, 1, 100))
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("buy", "spices", 1), p3=Submission("buy", "relics", 1)), "festival")
        assert by_id(out)["p1"].coins == 94 and by_id(out)["p3"].coins == 1 and by_id(out)["p2"].coins == 100

    def test_customs_fee_and_shield(self):
        ps = players(coins=(100, 100, 2, 100),
                     inventories=[{"spices": 9}, {"spices": 9}, {"spices": 10}, {"spices": 6}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(p2=Submission(card=CardPlay("shield"))), "customs")
        p = by_id(out)
        assert p["p1"].coins == 97  # 3 over
        assert p["p2"].coins == 100  # shielded
        assert p["p3"].coins == 0  # capped at coins
        assert p["p4"].coins == 100  # at threshold

    def test_insider_tip_card_has_no_resolution_effect(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(), "calm")
        assert out.summary["players"][0]["card"] is None

    def test_conservation_without_bank_effects(self):
        # Pure trading + tax: coins + inventory value at P0 are conserved (calm, no demand change).
        ps = players(inventories=[{"metals": 1}, {}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("sell", "metals", 1),
            p2=Submission("buy", "energy", 1, None),
            p3=Submission(card=CardPlay("tax_collector", target_player="p4"))), "calm")
        before = sum(net_worth(p, PRICES) for p in ps)
        after = sum(net_worth(p, PRICES) for p in out.players)
        assert before == after

    def test_never_negative_fuzz(self):
        import random
        rng = random.Random(1)
        events = event_keys()
        for _ in range(500):
            ps = players(coins=tuple(rng.randint(0, 150) for _ in range(4)),
                         inventories=[{c: rng.randint(0, 2) for c in COMMODITIES} for _ in range(4)])
            prices = {c: rng.randint(2, 99) for c in COMMODITIES}
            subs = {}
            for p in ps:
                action = rng.choice(["buy", "sell", "hold"])
                card = rng.choice([None, CardPlay("shield"), CardPlay("double_down"),
                                   CardPlay("market_crash", rng.choice(COMMODITIES)),
                                   CardPlay("market_pump", rng.choice(COMMODITIES)),
                                   CardPlay("tax_collector", target_player=rng.choice(
                                       [q.player_id for q in ps if q is not p]))])
                subs[p.player_id] = (Submission(card=card if card and card.card_type != "double_down" else None)
                                     if action == "hold"
                                     else Submission(action, rng.choice(COMMODITIES), rng.randint(1, 5), card))
            out = resolve_round(CFG, 1, prices, ps, subs, rng.choice(events))
            for p in out.players:
                assert p.coins >= 0 and all(q >= 0 for q in p.inventory.values())
                assert sum(p.inventory.values()) <= max(CFG.inventory_limit, sum(
                    next(x for x in ps if x.player_id == p.player_id).inventory.values()))
            assert all(2 <= v <= 99 for v in out.prices.values())


# -------------------------------------------------------------------- scoring

class TestScoring:
    def test_net_worth(self):
        p = PlayerState("a", 1, "A", 10, {"relics": 2, "spices": 1})
        assert net_worth(p, PRICES) == 10 + 50 + 10

    def test_ranking_and_tiebreaks(self):
        ps = [
            PlayerState("a", 1, "A", 50, {"relics": 2}),  # 100
            PlayerState("b", 2, "B", 100, {}),             # 100, more coins -> ahead of a
            PlayerState("c", 3, "C", 120, {}),             # 120
            PlayerState("d", 4, "D", 100, {}),             # 100, ties b exactly
        ]
        ranks = {r["player_id"]: r["rank"] for r in final_ranking(ps, PRICES)}
        assert ranks == {"c": 1, "b": 2, "d": 2, "a": 4}

    def test_full_game_simulation(self):
        """Eight rounds of scripted play end with consistent final scores."""
        seed = 99
        events = schedule_events(seed, CFG)
        ps = players()
        prices = dict(PRICES)
        for r, event in enumerate(events, start=1):
            subs = {
                "p1": Submission("buy", "spices", 1) if ps[0].coins >= prices["spices"] else Submission.hold(),
                "p2": Submission("sell", "spices", 1) if ps[1].held("spices") else Submission("buy", "spices", 2),
                "p3": Submission.hold(),
                "p4": Submission.hold(is_auto=True),
            }
            out = resolve_round(CFG, r, prices, ps, subs, event)
            ps, prices = out.players, out.prices
        ranking = final_ranking(ps, prices)
        assert [r["rank"] for r in ranking][0] == 1
        assert {r["player_id"] for r in ranking} == {"p1", "p2", "p3", "p4"}
        assert next(r for r in ranking if r["player_id"] == "p3")["net_worth"] == 100
