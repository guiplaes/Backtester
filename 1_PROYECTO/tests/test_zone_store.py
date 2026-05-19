"""Tests for zone_store — CRUD, build, reviewer merge, legacy archive."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from zone_store import (
    BIAS_BEARISH,
    BIAS_BULLISH,
    BIAS_NEUTRAL,
    LEGACY_ARCHIVE_FILE,
    LEGACY_ZONES_FILE,
    REVIEW_DOWNGRADE,
    REVIEW_KEEP,
    REVIEW_PROMOTE,
    REVIEW_REJECT,
    REVIEW_REMOVE,
    REVIEW_UPGRADE,
    ZONE_STATE_FILE,
    ZONE_STATUS_ACTIVE,
    ZONE_STATUS_INVALIDATED,
    ZONE_STATUS_STALE,
    ZONE_STRENGTH_MODERATE,
    ZONE_STRENGTH_STRONG,
    ZONE_STRENGTH_WEAK,
    ZONE_TYPE_RESISTANCE,
    ZONE_TYPE_SUPPORT,
    active_zones,
    apply_reviewer_decisions,
    archive_legacy_zones,
    build_zone,
    find_zone,
    legacy_compat_view,
    mark_invalidated,
    mark_stale,
    read_state,
    record_rejection,
    record_touch,
    write_state,
)


class TestReadWrite(unittest.TestCase):
    def test_read_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            state = read_state(d)
            self.assertEqual(state["zones"], [])
            self.assertEqual(state["bias"], BIAS_NEUTRAL)

    def test_read_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ZONE_STATE_FILE), "w") as f:
                f.write("{not: valid json")
            state = read_state(d)
            self.assertEqual(state["zones"], [])

    def test_write_is_atomic_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800.0, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY", "test")
            state = {"bias": BIAS_BULLISH, "context": "x", "zones": [z]}
            write_state(d, state)
            loaded = read_state(d)
            self.assertEqual(len(loaded["zones"]), 1)
            self.assertEqual(loaded["zones"][0]["price"], 4800.0)
            self.assertEqual(loaded["bias"], BIAS_BULLISH)
            self.assertFalse(os.path.exists(os.path.join(d, ZONE_STATE_FILE + ".tmp")))


class TestBuildZone(unittest.TestCase):
    def test_build_zone_has_required_fields(self):
        z = build_zone(4850.5, ZONE_TYPE_RESISTANCE, ZONE_STRENGTH_MODERATE, "SELL", "note")
        self.assertEqual(z["price"], 4850.5)
        self.assertEqual(z["type"], ZONE_TYPE_RESISTANCE)
        self.assertEqual(z["strength"], ZONE_STRENGTH_MODERATE)
        self.assertEqual(z["status"], ZONE_STATUS_ACTIVE)
        self.assertEqual(z["touches"], 0)
        self.assertEqual(z["rejections"], 0)
        self.assertIsNone(z["invalidated_at"])
        self.assertTrue(z["id"])
        self.assertTrue(z["created_at"])

    def test_build_zone_ids_are_unique(self):
        a = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        b = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        self.assertNotEqual(a["id"], b["id"])


class TestMutators(unittest.TestCase):
    def test_record_touch_increments_and_updates_timestamp(self):
        z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        ts0 = z["last_validated_at"]
        import time
        time.sleep(0.01)
        record_touch(z)
        self.assertEqual(z["touches"], 1)
        self.assertNotEqual(z["last_validated_at"], ts0)

    def test_record_touch_reactivates_stale(self):
        z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        mark_stale(z)
        self.assertEqual(z["status"], ZONE_STATUS_STALE)
        record_touch(z)
        self.assertEqual(z["status"], ZONE_STATUS_ACTIVE)

    def test_record_rejection_increments(self):
        z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        record_rejection(z)
        record_rejection(z)
        self.assertEqual(z["rejections"], 2)

    def test_mark_invalidated_sets_fields(self):
        z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        mark_invalidated(z, "clean_break_with_volume")
        self.assertEqual(z["status"], ZONE_STATUS_INVALIDATED)
        self.assertEqual(z["invalidated_reason"], "clean_break_with_volume")
        self.assertTrue(z["invalidated_at"])


class TestActiveZonesAndFind(unittest.TestCase):
    def test_active_zones_filters(self):
        a = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        b = build_zone(4900, ZONE_TYPE_RESISTANCE, ZONE_STRENGTH_STRONG, "SELL")
        mark_invalidated(b, "break")
        c = build_zone(4700, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_WEAK, "BUY")
        mark_stale(c)
        state = {"zones": [a, b, c]}
        self.assertEqual(len(active_zones(state)), 1)
        self.assertEqual(active_zones(state)[0]["id"], a["id"])

    def test_find_zone(self):
        a = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
        state = {"zones": [a]}
        self.assertEqual(find_zone(state, a["id"])["id"], a["id"])
        self.assertIsNone(find_zone(state, "nope"))


class TestReviewerMerge(unittest.TestCase):
    def _base_state(self):
        z_keep = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY", "keeper")
        z_up = build_zone(4780, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_WEAK, "BUY", "upgrademe")
        z_remove = build_zone(4900, ZONE_TYPE_RESISTANCE, ZONE_STRENGTH_WEAK, "SELL", "remove")
        return {
            "bias": BIAS_NEUTRAL,
            "context": "",
            "zones": [z_keep, z_up, z_remove],
        }, z_keep, z_up, z_remove

    def test_keep_preserves_counters(self):
        state, z_keep, _, _ = self._base_state()
        record_touch(z_keep)
        record_rejection(z_keep)
        decisions = [{"zone_id": z_keep["id"], "action": REVIEW_KEEP}]
        new_state = apply_reviewer_decisions(state, [], decisions)
        kept = next((z for z in new_state["zones"] if z["id"] == z_keep["id"]), None)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["touches"], 1)
        self.assertEqual(kept["rejections"], 1)

    def test_upgrade_changes_strength(self):
        state, _, z_up, _ = self._base_state()
        decisions = [{"zone_id": z_up["id"], "action": REVIEW_UPGRADE, "new_strength": ZONE_STRENGTH_MODERATE}]
        new_state = apply_reviewer_decisions(state, [], decisions)
        upped = next(z for z in new_state["zones"] if z["id"] == z_up["id"])
        self.assertEqual(upped["strength"], ZONE_STRENGTH_MODERATE)

    def test_downgrade_changes_strength(self):
        state, z_keep, _, _ = self._base_state()
        decisions = [{"zone_id": z_keep["id"], "action": REVIEW_DOWNGRADE, "new_strength": ZONE_STRENGTH_MODERATE}]
        new_state = apply_reviewer_decisions(state, [], decisions)
        down = next(z for z in new_state["zones"] if z["id"] == z_keep["id"])
        self.assertEqual(down["strength"], ZONE_STRENGTH_MODERATE)

    def test_remove_drops_zone(self):
        state, _, _, z_remove = self._base_state()
        decisions = [{"zone_id": z_remove["id"], "action": REVIEW_REMOVE}]
        new_state = apply_reviewer_decisions(state, [], decisions)
        self.assertFalse(any(z["id"] == z_remove["id"] for z in new_state["zones"]))

    def test_promote_inserts_new_zone_from_proposal(self):
        state, _, _, _ = self._base_state()
        proposed = [{"price": 4750.0, "type": ZONE_TYPE_SUPPORT, "strength": ZONE_STRENGTH_MODERATE, "bounce_direction": "BUY", "condition": "new"}]
        decisions = [{"zone_id": None, "action": REVIEW_PROMOTE, "proposed_index": 0, "new_strength": ZONE_STRENGTH_MODERATE, "reason": "decent"}]
        new_state = apply_reviewer_decisions(state, proposed, decisions)
        promoted = [z for z in new_state["zones"] if z["price"] == 4750.0]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["source"], "REVIEWER_PROMOTED")
        self.assertEqual(promoted[0]["strength"], ZONE_STRENGTH_MODERATE)

    def test_reject_is_noop(self):
        state, _, _, _ = self._base_state()
        proposed = [{"price": 4750.0, "type": ZONE_TYPE_SUPPORT, "strength": ZONE_STRENGTH_MODERATE, "bounce_direction": "BUY"}]
        decisions = [{"zone_id": None, "action": REVIEW_REJECT, "proposed_index": 0}]
        new_state = apply_reviewer_decisions(state, proposed, decisions)
        self.assertFalse(any(z["price"] == 4750.0 for z in new_state["zones"]))

    def test_promote_match_by_price(self):
        state, _, _, _ = self._base_state()
        proposed = [
            {"price": 4750.0, "type": ZONE_TYPE_SUPPORT, "strength": ZONE_STRENGTH_MODERATE, "bounce_direction": "BUY"},
            {"price": 4650.0, "type": ZONE_TYPE_SUPPORT, "strength": ZONE_STRENGTH_STRONG, "bounce_direction": "BUY"},
        ]
        decisions = [{"zone_id": None, "action": REVIEW_PROMOTE, "proposed_price": 4650.0}]
        new_state = apply_reviewer_decisions(state, proposed, decisions)
        self.assertTrue(any(z["price"] == 4650.0 for z in new_state["zones"]))

    def test_bias_and_context_updated(self):
        state, _, _, _ = self._base_state()
        new_state = apply_reviewer_decisions(state, [], [], new_bias=BIAS_BEARISH, new_context="ns")
        self.assertEqual(new_state["bias"], BIAS_BEARISH)
        self.assertEqual(new_state["context"], "ns")


class TestLegacyArchive(unittest.TestCase):
    def test_archive_renames_legacy_when_state_absent(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, LEGACY_ZONES_FILE)
            with open(legacy, "w") as f:
                json.dump({"reversal_zones": []}, f)
            self.assertTrue(archive_legacy_zones(d))
            self.assertFalse(os.path.exists(legacy))
            self.assertTrue(os.path.exists(os.path.join(d, LEGACY_ARCHIVE_FILE)))

    def test_archive_noop_when_state_present(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, LEGACY_ZONES_FILE)
            state = os.path.join(d, ZONE_STATE_FILE)
            with open(legacy, "w") as f:
                json.dump({}, f)
            with open(state, "w") as f:
                json.dump({"zones": []}, f)
            self.assertFalse(archive_legacy_zones(d))
            self.assertTrue(os.path.exists(legacy))

    def test_archive_noop_when_no_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(archive_legacy_zones(d))

    def test_archive_noop_when_archive_already_exists(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, LEGACY_ZONES_FILE), "w") as f:
                json.dump({}, f)
            with open(os.path.join(d, LEGACY_ARCHIVE_FILE), "w") as f:
                json.dump({}, f)
            self.assertFalse(archive_legacy_zones(d))


class TestLegacyCompatView(unittest.TestCase):
    def test_compat_view_only_includes_active(self):
        a = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY", "live")
        b = build_zone(4900, ZONE_TYPE_RESISTANCE, ZONE_STRENGTH_STRONG, "SELL")
        mark_invalidated(b, "break")
        state = {"bias": BIAS_BULLISH, "context": "ctx", "zones": [a, b]}
        view = legacy_compat_view(state)
        self.assertEqual(len(view["reversal_zones"]), 1)
        self.assertEqual(view["reversal_zones"][0]["price"], 4800)
        self.assertEqual(view["bias"], BIAS_BULLISH)
        self.assertEqual(view["context"], "ctx")


if __name__ == "__main__":
    unittest.main()
