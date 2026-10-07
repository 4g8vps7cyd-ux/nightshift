"""Unit tests for the rails — the parts that must never be wrong.

Run with:  python -m unittest discover -s tests -v
No network, no credentials: risk logic is testable in isolation by design.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nightshift import journal, regime, signals
from nightshift.risk import (RiskRefusal, build_plan, liquidation_price,
                             position_size, protective_rails)


def candles_from_closes(closes: list[float]) -> list[list[float]]:
    return [[i * 60_000, c, c * 1.001, c * 0.999, c, 1.0] for i, c in enumerate(closes)]


class TestRegime(unittest.TestCase):
    def test_efficiency_ratio_trend_vs_chop(self):
        trend = candles_from_closes([100 + i for i in range(60)])
        chop = candles_from_closes([100 + (2 if i % 2 else -2) for i in range(60)])
        self.assertGreater(regime.efficiency_ratio(trend, 24), 0.9)
        self.assertLess(regime.efficiency_ratio(chop, 24), 0.1)

    def test_screen_rejects_chop(self):
        chop = candles_from_closes([100 + (1 if i % 2 else -1) for i in range(120)])
        verdict = regime.screen("TESTUSDT", chop, er_floor=0.10)
        self.assertFalse(verdict.tradeable)
        self.assertIn("chop", verdict.reason)

    def test_screen_accepts_trend_with_range(self):
        series = [100 + i * 0.6 for i in range(120)]
        verdict = regime.screen("TESTUSDT", candles_from_closes(series), er_floor=0.10,
                                range_floor=0.5)
        self.assertTrue(verdict.tradeable)
        self.assertEqual(verdict.trend, "up")

    def test_levels_are_graded(self):
        # A resistance hit many times at 110, plus a trend away from it.
        series = []
        for _ in range(6):
            series += [104, 106, 108, 110, 108, 106]
        series += [104 + i * 0.5 for i in range(20)]
        levels = regime.find_levels(candles_from_closes(series), tolerance_pct=0.5)
        self.assertTrue(levels)
        self.assertGreaterEqual(max(lv.touches for lv in levels), 2)


class TestSignals(unittest.TestCase):
    def test_rsi_bounds(self):
        up = candles_from_closes([100 + i for i in range(40)])
        down = candles_from_closes([100 - i for i in range(40)])
        self.assertGreater(signals.rsi(up), 70)
        self.assertLess(signals.rsi(down), 30)

    def test_momentum_breakout_long(self):
        series = [100] * 30 + [100 + i for i in range(1, 6)]
        sig = signals.momentum(candles_from_closes(series), symbol="T")
        self.assertEqual(sig.action, "long")
        self.assertLess(sig.stop, sig.entry)

    def test_momentum_breakdown_short(self):
        series = [100] * 30 + [100 - i for i in range(1, 6)]
        sig = signals.momentum(candles_from_closes(series), symbol="T")
        self.assertEqual(sig.action, "short")
        self.assertGreater(sig.stop, sig.entry)

    def test_exhaustion_fades_a_spike(self):
        series = [100] * 20 + [101, 103, 105, 107, 109]
        sig = signals.exhaustion(candles_from_closes(series), spike_pct=1.0, symbol="T")
        self.assertEqual(sig.action, "short")

    def test_flat_when_nothing_happens(self):
        sig = signals.momentum(candles_from_closes([100 + (0.1 if i % 2 else -0.1)
                                                    for i in range(60)]), symbol="T")
        self.assertEqual(sig.action, "flat")


class TestRisk(unittest.TestCase):
    def test_size_targets_exact_risk(self):
        size = position_size(equity=100.0, risk_pct=1.0, entry=4130.0, stop=4120.0)
        self.assertAlmostEqual(size * 10.0, 1.0, places=6)

    def test_size_respects_step(self):
        size = position_size(100.0, 1.0, 4130.0, 4120.0, size_step=0.01)
        self.assertAlmostEqual(size % 0.01, 0.0, places=8)

    def test_liquidation_sides(self):
        self.assertLess(liquidation_price(100.0, 50, "long"), 100.0)
        self.assertGreater(liquidation_price(100.0, 50, "short"), 100.0)

    def test_build_plan_happy_path(self):
        plan = build_plan("XAUUSDT", "long", 4130.0, 4120.0, 4158.0, equity=100.0,
                          leverage=50, risk_pct=1.0)
        self.assertGreater(plan.size, 0)
        self.assertAlmostEqual(plan.rr, 2.8, places=1)
        self.assertLess(plan.margin_usd, 100.0)

    def test_stop_on_wrong_side_is_refused(self):
        with self.assertRaises(RiskRefusal):
            build_plan("X", "long", 100.0, 101.0, 110.0, equity=100.0)

    def test_stop_beyond_liquidation_is_refused(self):
        # 100x on a 5% stop: liquidation (~1.5%) arrives long before the stop.
        with self.assertRaises(RiskRefusal):
            build_plan("X", "long", 100.0, 95.0, 120.0, equity=100.0, leverage=100)

    def test_poor_rr_is_refused(self):
        with self.assertRaises(RiskRefusal):
            build_plan("X", "long", 100.0, 99.0, 100.5, equity=100.0, min_rr=1.5)

    def test_margin_cap_is_refused(self):
        with self.assertRaises(RiskRefusal):
            build_plan("X", "long", 100.0, 99.9, 120.0, equity=1.0, leverage=1,
                       risk_pct=100.0, max_margin_pct=40.0)

    def test_rails_from_atr_when_signal_has_none(self):
        long_stop, long_target = protective_rails(entry=100.0, stop=0.0, target=0.0,
                                                  atr_value=1.0, direction="long")
        self.assertLess(long_stop, 100.0)
        self.assertGreater(long_target, 100.0)
        short_stop, short_target = protective_rails(entry=100.0, stop=0.0, target=0.0,
                                                    atr_value=1.0, direction="short")
        self.assertGreater(short_stop, 100.0)
        self.assertLess(short_target, 100.0)

    def test_direction_inferred_from_the_rail_that_exists(self):
        # stop below entry => long, so the derived target must sit above entry.
        stop, target = protective_rails(entry=100.0, stop=99.0, target=0.0, atr_value=0.5)
        self.assertGreater(target, 100.0)
        self.assertAlmostEqual(target, 102.0, places=6)
        # stop above entry => short, so the derived target must sit below entry.
        stop2, target2 = protective_rails(entry=100.0, stop=101.0, target=0.0, atr_value=0.5)
        self.assertLess(target2, 100.0)

    def test_atr_stop_is_clamped(self):
        # 5% ATR would be an absurd scalp stop; it is clamped to max_stop_pct.
        stop, _ = protective_rails(entry=100.0, stop=0.0, target=0.0, atr_value=5.0,
                                   direction="long", max_stop_pct=1.2)
        self.assertAlmostEqual(stop, 98.8, places=6)


class TestJournal(unittest.TestCase):
    def test_review_expectancy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            journal.record("closed", {"pnl": 2.0, "engine": "rsi"}, path)
            journal.record("closed", {"pnl": -1.0, "engine": "rsi"}, path)
            journal.record("refused", {"why": "test"}, path)
            stats = journal.review(path)
            self.assertEqual(stats["trades"], 2)
            self.assertEqual(stats["winRate"], 0.5)
            self.assertAlmostEqual(stats["expectancy"], 0.5, places=6)
            self.assertEqual(stats["refusals"], 1)

    def test_lessons_are_plain_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            journal.record("closed", {"pnl": -1.0, "engine": "momentum"}, path)
            lines = journal.lessons(path)
            self.assertTrue(any("Expectancy is not positive" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
