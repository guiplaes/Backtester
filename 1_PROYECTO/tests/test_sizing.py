"""Tests for sizing.py — multiplier validation + lot computation."""
import pytest
from sizing import validate_multiplier, lot_from_multiplier, fast_engine_lot, SizingError


class TestValidateMultiplier:
    def test_valid_int(self):
        assert validate_multiplier(1, 5) == 1
        assert validate_multiplier(5, 5) == 5

    def test_zero_rejected(self):
        with pytest.raises(SizingError):
            validate_multiplier(0, 5)

    def test_above_max_rejected(self):
        with pytest.raises(SizingError):
            validate_multiplier(6, 5)

    def test_float_rejected(self):
        with pytest.raises(SizingError):
            validate_multiplier(2.5, 5)

    def test_none_rejected(self):
        with pytest.raises(SizingError):
            validate_multiplier(None, 5)

    def test_string_rejected(self):
        with pytest.raises(SizingError):
            validate_multiplier("2", 5)

    def test_bool_rejected(self):
        # True == 1 in Python but we reject it explicitly
        with pytest.raises(SizingError):
            validate_multiplier(True, 5)


class TestLotFromMultiplier:
    def test_basic(self):
        assert lot_from_multiplier(0.03, 1) == 0.03
        assert lot_from_multiplier(0.03, 3) == 0.09
        assert lot_from_multiplier(0.03, 5) == 0.15

    def test_larger_base(self):
        assert lot_from_multiplier(0.10, 2) == 0.20

    def test_malformed_raises(self):
        with pytest.raises(SizingError):
            lot_from_multiplier(0.03, 0)
        with pytest.raises(SizingError):
            lot_from_multiplier(0.03, 10, max_multiplier=5)


class TestFastEngineLot:
    def test_strong_multiplier_1(self):
        assert fast_engine_lot(0.03, "STRONG", {"STRONG": 1, "MODERATE": 1, "WEAK": 0}) == 0.03

    def test_weak_disabled(self):
        assert fast_engine_lot(0.03, "WEAK", {"STRONG": 1, "MODERATE": 1, "WEAK": 0}) is None

    def test_unknown_strength(self):
        assert fast_engine_lot(0.03, "WHATEVER", {"STRONG": 1, "MODERATE": 1, "WEAK": 0}) is None

    def test_custom_multiplier(self):
        assert fast_engine_lot(0.05, "STRONG", {"STRONG": 2}) == 0.10
