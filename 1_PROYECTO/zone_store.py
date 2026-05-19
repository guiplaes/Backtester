"""Zone store — CRUD wrapper over brain_zone_state.json.

Substitueix brain_zones.json (que només era un snapshot regenerat cada 75s)
per un store amb cicle de vida: zones persistents, comptadors de tocs i rebuigs,
i marques d'invalidació/staleness. La strength només la modifica el Zone
Reviewer (LLM); el lifecycle (codi) només toca status/touches/rejections.

Schema per zona
───────────────
{
  "id": str                          # uuid4
  "price": float
  "type": "SUPPORT" | "RESISTANCE"
  "strength": "STRONG" | "MODERATE" | "WEAK"
  "bounce_direction": "BUY" | "SELL"
  "condition": str                   # justificació textual del Indicator/Reviewer
  "status": "ACTIVE" | "INVALIDATED" | "STALE"
  "source": "INDICATOR" | "REVIEWER_PROMOTED"
  "touches": int                     # incrementat per zone_lifecycle en cada toc
  "rejections": int                  # incrementat quan un toc va seguit de vela de rebuig
  "created_at": str (ISO-8601 UTC)
  "last_validated_at": str (ISO-8601 UTC)
  "invalidated_at": str | None
  "invalidated_reason": str | None
}

Envelope del fitxer
───────────────────
{
  "updated_at": str (ISO-8601 UTC)
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL"
  "context": str                     # narrativa breu del Indicator/Reviewer
  "zones": [ ...zone objects... ]
}
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

# Module-level lock shared across all callers in the same process. Any
# read→modify→write cycle on brain_zone_state.json MUST be wrapped with
# `with STATE_LOCK:` or use `with state_transaction(common_dir) as state:`
# to avoid silent loss of decisions when lifecycle + reviewer interleave.
STATE_LOCK = threading.RLock()

# ── Noms de fitxer (dins Common\Files del terminal MT5) ──
ZONE_STATE_FILE = "brain_zone_state.json"
LEGACY_ZONES_FILE = "brain_zones.json"
LEGACY_ARCHIVE_FILE = "brain_zones.legacy.json"

# ── Status d'una zona ──
ZONE_STATUS_ACTIVE = "ACTIVE"
ZONE_STATUS_INVALIDATED = "INVALIDATED"
ZONE_STATUS_STALE = "STALE"

# ── Strength d'una zona ──
ZONE_STRENGTH_STRONG = "STRONG"
ZONE_STRENGTH_MODERATE = "MODERATE"
ZONE_STRENGTH_WEAK = "WEAK"

ZONE_STRENGTH_ORDER = {
    ZONE_STRENGTH_WEAK: 1,
    ZONE_STRENGTH_MODERATE: 2,
    ZONE_STRENGTH_STRONG: 3,
}

# ── Type d'una zona ──
ZONE_TYPE_SUPPORT = "SUPPORT"
ZONE_TYPE_RESISTANCE = "RESISTANCE"

# ── Origen d'una zona ──
ZONE_SOURCE_INDICATOR = "INDICATOR"
ZONE_SOURCE_REVIEWER_PROMOTED = "REVIEWER_PROMOTED"

# ── Bias global del mapa ──
BIAS_BULLISH = "BULLISH"
BIAS_BEARISH = "BEARISH"
BIAS_NEUTRAL = "NEUTRAL"

# ── Accions del Reviewer ──
REVIEW_PROMOTE = "PROMOTE"
REVIEW_REJECT = "REJECT"
REVIEW_KEEP = "KEEP"
REVIEW_UPGRADE = "UPGRADE"
REVIEW_DOWNGRADE = "DOWNGRADE"
REVIEW_REMOVE = "REMOVE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_state() -> dict:
    return {
        "updated_at": _now_iso(),
        "bias": BIAS_NEUTRAL,
        "regime": None,
        "context": "",
        "notes": "",
        "coverage": {"close": False, "mid": False, "far": False},
        "zones": [],
    }


def _state_path(common_dir: str) -> str:
    return os.path.join(common_dir, ZONE_STATE_FILE)


def _legacy_path(common_dir: str) -> str:
    return os.path.join(common_dir, LEGACY_ZONES_FILE)


def _legacy_archive_path(common_dir: str) -> str:
    return os.path.join(common_dir, LEGACY_ARCHIVE_FILE)


def read_state(common_dir: str) -> dict:
    """Read the zone state envelope. Returns empty structure if file missing or corrupt."""
    path = _state_path(common_dir)
    with STATE_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "zones" not in data:
                return _empty_state()
            data.setdefault("bias", BIAS_NEUTRAL)
            data.setdefault("context", "")
            data.setdefault("updated_at", _now_iso())
            data.setdefault("regime", None)
            data.setdefault("notes", "")
            data.setdefault("coverage", {"close": False, "mid": False, "far": False})
            data.setdefault("coverage_gap", None)
            if not isinstance(data["zones"], list):
                data["zones"] = []
            return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return _empty_state()


def write_state(common_dir: str, state: dict) -> None:
    """Atomically write the zone state envelope. Writes .tmp then os.replace()."""
    state = dict(state)
    state["updated_at"] = _now_iso()
    path = _state_path(common_dir)
    tmp = path + ".tmp"
    with STATE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)


class state_transaction:
    """Context manager for read→modify→write cycles on the zone state.

    Holds STATE_LOCK for the entire cycle so no other thread can interleave a
    read-modify-write. Usage:
        with state_transaction(common_dir) as state:
            ... mutate state ...
            # auto-written on __exit__ if no exception
    If the body raises, the transaction aborts (no write).
    """

    def __init__(self, common_dir: str):
        self.common_dir = common_dir
        self.state: dict | None = None

    def __enter__(self) -> dict:
        STATE_LOCK.acquire()
        self.state = read_state(self.common_dir)
        return self.state

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and self.state is not None:
                write_state(self.common_dir, self.state)
        finally:
            STATE_LOCK.release()
        return False


def build_zone(
    price: float,
    ztype: str,
    strength: str,
    bounce_direction: str,
    condition: str = "",
    source: str = ZONE_SOURCE_INDICATOR,
    region: str | None = None,
    confidence_numeric: float | None = None,
    naked_poc_futures: bool = False,
    data_sources_count: int = 1,
    expected_bounce_usd: float | None = None,
) -> dict:
    """Construct a new zone dict with fresh id and timestamps.

    `region` is one of "close" | "mid" | "far" | None — from the Indicator's
    classification. Optional; older call sites omit it and it defaults to None.

    Camps nous (2026-05-03 dual-feed):
      `confidence_numeric`: float 0-1 amb senyals confluents (POC spot+futures
        coincidència, HVN match, naked POC futures, sweep history, etc.).
        El chart drawing pinta gradient de color en funció d'això.
      `naked_poc_futures`: bool — la zona coincideix amb un Naked POC GC1!
        (imant institucional fort). Marca visual diferenciada al chart.
      `data_sources_count`: 1 (només spot confirmat) o 2 (spot + futures
        ambdós alineats). Diferencia visualment zones només-spot quan CME
        està tancat.
    """
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "price": float(price),
        "type": ztype,
        "strength": strength,
        "bounce_direction": bounce_direction,
        "condition": condition,
        "status": ZONE_STATUS_ACTIVE,
        "source": source,
        "region": region,
        "touches": 0,
        "rejections": 0,
        "created_at": now,
        "last_validated_at": now,
        "invalidated_at": None,
        "invalidated_reason": None,
        # Dual-feed metadata (defaults segurs si LLM no els proporciona)
        "confidence_numeric": confidence_numeric,
        "naked_poc_futures": bool(naked_poc_futures),
        "data_sources_count": int(data_sources_count) if data_sources_count else 1,
        # 2026-05-05: distància al primer rebot natural (TP estructural)
        "expected_bounce_usd": float(expected_bounce_usd) if expected_bounce_usd else None,
    }


def find_zone(state: dict, zone_id: str) -> dict | None:
    """Return zone dict by id or None."""
    for z in state.get("zones", []):
        if z.get("id") == zone_id:
            return z
    return None


def active_zones(state: dict) -> list[dict]:
    return [z for z in state.get("zones", []) if z.get("status") == ZONE_STATUS_ACTIVE]


def mark_invalidated(zone: dict, reason: str) -> None:
    """In-place mark a zone invalidated with reason + timestamp."""
    zone["status"] = ZONE_STATUS_INVALIDATED
    zone["invalidated_at"] = _now_iso()
    zone["invalidated_reason"] = reason


def mark_stale(zone: dict) -> None:
    """In-place mark a zone stale (still alive, degraded)."""
    zone["status"] = ZONE_STATUS_STALE


def record_touch(zone: dict) -> None:
    """Increment touches + update last_validated_at. Reactivates from STALE."""
    zone["touches"] = int(zone.get("touches", 0)) + 1
    zone["last_validated_at"] = _now_iso()
    if zone.get("status") == ZONE_STATUS_STALE:
        zone["status"] = ZONE_STATUS_ACTIVE


def record_rejection(zone: dict) -> None:
    """Increment rejections (caller has already recorded the touch)."""
    zone["rejections"] = int(zone.get("rejections", 0)) + 1


def apply_reviewer_decisions(
    current_state: dict,
    proposed_zones: list[dict],
    decisions: list[dict],
    new_bias: str | None = None,
    new_context: str | None = None,
    new_regime: str | None = None,
    new_coverage: dict | None = None,
    new_notes: str | None = None,
    new_coverage_gap: str | None = None,
    has_trade_open: bool = False,
    new_working_range: dict | None = None,
    new_directional_commitment: dict | None = None,
    new_asymmetric_risk: dict | None = None,
) -> dict:
    """Merge a Reviewer response into the current state.

    `decisions` is the Reviewer's list of {zone_id, action, new_strength, reason}.
    - PROMOTE: take matching item from `proposed_zones` (matched by price tolerance 0.5)
      and insert with a fresh build_zone().
    - REJECT: drop that proposed zone (no-op since we don't auto-insert).
    - KEEP: leave as is.
    - UPGRADE / DOWNGRADE: change strength on existing zone.
    - REMOVE: drop from state.

    Returns the new state dict (does not write).
    """
    zones_by_id = {z["id"]: dict(z) for z in current_state.get("zones", [])}
    kept_ids: set[str] = set()

    for dec in decisions:
        action = dec.get("action")
        zid = dec.get("zone_id")

        if action == REVIEW_REMOVE and zid in zones_by_id:
            zones_by_id.pop(zid, None)
            continue

        if action in (REVIEW_UPGRADE, REVIEW_DOWNGRADE) and zid in zones_by_id:
            new_s = dec.get("new_strength")
            if new_s in ZONE_STRENGTH_ORDER:
                zones_by_id[zid]["strength"] = new_s
            kept_ids.add(zid)
            continue

        if action == REVIEW_KEEP and zid in zones_by_id:
            kept_ids.add(zid)
            continue

        if action == REVIEW_PROMOTE:
            match = _match_proposed(dec, proposed_zones)
            if match is not None:
                # Reviewer can override bounce direction and strength; proposal supplies the rest.
                bounce = dec.get("bounce") or match.get("bounce") or match.get("bounce_direction", "BUY")
                new_zone = build_zone(
                    price=float(match.get("price", 0)),
                    ztype=match.get("type", ZONE_TYPE_SUPPORT),
                    strength=dec.get("new_strength") or match.get("strength", ZONE_STRENGTH_MODERATE),
                    bounce_direction=bounce,
                    condition=match.get("condition", dec.get("reason", "")),
                    source=ZONE_SOURCE_REVIEWER_PROMOTED,
                    region=match.get("region"),
                    # Dual-feed metadata propagada del proposal LLM
                    confidence_numeric=match.get("confidence_numeric"),
                    naked_poc_futures=match.get("naked_poc_futures", False),
                    data_sources_count=match.get("data_sources_count", 1),
                    expected_bounce_usd=match.get("expected_bounce_usd"),
                )
                zones_by_id[new_zone["id"]] = new_zone
            continue

        # REJECT or unknown → no-op
        if action == REVIEW_REJECT:
            continue

    new_zones = list(zones_by_id.values())

    # 2026-05-07: Hard cap actualitzat per coincidir amb el prompt del REVIEWER
    # (10 sense trade, 12 amb trade). Abans estava a 4/6 — limitava artificialment
    # el mapa, deixant l'usuari amb 3-4 zones quan en demanava 10.
    max_zones = 12 if has_trade_open else 10
    if len(new_zones) > max_zones:
        strength_rank = {ZONE_STRENGTH_STRONG: 3, ZONE_STRENGTH_MODERATE: 2, ZONE_STRENGTH_WEAK: 1}
        new_zones.sort(
            key=lambda z: (
                strength_rank.get(z.get("strength", ""), 0),
                z.get("last_validated_at", ""),
            ),
            reverse=True,
        )
        new_zones = new_zones[:max_zones]

    new_state = {
        "updated_at": _now_iso(),
        "bias": new_bias if new_bias is not None else current_state.get("bias", BIAS_NEUTRAL),
        "regime": new_regime if new_regime is not None else current_state.get("regime"),
        "context": new_context if new_context is not None else current_state.get("context", ""),
        "notes": new_notes if new_notes is not None else current_state.get("notes", ""),
        "coverage": new_coverage if new_coverage is not None else current_state.get(
            "coverage", {"close": False, "mid": False, "far": False}
        ),
        "coverage_gap": new_coverage_gap if new_coverage_gap is not None else current_state.get("coverage_gap"),
        "zones": new_zones,
        "working_range": new_working_range if new_working_range is not None else current_state.get("working_range"),
        "directional_commitment": new_directional_commitment if new_directional_commitment is not None else current_state.get("directional_commitment"),
        # NEW 2026-05-04 v2: asymmetric_risk persisted from Indicator response
        "asymmetric_risk": new_asymmetric_risk if new_asymmetric_risk is not None else current_state.get("asymmetric_risk"),
    }
    return new_state


def _match_proposed(decision: dict, proposed: list[dict], tol: float = 1.0) -> dict | None:
    """Match a PROMOTE decision to a proposed zone.

    Priority (most reliable first):
      1. `proposed_index` (legacy — still honored if present)
      2. `proposed_price` + `proposed_type` (current format from Reviewer v2)
      3. `proposed_price` alone (fallback, filters by price proximity only)

    Type filtering avoids collisions like SUPPORT@4830.3 vs RESISTANCE@4830.0.
    """
    idx = decision.get("proposed_index")
    if isinstance(idx, int) and 0 <= idx < len(proposed):
        return proposed[idx]

    price = decision.get("proposed_price")
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None

    want_type = decision.get("proposed_type")

    # Pass 1: match by price + type (strict)
    if want_type:
        for z in proposed:
            zp = z.get("price")
            if zp is None:
                continue
            try:
                if abs(float(zp) - p) <= tol and z.get("type") == want_type:
                    return z
            except (TypeError, ValueError):
                continue

    # Pass 2: match by price only (legacy fallback)
    for z in proposed:
        zp = z.get("price")
        if zp is None:
            continue
        try:
            if abs(float(zp) - p) <= tol:
                return z
        except (TypeError, ValueError):
            continue
    return None


def archive_legacy_zones(common_dir: str) -> bool:
    """One-shot migration: if brain_zones.json exists and brain_zone_state.json
    does not, rename the legacy file to brain_zones.legacy.json.

    Returns True if archiving happened, False otherwise (already migrated, or
    nothing to migrate). Never raises; logs via caller.
    """
    legacy = _legacy_path(common_dir)
    archive = _legacy_archive_path(common_dir)
    state = _state_path(common_dir)

    if os.path.exists(state):
        return False
    if not os.path.exists(legacy):
        return False
    try:
        if os.path.exists(archive):
            # Already archived once; don't overwrite — legacy was already renamed.
            return False
        os.rename(legacy, archive)
        return True
    except OSError:
        return False


def legacy_compat_view(state: dict) -> dict:
    """Return state formatted like the old brain_zones.json payload, for
    consumers that still read `reversal_zones`/`bias`/`context`. Drops
    non-active zones from the list (legacy consumers didn't know about STALE/INVALIDATED).
    """
    legacy_zones = []
    for z in state.get("zones", []):
        if z.get("status") != ZONE_STATUS_ACTIVE:
            continue
        legacy_zones.append({
            "id": z.get("id"),
            "price": z.get("price"),
            "type": z.get("type"),
            "strength": z.get("strength"),
            "bounce_direction": z.get("bounce_direction"),
            "condition": z.get("condition", ""),
            # NEW: estructurats per chart drawing i EXECUTOR
            # confidence_numeric: float 0-1 amb senyals confluents (cap fall-back)
            # naked_poc_futures: bool — zona coincideix amb Naked POC GC1!
            # data_sources_count: 1 (només spot) o 2 (spot + futures confirmats)
            "confidence_numeric": z.get("confidence_numeric"),
            "naked_poc_futures": z.get("naked_poc_futures"),
            "data_sources_count": z.get("data_sources_count"),
            # 2026-05-05: distància al primer rebot natural (TP de l'EXECUTOR)
            "expected_bounce_usd": z.get("expected_bounce_usd"),
            # 2026-05-05: nombre de tests per al judici qualitatiu
            "touches": z.get("touches", 0),
            "rejections": z.get("rejections", 0),
        })
    return {
        "reversal_zones": legacy_zones,
        "bias": state.get("bias", BIAS_NEUTRAL),
        "context": state.get("context", ""),
        # New fields (2026-05-02): working_range and directional_commitment
        # are produced by the Indicator. Persisted at the top level of state
        # so legacy consumers can read them without breaking.
        "working_range": state.get("working_range"),
        "directional_commitment": state.get("directional_commitment"),
        # NEW 2026-05-04: regime exposed for chart state-table widget.
        "regime": state.get("regime"),
        # NEW 2026-05-04 v2: asymmetric_risk (bull_squeeze + bear_continuation)
        # produced by Indicator's qualitative reasoning. Used by Executor to
        # calibrate setup sizing and caution.
        "asymmetric_risk": state.get("asymmetric_risk"),
        "updated": state.get("updated_at", _now_iso()),
    }
