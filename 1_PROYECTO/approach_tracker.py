"""approach_tracker.py — autonomous approach phase tracking per zona.

Filosofia:
  - Per cada zona del zone_store (LLM-validated), tracks com s'apropa el preu
    en temps real i acumula volum institucional + signed delta durant
    l'aproximació.
  - Tres estats: IDLE (lluny) / APPROACH (dins approach_dist) / AT_ZONE
    (dins zone_tol). Transicions instantànies basades en preu live.
  - Delta i volum acumulats provenen de bars M1 GC1! tancades durant
    l'approach phase.
  - Exposa el state per zona a:
      · FastEngine (gate al staged setup fire si flux contrari fort)
      · LLM payloads (INDICATOR/EXECUTOR per raonar amb context)
      · Chart drawing (gradient color band per zona)

NO és una regla determinista — és un mòdul de mesura. La decisió "fire/skip"
final segueix sent del FastEngine + EXECUTOR amb judici qualitatiu.

Update cycle: cada 2s al FastEngine main loop.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

log = logging.getLogger("approach_tracker")


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


@dataclass
class ApproachState:
    """Estat per zona al tracker."""
    zone_id: str = ""
    zone_price: float = 0.0
    zone_type: str = "SUPPORT"          # SUPPORT o RESISTANCE
    state: str = "IDLE"                 # IDLE | APPROACH | AT_ZONE
    bars_acc: int = 0                   # M1 bars acumulats
    vol_acc: float = 0.0                # contractes acumulats (futures)
    delta_acc: float = 0.0               # signed delta acumulat
    approach_started_ts: float = 0.0    # quan va entrar a APPROACH
    atzone_started_ts: float = 0.0      # quan va entrar a AT_ZONE
    last_update_ts: float = 0.0
    last_seen_m1_ts: int = 0            # timestamp del darrer M1 bar processat (anti-doble-comptatge)

    def signal_strength(self, baseline_std_per_bar: float = 0.0) -> float:
        """Z-score normalitzat del delta acumulat vs baseline noise.

        Filosofia: el delta acumulat només és "anòmal" si supera el que la
        variació natural del moment del dia produeix per pur soroll.

        Càlcul:
          expected_noise_total = baseline_std_per_bar × sqrt(bars_acc)
          strength = delta_acc / expected_noise_total
          (capped a ±3.0 per sanity)

        Interpretació:
          0.0 = típic per a aquest número de bars
          1.0 = lleugerament anòmal
          2.0 = clarament anòmal (institucional)
          3.0+ = anomalia extrema (saturat)

        Si baseline insuficient, fallback a 0.0 (neutre).
        """
        if baseline_std_per_bar <= 0 or self.bars_acc <= 0:
            return 0.0
        import math
        expected_noise = baseline_std_per_bar * math.sqrt(self.bars_acc)
        if expected_noise <= 0:
            return 0.0
        strength = self.delta_acc / expected_noise
        return max(-3.0, min(3.0, strength))

    def to_dict(self) -> dict:
        """Per serialitzar al payload dels LLMs."""
        now = time.time()
        atzone_min = (now - self.atzone_started_ts) / 60 if self.atzone_started_ts else 0
        approach_min = (now - self.approach_started_ts) / 60 if self.approach_started_ts else 0
        return {
            "zone_price": round(self.zone_price, 2),
            "zone_type": self.zone_type,
            "state": self.state,
            "bars_acc": self.bars_acc,
            "vol_acc": round(self.vol_acc, 0),
            "delta_acc": round(self.delta_acc, 0),
            "approach_min": round(approach_min, 1),
            "atzone_min": round(atzone_min, 1),
        }


def _bar_signed_delta(bar: dict) -> float:
    """Estima delta d'una barra OHLCV: volum signat per direcció + força del cos.

    delta = volume × (close - open) / max(high - low, 0.01)

    Resultat: float; positiu = compradors agressius dominen; negatiu = venedors.
    En futures GC1! això és proxy directa de buy-vs-sell aggressor.
    """
    h = _safe_float(bar.get("high"))
    l = _safe_float(bar.get("low"))
    c = _safe_float(bar.get("close"))
    o = _safe_float(bar.get("open"))
    v = _safe_float(bar.get("volume"))
    rng = h - l
    if rng > 0 and v > 0:
        return v * (c - o) / rng
    return 0.0


class ApproachTracker:
    """Singleton stateful per a totes les zones actives.

    Ús:
        tracker = ApproachTracker(config)
        # cada cycle del FastEngine
        tracker.update(zones, price_now, gc_m1_bars)
        # consultes
        state = tracker.get_state(zone_id)
        all_states = tracker.get_all_states()

    Normalització Z-score:
        El tracker manté un buffer rolling dels últims N bars M1 deltas
        i recompute la stddev periòdicament. signal_strength es retorna
        com a múltiple de stddev (z-score), no com a fracció d'absolut.
        Auto-adaptatiu a régim del mercat: Asia tranquil → std petita,
        senyal lleuger compta. NY actiu → std gran, només bursts veritables.
    """

    def __init__(self, config: dict):
        """
        config: dict amb claus 'approach_dist', 'zone_tol',
                'baseline_window_bars' (default 60),
                'gate_strength_threshold' (z-score que activa block, default 1.5),
                'enabled'.
        """
        self.config = config or {}
        self.state_by_zone_id: dict[str, ApproachState] = {}
        # Rolling baseline buffer: (timestamp, delta_value) per cada M1 bar processat
        self._baseline_buffer: list[tuple[int, float]] = []
        self._baseline_std_cached: float = 0.0
        self._baseline_std_recompute_at: float = 0.0

    @property
    def approach_dist(self) -> float:
        return float(self.config.get("approach_dist", 5.0))

    @property
    def zone_tol(self) -> float:
        return float(self.config.get("zone_tol", 1.5))

    @property
    def baseline_window_bars(self) -> int:
        """Quants bars M1 mantenim al rolling buffer per calcular std."""
        return int(self.config.get("baseline_window_bars", 60))

    @property
    def gate_strength_threshold(self) -> float:
        """Z-score (sigmas) a partir del qual el gate bloqueja."""
        return float(self.config.get("gate_strength_threshold", 1.5))

    @property
    def min_bars_for_gate(self) -> int:
        return int(self.config.get("min_bars_for_gate", 3))

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    # ───────────── Compatibilitat retrocompatible ─────────────
    # Els consumidors antics (chart drawing, prompts) demanaven
    # delta_threshold_futures per dibuixar/colorar. Ara la noció ha
    # canviat: la "saturació" es defineix com strength = ±3.0 (z-score).
    # Mantenim el property per compatibilitat però retorna un valor
    # nominal que es interpreta com "1 sigma del baseline actual".
    @property
    def delta_threshold_futures(self) -> float:
        # Per a chart drawing: 1.0 sigma equival al strength 1.0.
        # Així el gradient es manté coherent (color saturat a strength=1.0).
        return self._baseline_std_cached if self._baseline_std_cached > 0 else 1.0

    @property
    def delta_threshold_spot(self) -> float:
        return self.delta_threshold_futures

    @property
    def baseline_std_per_bar(self) -> float:
        """std rolling del delta per barra. Recomputat lazy."""
        return self._baseline_std_cached

    def update(self, zones: list, price_now: float, gc_m1_bars: list, spot_m5_bars: list = None) -> None:
        """Crida cada cycle del FastEngine.

        Args:
            zones: llista de zones actives (dicts amb id, price, type)
            price_now: preu actual (spot)
            gc_m1_bars: bars M1 GC1! recents (per acumular delta institucional)
            spot_m5_bars: opcional, fallback per spot tick volume si gc absent
        """
        if not self.enabled:
            return
        if not zones:
            # Cap zona — purgem tot
            self.state_by_zone_id.clear()
            return

        now = time.time()

        # 1) Garbage collect: elimina zones que ja no existeixen
        active_ids = {z.get("id") for z in zones if z.get("id")}
        for zid in list(self.state_by_zone_id.keys()):
            if zid not in active_ids:
                del self.state_by_zone_id[zid]

        # 2) Determina la barra M1 més nova que NO hem processat encara
        # i computa el seu delta
        latest_m1_delta = None
        latest_m1_vol = 0.0
        latest_m1_ts = 0
        if gc_m1_bars and len(gc_m1_bars) >= 2:
            # La últimissima barra del cache pot ser la "viva" (no tancada).
            # Agafem la prèvia que SÍ està tancada.
            closed_bar = gc_m1_bars[-2]
            ts = int(closed_bar.get("time", 0))
            if ts > 0:
                latest_m1_ts = ts
                latest_m1_delta = _bar_signed_delta(closed_bar)
                latest_m1_vol = _safe_float(closed_bar.get("volume"))
                # Afegeix al rolling baseline (només si no és duplicat)
                if not self._baseline_buffer or self._baseline_buffer[-1][0] != ts:
                    self._baseline_buffer.append((ts, latest_m1_delta))
                    # Trim al window
                    max_size = self.baseline_window_bars
                    if len(self._baseline_buffer) > max_size:
                        self._baseline_buffer = self._baseline_buffer[-max_size:]
                    # Mark per recompute:
                    #  - sempre si encara no tenim cache calibrat (std==0)
                    #  - cada 30s un cop calibrat (per absorbir canvi de regim)
                    needs_recompute = (
                        self._baseline_std_cached <= 0
                        or now > self._baseline_std_recompute_at
                    )
                    if needs_recompute:
                        self._recompute_baseline_std()
                        self._baseline_std_recompute_at = now + 30

        # 3) Update state per cada zona
        for z in zones:
            zid = z.get("id")
            if not zid:
                continue
            zp = _safe_float(z.get("price"))
            ztype = z.get("type", "SUPPORT")
            if zp <= 0 or price_now <= 0:
                continue

            dist = abs(price_now - zp)
            st = self.state_by_zone_id.get(zid)
            if st is None:
                st = ApproachState(zone_id=zid, zone_price=zp, zone_type=ztype)
                self.state_by_zone_id[zid] = st
            st.zone_price = zp
            st.zone_type = ztype
            st.last_update_ts = now

            if dist <= self.zone_tol:
                # AT_ZONE
                if st.state != "AT_ZONE":
                    st.state = "AT_ZONE"
                    st.atzone_started_ts = now
                    if st.approach_started_ts == 0:
                        st.approach_started_ts = now
                # Acumulem si tenim un M1 nou no processat
                if latest_m1_delta is not None and latest_m1_ts > st.last_seen_m1_ts:
                    st.delta_acc += latest_m1_delta
                    st.vol_acc += latest_m1_vol
                    st.bars_acc += 1
                    st.last_seen_m1_ts = latest_m1_ts
            elif dist <= self.approach_dist:
                # APPROACH
                if st.state == "IDLE":
                    # Comencem nova approach phase
                    st.state = "APPROACH"
                    st.approach_started_ts = now
                    st.atzone_started_ts = 0
                    st.bars_acc = 0
                    st.vol_acc = 0.0
                    st.delta_acc = 0.0
                    st.last_seen_m1_ts = 0
                elif st.state == "AT_ZONE":
                    # Va sortir de zone_tol però encara dins approach
                    st.state = "APPROACH"
                    # No resetegem acumulats — el preu pot tornar
                # Acumulem M1 nou
                if latest_m1_delta is not None and latest_m1_ts > st.last_seen_m1_ts:
                    st.delta_acc += latest_m1_delta
                    st.vol_acc += latest_m1_vol
                    st.bars_acc += 1
                    st.last_seen_m1_ts = latest_m1_ts
            else:
                # Lluny — reset si no estava ja IDLE
                if st.state != "IDLE":
                    st.state = "IDLE"
                    st.bars_acc = 0
                    st.vol_acc = 0.0
                    st.delta_acc = 0.0
                    st.approach_started_ts = 0
                    st.atzone_started_ts = 0
                    st.last_seen_m1_ts = 0

    def _recompute_baseline_std(self):
        """Recompute la stddev del rolling baseline.

        Filtra outliers (>3σ del propi cluster) per evitar contaminar
        la baseline amb burstos institucionals que precisament volem detectar.
        """
        if len(self._baseline_buffer) < 10:
            self._baseline_std_cached = 0.0
            return
        deltas = [d for _, d in self._baseline_buffer]
        # Primera passada: mean i std
        mean = sum(deltas) / len(deltas)
        var = sum((d - mean) ** 2 for d in deltas) / len(deltas)
        std1 = var ** 0.5
        if std1 <= 0:
            self._baseline_std_cached = 0.0
            return
        # Segona passada: filtra outliers >3σ
        clean = [d for d in deltas if abs(d - mean) <= 3 * std1]
        if len(clean) < 5:
            clean = deltas  # fallback
        mean2 = sum(clean) / len(clean)
        var2 = sum((d - mean2) ** 2 for d in clean) / len(clean)
        self._baseline_std_cached = var2 ** 0.5

    def get_state(self, zone_id: str) -> ApproachState | None:
        return self.state_by_zone_id.get(zone_id)

    def get_all_states(self) -> dict[str, ApproachState]:
        return dict(self.state_by_zone_id)

    def get_payload_dict(self) -> dict:
        """Versió per enviar als LLMs (només zones APPROACH/AT_ZONE)."""
        out = {}
        std = self.baseline_std_per_bar
        for zid, st in self.state_by_zone_id.items():
            if st.state in ("APPROACH", "AT_ZONE"):
                d = st.to_dict()
                d["signal_strength"] = round(st.signal_strength(std), 2)
                d["baseline_std_per_bar"] = round(std, 1)
                out[zid] = d
        return out

    def should_block_fire(self, zone_id: str, direction: str) -> tuple[bool, str]:
        """Consulta usada pel FastEngine just abans de disparar un staged_setup.

        Retorna (block, reason).

        Bloquegem només si:
          - state == AT_ZONE
          - signal_strength (z-score) clarament contrari a direction
          - bars_acc >= mínim per evitar fires per soroll
          - baseline calibrat (std > 0 amb prou història)

        Filosofia: gate suau. Default: NO bloquejar (passa).
        """
        if not self.enabled:
            return False, "tracker_disabled"
        if not bool(self.config.get("gate_at_fastengine", True)):
            return False, "gate_disabled"

        st = self.get_state(zone_id)
        if not st:
            return False, "no_state"
        if st.state != "AT_ZONE":
            return False, f"state_{st.state}"
        if st.bars_acc < self.min_bars_for_gate:
            return False, f"bars_acc_only_{st.bars_acc}"

        std = self.baseline_std_per_bar
        if std <= 0:
            return False, "baseline_not_ready"

        strength = st.signal_strength(std)
        gate_thr = self.gate_strength_threshold

        if direction == "BUY" and strength < -gate_thr:
            return True, f"flow_strongly_bearish (z={strength:+.2f}sd, delta_acc={st.delta_acc:.0f}, std={std:.1f})"
        if direction == "SELL" and strength > gate_thr:
            return True, f"flow_strongly_bullish (z={strength:+.2f}sd, delta_acc={st.delta_acc:.0f}, std={std:.1f})"
        return False, f"z={strength:+.2f}sd_ok"


# ───────────── self-test ─────────────
if __name__ == "__main__":
    import time
    cfg = {
        "enabled": True,
        "approach_dist": 5.0,
        "zone_tol": 1.5,
        "baseline_window_bars": 60,
        "gate_at_fastengine": True,
        "gate_strength_threshold": 1.5,  # z-score, no fracció absoluta
        "min_bars_for_gate": 3,
    }
    tracker = ApproachTracker(cfg)
    zones = [
        {"id": "z1", "price": 4607.0, "type": "SUPPORT"},
        {"id": "z2", "price": 4640.0, "type": "RESISTANCE"},
    ]

    def mkbar(ts, o, c, vol=200):
        # Bar amb body pos (close > open) si o<c, força ràpida
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        return {"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": vol}

    # 1. Preu lluny → IDLE
    tracker.update(zones, price_now=4620, gc_m1_bars=[], spot_m5_bars=[])
    assert tracker.get_state("z1").state == "IDLE", tracker.get_state("z1").state
    print("OK IDLE")

    # 2. Preu dins approach (4612 vs zone 4607, dist=5 OK)
    bars = [mkbar(1000 + i, 4615, 4615 + (1 if i % 2 == 0 else -1), vol=200) for i in range(5)]
    tracker.update(zones, price_now=4611, gc_m1_bars=bars, spot_m5_bars=[])
    s = tracker.get_state("z1")
    assert s.state == "APPROACH", s.state
    assert s.bars_acc == 1, f"bars_acc should be 1, got {s.bars_acc}"
    print(f"OK APPROACH state={s.state} bars={s.bars_acc} delta={s.delta_acc:.1f}")

    # 3. Preu dins zone_tol → AT_ZONE
    bars2 = bars + [mkbar(1006, 4607, 4608, vol=300)]  # bullish bar
    tracker.update(zones, price_now=4607.5, gc_m1_bars=bars2, spot_m5_bars=[])
    s = tracker.get_state("z1")
    assert s.state == "AT_ZONE", s.state
    print(f"OK AT_ZONE state={s.state} bars={s.bars_acc} delta={s.delta_acc:.1f}")

    # 4. Test gate
    block, reason = tracker.should_block_fire("z1", "BUY")
    print(f"Gate BUY: block={block} reason={reason}")
    block, reason = tracker.should_block_fire("z1", "SELL")
    print(f"Gate SELL: block={block} reason={reason}")

    # 5. Preu lluny altra vegada → reset
    tracker.update(zones, price_now=4625, gc_m1_bars=[], spot_m5_bars=[])
    s = tracker.get_state("z1")
    assert s.state == "IDLE", s.state
    assert s.bars_acc == 0, f"reset failed: bars_acc={s.bars_acc}"
    print(f"OK reset state={s.state} bars={s.bars_acc}")

    # 6. Garbage collect
    tracker.update([{"id": "z2", "price": 4640.0, "type": "RESISTANCE"}], price_now=4625, gc_m1_bars=[], spot_m5_bars=[])
    assert tracker.get_state("z1") is None, "z1 should be purged"
    assert tracker.get_state("z2") is not None
    print("OK garbage collect")

    print("\n=== ALL TESTS PASSED ===")
