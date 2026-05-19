"""Scout — lightweight trade recommendation when IDLE.

Heuristic-only (no LLM) so it's cheap and fast. Scans the current zone map,
market context, and bars to answer: "if I had to pick a direction right now,
which would it be?" Returns a short recommendation string.

Signals scored (each +/- points toward BUY or SELL):
  · Nearest STRONG/MODERATE zone above vs below (closer = stronger vote for that side)
  · DXY trend H1 (UP → bearish gold = vote SELL; DOWN → BUY)
  · RSI M5 extreme (oversold > 70 → vote BUY; overbought < 30 → vote SELL)
  · Last BOS M15 direction (bullish → BUY; bearish → SELL) with decay by age
  · Intraday bias from Indicator (if available in zone state)

Returns dict with direction, confidence, top reasons.
"""
from __future__ import annotations
from typing import Optional


def _nearest_zone(zones: list[dict], price: float, side: str) -> Optional[dict]:
    """side='above' returns nearest resistance; 'below' nearest support.
    Only considers STRONG/MODERATE (WEAK are map references, not setups).
    """
    strong_mod = [z for z in zones if z.get('strength', '').upper() in ('STRONG', 'MODERATE')]
    if not strong_mod:
        return None
    if side == 'above':
        candidates = [z for z in strong_mod if float(z.get('price', 0)) > price]
        if not candidates:
            return None
        return min(candidates, key=lambda z: float(z.get('price', 0)) - price)
    else:
        candidates = [z for z in strong_mod if float(z.get('price', 0)) < price]
        if not candidates:
            return None
        return min(candidates, key=lambda z: price - float(z.get('price', 0)))


def recommend(price: float, bars_m5: list[dict], zones: list[dict],
              market_state: dict | None = None, indicator_bias: str = 'NEUTRAL') -> dict:
    """Return {direction, confidence, reasons[]}. confidence is 0-100 heuristic."""
    score_buy = 0
    score_sell = 0
    reasons_buy = []
    reasons_sell = []

    # 1. Nearest zones
    z_above = _nearest_zone(zones, price, 'above')
    z_below = _nearest_zone(zones, price, 'below')
    if z_above:
        # Resistance close → SELL from here has setup (price hits resistance, reverses)
        dist = float(z_above.get('price', 0)) - price
        strength_pts = 3 if z_above.get('strength', '').upper() == 'STRONG' else 2
        # Closer zone = more immediate setup
        proximity_pts = max(0, 3 - dist / 5)  # within 5$ = full, within 15$ = minimal
        pts = strength_pts + proximity_pts
        score_sell += pts
        reasons_sell.append(f"resist {z_above.get('price'):.1f} ({z_above.get('strength','?')[0]}) a {dist:.1f}$")
    if z_below:
        dist = price - float(z_below.get('price', 0))
        strength_pts = 3 if z_below.get('strength', '').upper() == 'STRONG' else 2
        proximity_pts = max(0, 3 - dist / 5)
        pts = strength_pts + proximity_pts
        score_buy += pts
        reasons_buy.append(f"support {z_below.get('price'):.1f} ({z_below.get('strength','?')[0]}) a {dist:.1f}$")

    # 2. DXY trend H1 (inverse correlation with gold)
    if market_state:
        dxy = (market_state.get('external') or {}).get('dxy') or {}
        trend = dxy.get('trend_h1')
        if trend == 'UP':
            score_sell += 2
            reasons_sell.append("DXY H1 UP")
        elif trend == 'DOWN':
            score_buy += 2
            reasons_buy.append("DXY H1 DOWN")

        # 10Y yield (strong up = bearish gold)
        yld = market_state.get('yield_10y') or {}
        if yld.get('trend_m15') == 'UP':
            score_sell += 1
            reasons_sell.append("10Y UP")
        elif yld.get('trend_m15') == 'DOWN':
            score_buy += 1
            reasons_buy.append("10Y DOWN")

        # 3. Session quality
        session = (market_state.get('session') or {}).get('name')
        if session in ('DEAD', 'ASIA'):
            # Discount scores — low conviction environment
            score_buy = int(score_buy * 0.7)
            score_sell = int(score_sell * 0.7)

    # 4. RSI extreme (M5, last 14)
    if len(bars_m5) >= 15:
        closes = [b['close'] for b in bars_m5]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(0, d))
            losses.append(max(0, -d))
        avg_g = sum(gains[-14:]) / 14
        avg_l = sum(losses[-14:]) / 14
        if avg_l == 0:
            rsi = 100
        else:
            rs = avg_g / avg_l
            rsi = 100 - 100 / (1 + rs)
        if rsi > 70:
            score_sell += 2
            reasons_sell.append(f"RSI {rsi:.0f} (sobrecomprat)")
        elif rsi < 30:
            score_buy += 2
            reasons_buy.append(f"RSI {rsi:.0f} (sobrevenut)")

    # 5. Last BOS (if available in market_state)
    if market_state:
        last_bos = ((market_state.get('structure') or {}).get('last_bos') or {})
        if last_bos:
            age = last_bos.get('age_bars', 999)
            btype = last_bos.get('type', '')
            # Recent BOS (< 10 bars) gets points that decay with age
            if age < 10:
                pts = 3 - (age / 5)
                if 'bull' in btype.lower():
                    score_buy += pts
                    reasons_buy.append(f"BOS bullish ({age} bars)")
                elif 'bear' in btype.lower():
                    score_sell += pts
                    reasons_sell.append(f"BOS bearish ({age} bars)")

    # 6. Indicator bias
    if indicator_bias == 'BULLISH':
        score_buy += 1
        reasons_buy.append("Indicator BULLISH")
    elif indicator_bias == 'BEARISH':
        score_sell += 1
        reasons_sell.append("Indicator BEARISH")

    # Final
    total = score_buy + score_sell
    if total == 0:
        return {'direction': None, 'confidence': 0, 'reasons': ['sense dades suficients']}

    if score_buy > score_sell:
        direction = 'BUY'
        confidence = int(score_buy / total * 100)
        reasons = reasons_buy[:3]
    elif score_sell > score_buy:
        direction = 'SELL'
        confidence = int(score_sell / total * 100)
        reasons = reasons_sell[:3]
    else:
        direction = None
        confidence = 50
        reasons = ['signals empat']

    return {'direction': direction, 'confidence': confidence,
            'reasons': reasons, 'score_buy': score_buy, 'score_sell': score_sell}


def format_recommendation(rec: dict) -> str:
    """Short one-liner for the status log."""
    d = rec.get('direction')
    if not d:
        return "cap direcció clara"
    c = rec.get('confidence', 0)
    reasons = ', '.join(rec.get('reasons', []))
    conf_label = '🟢' if c >= 65 else ('🟡' if c >= 50 else '🔴')
    return f"{conf_label} {d} ({c}%) — {reasons}"
