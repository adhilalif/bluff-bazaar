"""The Kerala display layer: complete, ambiguity-preserving, and purely cosmetic."""
import re
import xml.etree.ElementTree as ET

import pytest

from bluff import theme, ui
from bluff.config import CARD_TARGETS, CARD_TYPES, COMMODITIES, DEFAULT_STARTING_PRICES, GameConfig, event_keys
from bluff.engine import InvalidAction, PlayerState, Submission, validate_submission


class TestCoverage:
    def test_every_internal_id_has_display_strings(self):
        assert set(theme.COMMODITY_DISPLAY) == set(COMMODITIES) == set(theme.DISPLAY_ORDER)
        assert set(theme.CARD_DISPLAY) == set(CARD_TYPES) == set(CARD_TARGETS)
        for key in event_keys():
            d = theme.event_display(key)
            assert d["title"] and d["en"] and d["text"] and d["rumor"]

    def test_requested_names(self):
        names = {theme.commodity_name(c): theme.commodity_en(c) for c in COMMODITIES}
        assert names == {"Thenga": "Coconut", "Kurumulaku": "Black pepper", "Elakka": "Cardamom",
                         "Chakka": "Jackfruit", "Nendran": "Kerala banana"}
        cards = {t: theme.card_name(t) for t in CARD_TYPES}
        assert cards == {"market_pump": "Vila Keri!", "market_crash": "Vila Idinju!", "shield": "Raksha",
                         "insider_tip": "Oru Karyam Parayam…", "tax_collector": "Chungam",
                         "double_down": "Iratti Labham"}

    def test_requested_event_titles_are_all_used(self):
        titles = {theme.event_display(k)["title"] for k in event_keys()}
        assert {"Mazha Thudangi!", "Kurumulaku Season!", "Vila Poyi!", "Nalla Kachavadam!",
                "Onam Vishesham"} <= titles

    def test_event_mapping_matches_effects(self):
        assert theme.event_display("festival")["title"] == "Onam Vishesham"
        assert theme.event_display("calm")["title"] == "Mazha Thudangi!"
        assert theme.event_display("relics_boom")["title"] == "Kurumulaku Season!"  # relics = Kurumulaku
        for c in COMMODITIES:
            assert theme.event_display(f"{c}_bust")["title"] == "Vila Poyi!"
            assert theme.commodity_name(c) in theme.event_display(f"{c}_boom")["text"]

    def test_reactions(self):
        assert [theme.reaction(k) for k in ("profit", "caught", "risk", "winner")] == [
            "Entha oru labham!", "Kallan pidichu!", "Risk edukkua alle?", "Adipoli! Bazaarile Raja!"]
        assert "(" in theme.reaction("profit", english=True)


class TestNoInformationLeak:
    def test_rumor_identical_for_boom_and_bust(self):
        for c in COMMODITIES:
            assert theme.rumor_for(f"{c}_boom") == theme.rumor_for(f"{c}_bust")
            assert theme.rumor_subject(f"{c}_boom") == theme.rumor_subject(f"{c}_bust") == c

    def test_rumor_identical_for_global_events(self):
        assert len({theme.rumor_for(k) for k in ("calm", "festival", "customs")}) == 1
        assert {theme.rumor_subject(k) for k in ("calm", "festival", "customs")} == {None}

    def test_rumors_never_contain_titles(self):
        for key in event_keys():
            rumor = theme.rumor_for(key).lower()
            for other in event_keys():
                assert theme.event_display(other)["title"].lower().rstrip("!") not in rumor


class TestPurelyCosmetic:
    def test_ids_and_prices_unchanged(self):
        assert COMMODITIES == ("crystals", "spices", "energy", "metals", "relics")
        assert DEFAULT_STARTING_PRICES == {"crystals": 20, "spices": 10, "energy": 15, "metals": 12, "relics": 25}
        assert CARD_TYPES == ("market_pump", "market_crash", "shield", "insider_tip", "tax_collector", "double_down")

    def test_engine_messages_use_display_names_not_ids(self):
        p = PlayerState("p1", 1, "P1", 100, {"relics": 1})
        with pytest.raises(InvalidAction) as exc:
            validate_submission(GameConfig(), p, DEFAULT_STARTING_PRICES, Submission("sell", "relics", 2),
                                [], ["p2"])
        assert "Kurumulaku" in str(exc.value) and "relics" not in str(exc.value)


class TestIcons:
    @pytest.mark.parametrize("icon", ["thenga", "kurumulaku", "elakka", "chakka", "nendran", "uru"])
    def test_icons_are_small_valid_svg(self, icon):
        raw = (theme.ICON_DIR / f"{icon}.svg").read_bytes()
        assert len(raw) < 3000
        root = ET.fromstring(raw)
        assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 48 48"
        assert b"<script" not in raw and b"href" not in raw
        assert theme.icon_data_uri(icon).startswith("data:image/svg+xml;base64,")


class TestRenderedHtml:
    def test_market_tiles_translation_toggle(self):
        prices = dict(DEFAULT_STARTING_PRICES)
        with_en = ui.market_html(prices, None, {"spices": 2}, english=True)
        without = ui.market_html(prices, None, {}, english=False)
        assert "Thenga" in with_en and "Coconut" in with_en and "you ×2" in with_en
        assert "Thenga" in without and 'class="bb-en"' not in without
        assert 'alt="Thenga (Coconut)"' in without  # screen readers always get the English meaning
        names = re.findall(r'<div class="nm">([^<]+)</div>', without)
        assert names == ["Thenga", "Kurumulaku", "Elakka", "Chakka", "Nendran"]

    def test_player_names_are_escaped(self):
        players = [{"player_id": "a", "seat": 1, "name": "<img onerror=x>", "is_host": True, "is_ready": True,
                    "online": True, "is_me": True}]
        assert "<img onerror" not in ui.players_html(players, {}, lobby=True)
        assert "<img onerror" not in ui.lock_strip_html(players, [1], [1])
        res = [{"rank": 1, "name": "<b>x</b>", "net_worth": 1, "coins": 1, "inventory_value": 0, "player_id": "a"}]
        assert "<b>x</b>" not in ui.results_table_html(res) and "<b>x</b>" not in ui.podium_html(res)

    def test_results_table_has_mobile_labels(self):
        res = [{"rank": 1, "name": "A", "net_worth": 120, "coins": 100, "inventory_value": 20, "player_id": "a"}]
        html = ui.results_table_html(res)
        assert 'data-label="Net worth">120' in html and 'data-label="Trader">A' in html


def summary(rnd, rows):
    return {"round": rnd, "event": "calm", "players": rows}


def row(pid, nw, effects=(), auto=False):
    return {"player_id": pid, "net_worth": nw, "effects": list(effects),
            "trade": {"action": "hold", "auto": auto}}


class TestRoundFeedback:
    def test_delta_from_starting_coins_then_previous_round(self):
        s1 = summary(1, [row("me", 104)])
        s2 = summary(2, [row("me", 117)])
        assert ui.my_round_result([s1], "me", 100)["delta"] == 4
        r = ui.my_round_result([s1, s2], "me", 100)
        assert (r["before"], r["after"], r["delta"], r["round"]) == (104, 117, 13, 2)
        assert ui.my_round_result([], "me", 100) is None

    def test_reactions_at_most_one_and_only_when_notable(self):
        assert ui.round_reaction(ui.my_round_result([summary(1, [row("me", 105)])], "me", 100)) is None
        assert ui.round_reaction(ui.my_round_result([summary(1, [row("me", 110)])], "me", 100)) == "profit"
        blocked = {"kind": "tax_paid", "amount": 0, "note": "blocked by Shield"}
        # Catching a thief wins over profit; only one reaction is ever returned.
        assert ui.round_reaction(ui.my_round_result([summary(1, [row("me", 130, [blocked])])], "me", 100)) == "caught"
        taxed = {"kind": "tax_paid", "amount": -15}
        assert ui.round_reaction(ui.my_round_result([summary(1, [row("me", 85, [taxed])])], "me", 100)) is None

    def test_timeout_flag(self):
        assert ui.my_round_result([summary(1, [row("me", 100, auto=True)])], "me", 100)["auto"] is True
