"""Tests for risk.py — safety net projection + next adverse zone finder."""
from risk import project_safety_net_price, build_dd_projection, find_next_adverse_zone


# XAUUSD: 1 lot = 100 oz → $1 adverse × 1 lot = $100 loss
CVU = 100.0


class TestProjectSafetyNetPrice:
    def test_buy_basic(self):
        # balance=100k, dd_limit=3500, dd_used=500 → remaining=3000
        # total_lots after = 0 + 0.06 = 0.06; adverse = 3000 / (0.06 * 100) = $500
        # BUY → price - 500
        sn = project_safety_net_price(
            current_price=4000.0,
            direction="BUY",
            current_total_lots=0.0,
            new_lot=0.06,
            dd_usd_used=500.0,
            dd_limit_usd=3500.0,
            contract_value_per_usd=CVU,
        )
        assert sn is not None
        assert abs(sn - 3500.0) < 0.01

    def test_sell_basic(self):
        sn = project_safety_net_price(
            current_price=4000.0,
            direction="SELL",
            current_total_lots=0.0,
            new_lot=0.06,
            dd_usd_used=500.0,
            dd_limit_usd=3500.0,
            contract_value_per_usd=CVU,
        )
        assert sn is not None
        assert abs(sn - 4500.0) < 0.01

    def test_existing_lots_tighten_safety_net(self):
        # Already holding 0.3 lots → adding 0.06 → 0.36 → adverse = 3000 / 36 = $83.3
        sn = project_safety_net_price(
            current_price=4000.0,
            direction="BUY",
            current_total_lots=0.3,
            new_lot=0.06,
            dd_usd_used=500.0,
            dd_limit_usd=3500.0,
            contract_value_per_usd=CVU,
        )
        assert sn is not None
        assert abs(sn - (4000.0 - 3000.0 / 36.0)) < 0.01

    def test_zero_lots_returns_none(self):
        sn = project_safety_net_price(
            current_price=4000.0,
            direction="BUY",
            current_total_lots=0.0,
            new_lot=0.0,
            dd_usd_used=500.0,
            dd_limit_usd=3500.0,
        )
        assert sn is None

    def test_past_dd_limit_returns_current_price(self):
        sn = project_safety_net_price(
            current_price=4000.0,
            direction="BUY",
            current_total_lots=0.1,
            new_lot=0.06,
            dd_usd_used=3600.0,  # already past
            dd_limit_usd=3500.0,
        )
        assert sn == 4000.0

    def test_unknown_direction(self):
        assert project_safety_net_price(
            current_price=4000.0, direction="WHATEVER",
            current_total_lots=0.1, new_lot=0.06,
            dd_usd_used=500.0, dd_limit_usd=3500.0,
        ) is None


class TestBuildDdProjection:
    def test_five_entries(self):
        arr = build_dd_projection(
            current_price=4000.0, direction="BUY",
            current_total_lots=0.0, base_lot=0.03, max_multiplier=5,
            dd_usd_used=0.0, dd_limit_usd=3500.0, contract_value_per_usd=CVU,
        )
        assert len(arr) == 5
        assert [e["multiplier"] for e in arr] == [1, 2, 3, 4, 5]
        assert [e["new_lot"] for e in arr] == [0.03, 0.06, 0.09, 0.12, 0.15]

    def test_higher_mult_gives_closer_safety_net(self):
        # For BUY, higher lot → less adverse distance → safety_net closer to current price
        arr = build_dd_projection(
            current_price=4000.0, direction="BUY",
            current_total_lots=0.0, base_lot=0.03, max_multiplier=5,
            dd_usd_used=0.0, dd_limit_usd=3500.0, contract_value_per_usd=CVU,
        )
        prices = [e["safety_net_price"] for e in arr]
        # Higher multiplier → safety net price is higher (less room below current)
        for i in range(len(prices) - 1):
            assert prices[i] < prices[i + 1]


class TestFindNextAdverseZone:
    def test_buy_finds_nearest_support_below(self):
        zones = [
            {"price": 3950.0, "type": "SUPPORT", "strength": "STRONG"},
            {"price": 3900.0, "type": "SUPPORT", "strength": "MODERATE"},
            {"price": 4050.0, "type": "RESISTANCE", "strength": "STRONG"},
        ]
        z = find_next_adverse_zone(current_price=4000.0, direction="BUY", zones=zones)
        assert z is not None
        assert z["price"] == 3950.0
        assert z["distance_usd"] == 50.0

    def test_sell_finds_nearest_resistance_above(self):
        zones = [
            {"price": 3950.0, "type": "SUPPORT", "strength": "STRONG"},
            {"price": 4050.0, "type": "RESISTANCE", "strength": "STRONG"},
            {"price": 4100.0, "type": "RESISTANCE", "strength": "MODERATE"},
        ]
        z = find_next_adverse_zone(current_price=4000.0, direction="SELL", zones=zones)
        assert z is not None
        assert z["price"] == 4050.0

    def test_no_adverse_zone(self):
        zones = [{"price": 4050.0, "type": "RESISTANCE", "strength": "STRONG"}]
        # BUY → looking for SUPPORT below, none exists
        assert find_next_adverse_zone(current_price=4000.0, direction="BUY", zones=zones) is None
