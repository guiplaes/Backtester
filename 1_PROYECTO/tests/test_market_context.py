"""Tests for market_context.py — defensive, deterministic, no network."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import market_context as mc


def _make_bars(n=100, start_time=None, base_price=4000.0, pattern=None):
    """Synthetic M5 bars. pattern: list of close offsets applied cyclically."""
    start_time = start_time or int(
        datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc).timestamp()
    )
    pattern = pattern or [0.0]
    out = []
    p = base_price
    for i in range(n):
        offset = pattern[i % len(pattern)]
        p = p + offset
        out.append({
            "time": start_time + i * 300,
            "open": round(p - 0.5, 2),
            "high": round(p + 1.0, 2),
            "low": round(p - 1.0, 2),
            "close": round(p, 2),
            "volume": 1000,
        })
    return out


class TestTrendClassifier:
    def test_up_trend(self):
        closes = [100 + i * 0.5 for i in range(30)]
        assert mc._trend_from_closes(closes) == "UP"

    def test_down_trend(self):
        closes = [100 - i * 0.5 for i in range(30)]
        assert mc._trend_from_closes(closes) == "DOWN"

    def test_flat(self):
        closes = [100.0] * 30
        assert mc._trend_from_closes(closes) == "FLAT"

    def test_too_few_bars(self):
        assert mc._trend_from_closes([100, 101]) == "FLAT"


class TestDetectRecentBreak:
    def test_break_resistance(self):
        bars = _make_bars(50, base_price=100)
        # Force last close above previous high
        bars[-1]["close"] = 200.0
        assert mc._detect_recent_break(bars, lookback=20) == "resistance"

    def test_break_support(self):
        bars = _make_bars(50, base_price=100)
        bars[-1]["close"] = 1.0
        assert mc._detect_recent_break(bars, lookback=20) == "support"

    def test_no_break(self):
        bars = _make_bars(50, base_price=100)
        assert mc._detect_recent_break(bars, lookback=20) == "none"

    def test_too_few_bars(self):
        assert mc._detect_recent_break([{"close": 100}], lookback=20) == "none"


class TestAggregate:
    def test_m5_to_m15(self):
        bars = _make_bars(15, base_price=100)
        m15 = mc._aggregate(bars, 3)
        assert len(m15) == 5
        # First aggregated bar high = max of first 3 M5 highs
        assert m15[0]["high"] == max(b["high"] for b in bars[:3])
        assert m15[0]["open"] == bars[0]["open"]
        assert m15[0]["close"] == bars[2]["close"]


class TestComputeD1ContextFromM5:
    def test_returns_none_if_not_enough_bars(self):
        assert mc.compute_d1_context_from_m5([], 4000) is None
        assert mc.compute_d1_context_from_m5(_make_bars(20), 4000) is None

    def test_basic_extraction(self):
        # Generate 2 days of bars: 288 M5 bars = 24h × 12 per hour
        start_2_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        start_ts = int(start_2_days_ago.timestamp())
        bars = _make_bars(700, start_time=start_ts, base_price=4800,
                          pattern=[0.1, -0.05, 0.02])
        out = mc.compute_d1_context_from_m5(bars, current_price=4815.0)
        assert out is not None
        # Weekly open should be set (we have data spanning days)
        # Nearest round for 4815 = 4820
        assert out["nearest_round"] == 4820

    def test_nearest_round_rounds_correctly(self):
        bars = _make_bars(100, base_price=4816)
        out = mc.compute_d1_context_from_m5(bars, current_price=4816.0)
        # 4816 → 4820
        assert out["nearest_round"] == 4820


class TestSessionContext:
    def test_asia_session(self):
        dt = datetime(2026, 4, 14, 3, 0, tzinfo=timezone.utc)
        ctx = mc.compute_session_context(dt, [])
        assert ctx["name"] == "ASIA"
        assert ctx["minutes_since_open"] == 180  # 3h

    def test_london_session(self):
        dt = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
        ctx = mc.compute_session_context(dt, [])
        assert ctx["name"] == "LONDON"
        assert ctx["minutes_since_open"] == 150  # 2.5h

    def test_overlap_session(self):
        dt = datetime(2026, 4, 14, 14, 0, tzinfo=timezone.utc)
        ctx = mc.compute_session_context(dt, [])
        assert ctx["name"] == "OVERLAP"

    def test_ny_session(self):
        dt = datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc)
        ctx = mc.compute_session_context(dt, [])
        assert ctx["name"] == "NY"

    def test_dead_session(self):
        dt = datetime(2026, 4, 14, 22, 0, tzinfo=timezone.utc)
        ctx = mc.compute_session_context(dt, [])
        assert ctx["name"] == "DEAD"

    def test_vwap_computation(self):
        # 10 M5 bars at NY session start, all @ $4800 with volume 100
        start = datetime(2026, 4, 14, 16, 0, tzinfo=timezone.utc)
        bars = []
        for i in range(10):
            bars.append({
                "time": int(start.timestamp()) + i * 300,
                "high": 4801, "low": 4799, "close": 4800, "open": 4800,
                "volume": 100,
            })
        dt = datetime(2026, 4, 14, 16, 50, tzinfo=timezone.utc)  # 50 min in
        ctx = mc.compute_session_context(dt, bars)
        # Typical = (4801+4799+4800)/3 = 4800, VWAP = 4800
        assert ctx["vwap"] == 4800.0
        assert ctx["distance_from_vwap_usd"] == 0


class TestAtrPercentile:
    def test_none_when_no_bars(self):
        out = mc.compute_atr_percentile([], atr_current=2.0)
        assert out["percentile_20d"] is None

    def test_anomalous_high(self):
        # Generate flat bars → low ATR history, then current ATR is huge
        bars = _make_bars(200, base_price=4000, pattern=[0.01])
        out = mc.compute_atr_percentile(bars, atr_current=50.0)
        assert out["percentile_20d"] == 100.0
        assert out["is_anomalous"] is True

    def test_normal_range(self):
        bars = _make_bars(200, base_price=4000, pattern=[1.0, -1.0])
        # Current ATR somewhere mid-range
        out = mc.compute_atr_percentile(bars, atr_current=2.0)
        # Should not be anomalous
        assert 0 <= out["percentile_20d"] <= 100


class TestDetectLastBos:
    def test_bullish_bos(self):
        # Create a dip then rise above dip's left pivot-high
        bars = _make_bars(30, base_price=100, pattern=[0.0])
        # Craft: pivot high at index 10 (value 120), pivot low later, then close > 120
        for i, b in enumerate(bars):
            b["high"] = 110
            b["low"] = 105
            b["close"] = 108
            b["open"] = 108
        # Pivot high at i=10 with high=120
        for offset in range(-5, 6):
            bars[10 + offset]["high"] = 115 if offset != 0 else 120
        # Final bar breaks above 120
        bars[-1]["close"] = 125
        bos = mc.detect_last_bos(bars, pivot_window=5)
        assert bos is not None
        assert bos["type"] == "bullish"

    def test_none_when_no_break(self):
        bars = _make_bars(30, base_price=100, pattern=[0.0])
        for b in bars:
            b["high"] = 102; b["low"] = 98; b["close"] = 100; b["open"] = 100
        bos = mc.detect_last_bos(bars, pivot_window=5)
        assert bos is None

    def test_too_few_bars(self):
        assert mc.detect_last_bos(_make_bars(5)) is None


class TestFindLiquidityPools:
    def test_equal_highs_above(self):
        bars = _make_bars(50, base_price=4800)
        # Force a cluster of equal highs at 4810
        for idx in [10, 20, 30]:
            bars[idx]["high"] = 4810.2
        pools = mc.find_liquidity_pools(bars, current_price=4800.0,
                                         tolerance_usd=1.0, lookback=50)
        assert any(abs(p - 4810) < 2 for p in pools["pools_above"])

    def test_empty_when_no_bars(self):
        pools = mc.find_liquidity_pools([], current_price=4800)
        assert pools == {"pools_below": [], "pools_above": []}


class TestBuildMarketContext:
    def test_returns_all_sections(self):
        bars = _make_bars(200, base_price=4800, pattern=[0.3, -0.1])
        now = datetime(2026, 4, 14, 14, 30, tzinfo=timezone.utc)
        ctx = mc.build_market_context(
            bars, account={}, tv_helper=None, now_utc=now,
            for_executor=True, atr_m5=2.5,
        )
        assert "external" in ctx
        assert "htf" in ctx
        assert "market_state" in ctx
        # Without tv_helper, external feeds are None but structure valid
        assert ctx["external"]["dxy"] is None
        assert ctx["external"]["yield_10y"] is None
        assert ctx["market_state"]["session"]["name"] == "OVERLAP"
        assert ctx["market_state"]["liquidity"] is not None

    def test_indicator_version_no_liquidity(self):
        bars = _make_bars(200, base_price=4800)
        now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
        ctx = mc.build_market_context(
            bars, account={}, tv_helper=None, now_utc=now,
            for_executor=False, atr_m5=2.0,
        )
        # Indicator version does not include liquidity
        assert ctx["market_state"]["liquidity"] is None

    def test_tv_helper_failure_is_defensive(self):
        bars = _make_bars(200, base_price=4800)
        now = datetime(2026, 4, 14, 14, 0, tzinfo=timezone.utc)

        def _fail_tv(*args, **kwargs):
            raise Exception("CDP disconnected")

        # Reset the module-level DXY cache so the stub tv helper is actually invoked
        mc._DXY_CACHE["ts"] = 0.0
        mc._DXY_CACHE["data"] = None
        mc._YIELD_CACHE["ts"] = 0.0
        mc._YIELD_CACHE["data"] = None

        ctx = mc.build_market_context(
            bars, account={}, tv_helper=_fail_tv, now_utc=now,
            for_executor=True, atr_m5=2.0,
        )
        # Should NOT raise. DXY/yield become None but the rest works.
        assert ctx["external"]["dxy"] is None
        assert ctx["market_state"]["session"]["name"] == "OVERLAP"

    def test_dxy_fetch_success(self):
        bars = _make_bars(200, base_price=4800)
        now = datetime(2026, 4, 14, 14, 0, tzinfo=timezone.utc)

        # Reset the module-level DXY cache so this test's stub is actually invoked.
        # Other tests may have left stale None entries that would short-circuit us.
        mc._DXY_CACHE["ts"] = 0.0
        mc._DXY_CACHE["data"] = None
        mc._YIELD_CACHE["ts"] = 0.0
        mc._YIELD_CACHE["data"] = None

        # Mock tv helper returning reasonable DXY bars
        def _mock_tv(cmd, sym=None, count=None, timeout=None):
            if cmd == "ohlcv-sym" and "DXY" in str(sym):
                mock_bars = _make_bars(150, base_price=104, pattern=[0.01, -0.005])
                return {"success": True, "bars": mock_bars, "symbol": sym,
                        "restored_to": "XAUUSD"}
            if cmd == "ohlcv-sym" and "10Y" in str(sym):
                mock_bars = _make_bars(60, base_price=4.3, pattern=[0.001])
                return {"success": True, "bars": mock_bars, "symbol": sym}
            return None

        ctx = mc.build_market_context(
            bars, account={}, tv_helper=_mock_tv, now_utc=now,
            for_executor=True, atr_m5=2.0,
        )
        assert ctx["external"]["dxy"] is not None
        assert "price" in ctx["external"]["dxy"]
        assert ctx["external"]["dxy"]["trend_m5"] in ("UP", "DOWN", "FLAT")
        assert ctx["external"]["yield_10y"] is not None
