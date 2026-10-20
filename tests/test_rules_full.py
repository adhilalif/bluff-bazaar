"""Full Bazaar rules: cargo auction, perishables, monsoon season, secret orders, wealth breakdown."""
import random

import pytest

from bluff.config import COMMODITIES, DEFAULT_STARTING_PRICES, ORDERS, SEASON_KEYS, GameConfig, event_keys
from bluff.engine import (
    CLASSIC, CardPlay, InvalidAction, Lot, PlayerState, RoundRules, Submission, deal_orders, final_ranking,
    order_bonus, resolve_round, round_rules, schedule_lots, schedule_season, validate_submission,
)

CFG = GameConfig()
PRICES = dict(DEFAULT_STARTING_PRICES)  # crystals 20, spices 10, energy 15, metals 12, relics 25
LOT = Lot("crystals", 3, 1)


def players(coins=(100, 100, 100, 100), inventories=None):
    inventories = inventories or [{}, {}, {}, {}]
    return [PlayerState(f"p{i}", i, f"P{i}", coins[i - 1], dict(inventories[i - 1])) for i in range(1, 5)]


def holds(**overrides):
    subs = {f"p{i}": Submission.hold() for i in range(1, 5)}
    subs.update(overrides)
    return subs


def by_id(out):
    return {p.player_id: p for p in out.players}


def row(out, pid):
    return next(r for r in out.summary["players"] if r["player_id"] == pid)


def rules(**kw):
    base = dict(perishables=CFG.perishables, spoil_threshold=CFG.spoil_threshold)
    base.update(kw)
    return RoundRules(**base)


def validate(sub, player=None, r=None):
    validate_submission(CFG, player or players()[0], PRICES, sub, [], ["p2", "p3", "p4"], False, r or CLASSIC)


# ------------------------------------------------------------------ setup / modes

class TestModes:
    def test_classic_rooms_get_no_modifiers(self):
        classic = GameConfig(rules="classic")
        assert all(round_rules(classic, 7, r) == CLASSIC for r in range(1, 9))

    def test_full_rules_schedule(self):
        rr = [round_rules(CFG, 7, r) for r in range(1, 9)]
        assert all(r.lot is not None and r.spoil_threshold for r in rr)
        assert [r.season is not None for r in rr] == [False, False, False, True, True, True, False, False]
        assert len({r.season for r in rr if r.season}) == 1  # one effect for the whole season

    def test_schedules_deterministic(self):
        assert schedule_lots(5, CFG) == schedule_lots(5, CFG)
        assert all(CFG.lot_min_qty <= lot.quantity <= CFG.lot_max_qty for lot in schedule_lots(5, CFG))
        assert {schedule_season(s) for s in range(200)} == set(SEASON_KEYS)
        orders = deal_orders(3, ["a", "b", "c", "d"])
        assert orders == deal_orders(3, ["a", "b", "c", "d"]) and len(set(orders.values())) == 4
        assert set(orders.values()) <= set(ORDERS)

    def test_legacy_config_is_classic(self):
        d = GameConfig().to_dict()
        d.pop("rules")
        assert GameConfig.from_dict(d).rules == "classic"

    def test_bad_mode_rejected(self):
        with pytest.raises(ValueError):
            GameConfig(rules="chaos")


class TestClassicUnchanged:
    def test_classic_resolution_matches_default_for_random_games(self):
        """CLASSIC rules give the same outcome as the original call signature (no new behaviour)."""
        rng = random.Random(11)
        for _ in range(300):
            ps = players(coins=tuple(rng.randint(0, 150) for _ in range(4)),
                         inventories=[{c: rng.randint(0, 3) for c in COMMODITIES} for _ in range(4)])
            subs = {p.player_id: Submission(rng.choice(["buy", "sell"]), rng.choice(COMMODITIES), rng.randint(1, 5))
                    for p in ps}
            ev = rng.choice(event_keys())
            a = resolve_round(CFG, 1, PRICES, ps, subs, ev)
            b = resolve_round(CFG, 1, PRICES, ps, subs, ev, CLASSIC)
            assert a.prices == b.prices and a.players == b.players
            assert a.summary["auction"] is None
            assert not any(e["kind"] == "spoiled" for r in a.summary["players"] for e in r["effects"])


# ---------------------------------------------------------------- monsoon / trades

class TestSeasonValidation:
    def test_ship_delay_caps_quantity(self):
        r = rules(max_qty=3)
        validate(Submission("buy", "spices", 3), r=r)
        with pytest.raises(InvalidAction, match="between 1 and 3"):
            validate(Submission("buy", "spices", 4), r=r)

    def test_godown_lock_blocks_pepper_sales(self):
        p = PlayerState("p1", 1, "P1", 100, {"relics": 3})
        with pytest.raises(InvalidAction, match="Kurumulaku cannot be sold"):
            validate(Submission("sell", "relics", 1), p, rules(unsellable=frozenset({"relics"})))
        validate(Submission("buy", "relics", 1), p, rules(unsellable=frozenset({"relics"})))

    def test_ship_delay_rechecked_at_resolution(self):
        out = resolve_round(CFG, 4, PRICES, players(), holds(p1=Submission("buy", "spices", 5)), "calm",
                            rules(max_qty=3))
        assert row(out, "p1")["trade"]["executed"] is False


# ---------------------------------------------------------------- perishables

class TestSpoilage:
    def test_perishables_at_threshold_lose_one(self):
        ps = players(inventories=[{"energy": 3, "metals": 2}, {"metals": 5}, {"relics": 6}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(), "calm", rules())
        p = by_id(out)
        assert p["p1"].inventory["energy"] == 2 and p["p1"].inventory["metals"] == 2
        assert p["p2"].inventory["metals"] == 4
        assert p["p3"].inventory["relics"] == 6  # pepper keeps

    def test_spoilage_happens_after_buying(self):
        ps = players(inventories=[{"energy": 2}, {}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(p1=Submission("buy", "energy", 1)), "calm", rules())
        assert by_id(out)["p1"].inventory["energy"] == 2
        assert any(e["kind"] == "spoiled" for e in row(out, "p1")["effects"])

    def test_wet_godowns_lower_threshold(self):
        ps = players(inventories=[{"energy": 2}, {}, {}, {}])
        assert by_id(resolve_round(CFG, 1, PRICES, ps, holds(), "calm", rules()))["p1"].inventory["energy"] == 2
        wet = rules(spoil_threshold=2)
        assert by_id(resolve_round(CFG, 5, PRICES, ps, holds(), "calm", wet))["p1"].inventory["energy"] == 1


# ---------------------------------------------------------------- cargo auction

class TestAuction:
    def test_bid_validation(self):
        r = rules(lot=LOT)
        validate(Submission(bid=30), r=r)
        with pytest.raises(InvalidAction, match="no cargo"):
            validate(Submission(bid=5))
        with pytest.raises(InvalidAction, match="Bid too high"):
            validate(Submission("buy", "relics", 3, bid=30), r=r)  # 75 + 30 > 100
        with pytest.raises(InvalidAction):
            validate(Submission(bid=-1), r=r)
        with pytest.raises(InvalidAction, match="No room"):
            validate(Submission(bid=30), PlayerState("p1", 1, "P1", 100, {"spices": 8}), r)
        validate(Submission("sell", "spices", 1, bid=30), PlayerState("p1", 1, "P1", 100, {"spices": 8}), r)

    def test_reserve_price_is_half_the_lot_value(self):
        r = rules(lot=LOT)  # 3 Elakka at 20 = 60 -> reserve 30
        with pytest.raises(InvalidAction, match="Minimum bid for this cargo is 30"):
            validate(Submission(bid=29), r=r)
        validate(Submission(bid=30), r=r)

    def test_highest_bid_wins_and_pays(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(bid=30), p2=Submission(bid=45), p3=Submission(bid=10)), "calm", rules(lot=LOT))
        p = by_id(out)
        assert p["p2"].coins == 55 and p["p2"].inventory["crystals"] == 3
        assert p["p1"].coins == 100 and p["p3"].coins == 100
        a = out.summary["auction"]
        assert a["winner"] == "p2" and a["price"] == 45 and a["reserve"] == 30
        assert a["bids"] == {"p1": 30, "p2": 45, "p3": 10}  # below-reserve bids are shown but ignored

    def test_exact_tie_leaves_lot_unsold(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(
            p1=Submission(bid=40), p4=Submission(bid=40)), "calm", rules(lot=LOT))
        assert out.summary["auction"]["outcome"] == "tie" and out.summary["auction"]["winner"] is None
        assert all(p.coins == 100 for p in out.players)

    def test_cargo_does_not_move_demand(self):
        out = resolve_round(CFG, 1, PRICES, players(), holds(p1=Submission(bid=30)), "calm", rules(lot=LOT))
        assert out.summary["auction"]["winner"] == "p1" and out.prices["crystals"] == 20

    def test_bid_rechecked_after_trades(self):
        # p1 buys first (trades settle before the auction) and can no longer afford the bid.
        ps = players(coins=(50, 100, 100, 100))
        out = resolve_round(CFG, 1, PRICES, ps, holds(p1=Submission("buy", "relics", 2, bid=30)), "calm",
                            rules(lot=LOT))
        assert out.summary["auction"]["outcome"] == "no bids" and by_id(out)["p1"].coins == 0

    def test_won_cargo_is_fresh_this_round_and_can_spoil_next(self):
        lot_rules = rules(lot=Lot("energy", 3, 1))  # 3 x 15 = 45 -> reserve 23
        out = resolve_round(CFG, 1, PRICES, players(), holds(p1=Submission(bid=23)), "calm", lot_rules)
        assert by_id(out)["p1"].inventory["energy"] == 3  # fresh cargo doesn't spoil on arrival
        nxt = resolve_round(CFG, 2, out.prices, out.players, holds(), "calm", rules())
        assert by_id(nxt)["p1"].inventory["energy"] == 2

    def test_fresh_cargo_does_not_shield_old_stock(self):
        ps = players(inventories=[{"energy": 3}, {}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(p1=Submission(bid=23)), "calm", rules(lot=Lot("energy", 3, 1)))
        assert by_id(out)["p1"].inventory["energy"] == 5  # 3 old (one spoils) + 3 fresh


# ---------------------------------------------------------------- orders

class TestOrders:
    @pytest.mark.parametrize("key,inv,coins,lots,expected", [
        ("pepper_exporter", {"relics": 4}, 0, 0, True),
        ("pepper_exporter", {"relics": 3}, 0, 0, False),
        ("fruit_stall", {"energy": 2, "metals": 2}, 0, 0, True),
        ("full_stall", {c: 1 for c in COMMODITIES}, 0, 0, True),
        ("full_stall", {"spices": 9}, 0, 0, False),
        ("cash_chest", {}, 150, 0, True),
        ("cash_chest", {}, 149, 0, False),
        ("cash_chest", {}, 100, 0, False),  # starting coins alone never earn it
        ("harbour_baron", {}, 0, 2, True),
        ("harbour_baron", {}, 0, 1, False),
    ])
    def test_conditions(self, key, inv, coins, lots, expected):
        p = PlayerState("a", 1, "A", coins, inv)
        assert (order_bonus(key, p, lots) > 0) is expected
        assert order_bonus(None, p, lots) == 0

    def test_bonus_counts_in_final_ranking(self):
        ps = [PlayerState("a", 1, "A", 100, {}), PlayerState("b", 2, "B", 110, {}),
              PlayerState("c", 3, "C", 50, {}), PlayerState("d", 4, "D", 60, {})]
        ranking = final_ranking(ps, PRICES, {"a": 15})
        top = ranking[0]
        assert top["player_id"] == "a" and top["net_worth"] == 115 and top["order_bonus"] == 15
        assert next(r for r in ranking if r["player_id"] == "b")["order_bonus"] == 0


# ---------------------------------------------------------------- breakdown + fuzz

class TestBreakdown:
    def test_breakdown_reconciles(self):
        ps = players(inventories=[{"relics": 2}, {"energy": 3}, {}, {}])
        out = resolve_round(CFG, 1, PRICES, ps, holds(
            p1=Submission("buy", "relics", 2, CardPlay("double_down")),
            p2=Submission(bid=30), p3=Submission(card=CardPlay("tax_collector", target_player="p4"))),
            "relics_boom", rules(lot=LOT))
        for r in out.summary["players"]:
            b = r["breakdown"]
            assert b["end"] - b["start"] == b["price_moves"] + b["cards_events"] + b["cargo"] + b["spoiled"]
            assert b["end"] == r["net_worth"]
        b1 = row(out, "p1")["breakdown"]
        assert b1["trade_cash"] == -50 and b1["price_moves"] > 0 and b1["cards_events"] > 0
        assert row(out, "p2")["breakdown"]["spoiled"] < 0

    def test_full_rules_fuzz_never_negative_and_reconciles(self):
        rng = random.Random(4)
        for _ in range(800):
            ps = players(coins=tuple(rng.randint(0, 150) for _ in range(4)),
                         inventories=[{c: rng.randint(0, 2) for c in COMMODITIES} for _ in range(4)])
            prices = {c: rng.randint(2, 99) for c in COMMODITIES}
            r = rules(lot=Lot(rng.choice(COMMODITIES), rng.randint(2, 3), 1),
                      spoil_threshold=rng.choice([2, 3]),
                      max_qty=rng.choice([None, 3]), unsellable=rng.choice([frozenset(), frozenset({"relics"})]))
            subs = {}
            for p in ps:
                action = rng.choice(["buy", "sell", "hold"])
                card = rng.choice([None, CardPlay("shield"), CardPlay("market_crash", rng.choice(COMMODITIES)),
                                   CardPlay("tax_collector", target_player=rng.choice(
                                       [q.player_id for q in ps if q is not p]))])
                bid = rng.choice([0, 0, rng.randint(1, 80)])
                subs[p.player_id] = (Submission(card=card, bid=bid) if action == "hold" else
                                     Submission(action, rng.choice(COMMODITIES), rng.randint(1, 5), card, bid=bid))
            out = resolve_round(CFG, 1, prices, ps, subs, rng.choice(event_keys()), r)  # asserts reconcile
            for p in out.players:
                assert p.coins >= 0 and all(q >= 0 for q in p.inventory.values())
                assert p.units() <= max(CFG.inventory_limit,
                                        next(x for x in ps if x.player_id == p.player_id).units())
