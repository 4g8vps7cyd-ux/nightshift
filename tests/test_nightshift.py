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

from nightshift import journal, metrics, paper, regime, signals
from nightshift.risk import (RiskRefusal, build_plan, liquidation_price,
                             position_size, protective_rails)


def flat_bars(count: int, price: float = 100.0, spread: float = 0.5, start_ts: int = 0):
    return [[start_ts + i * 60_000, price, price + spread, price - spread, price, 1.0]
            for i in range(count)]


def bar(ts_index: int, open_: float, high: float, low: float, close: float):
    return [ts_index * 60_000, open_, high, low, close, 1.0]


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
        # 0.6% stop: the fee gate is satisfiable at this distance (fees ~20% of risk).
        plan = build_plan("XAUUSDT", "long", 4130.0, 4105.2, 4192.0, equity=100.0,
                          leverage=50, risk_pct=1.0)
        self.assertGreater(plan.size, 0)
        self.assertAlmostEqual(plan.rr, 2.48, places=1)
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

    def test_fee_dominated_setup_is_refused(self):
        # 0.10% stop on a 0.06% taker fee: the round trip costs the whole risk budget.
        with self.assertRaises(RiskRefusal) as ctx:
            build_plan("X", "long", 100.0, 99.9, 105.0, equity=1000.0, leverage=50,
                       risk_pct=1.0, max_fee_ratio=0.30)
        self.assertIn("fee-dominated", str(ctx.exception))

    def test_wide_stop_passes_the_fee_gate(self):
        plan = build_plan("X", "long", 100.0, 99.4, 106.0, equity=1000.0, leverage=50,
                          risk_pct=1.0, max_fee_ratio=0.30)
        notes = " ".join(plan.notes)
        self.assertIn("fees", notes)
        self.assertLessEqual(plan.risk_usd, 1000.0 * 0.01 + 1e-9)

    def test_min_stop_pct_widens_a_tight_stop(self):
        plan = build_plan("X", "long", 100.0, 99.95, 100.2, equity=1000.0, leverage=50,
                          risk_pct=1.0, min_stop_pct=0.6, min_rr=1.5)
        self.assertAlmostEqual(plan.stop, 99.4, places=6)      # 0.6% of 100
        self.assertTrue(any("widened" in n for n in plan.notes))
        # the target is recomputed to preserve the original reward:risk ratio
        self.assertGreater(plan.target, 100.0)

    def test_widening_does_not_run_away_with_margin(self):
        with self.assertRaises(RiskRefusal):
            build_plan("X", "long", 100.0, 99.95, 101.0, equity=10.0, leverage=1,
                       risk_pct=100.0, min_stop_pct=0.6, min_rr=1.5, max_margin_pct=40.0)

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


class TestMetrics(unittest.TestCase):
    def test_win_rate_and_expectancy(self):
        rep = metrics.report([2.0, -1.0, 1.0, -1.0], [100, 102, 101, 102, 101],
                             start_equity=100.0, periods_per_year=365)
        self.assertEqual(rep.trades, 4)
        self.assertEqual(rep.win_rate, 0.5)
        self.assertAlmostEqual(rep.expectancy, 0.25, places=6)
        self.assertAlmostEqual(rep.profit_factor, 1.5, places=6)

    def test_max_drawdown_peak_to_trough(self):
        dd, peak, trough = metrics.max_drawdown([100, 120, 90, 95, 130])
        self.assertAlmostEqual(dd, 0.25, places=6)
        self.assertEqual(peak, 1)
        self.assertEqual(trough, 2)

    def test_sharpe_needs_dispersion(self):
        self.assertEqual(metrics.sharpe([1.0, 1.0, 1.0], 365), 0.0)
        self.assertEqual(metrics.sharpe([1.0], 365), 0.0)
        self.assertGreater(metrics.sharpe([1.0, 2.0, 1.5, 3.0], 365), 0.0)

    def test_sortino_ignores_upside_volatility(self):
        # Sortino divides by downside deviation only, so a series whose losses are
        # small relative to its dispersion scores *higher* than plain Sharpe.
        returns = [-1.0, 5.0, -2.0, 0.5]
        self.assertGreater(metrics.sortino(returns, 365), metrics.sharpe(returns, 365))

    def test_sortino_is_zero_without_downside(self):
        # No losses means no downside deviation to divide by: 0.0, not infinity.
        self.assertEqual(metrics.sortino([1.0, 5.0, 2.0, 0.5], 365), 0.0)


class TestPaperSimulator(unittest.TestCase):
    """The simulator is where a backtest lies to its author, so it is tested hardest."""

    def _breakout_series(self, outcome_bar):
        series = flat_bars(120, 100.0, 0.5)          # warmup: flat market
        series.append(bar(120, 100.0, 105.2, 99.9, 105.0))   # breakout close -> long signal
        # entry bar: its OPEN (105.4) differs from the signal bar's close (105.0),
        # so a fill at "the signal close" would be detectable.
        series.append(bar(121, 105.4, 105.5, 104.8, 105.4))
        series.append(outcome_bar)
        series += [bar(123 + i, 105.0, 105.1, 104.9, 105.0) for i in range(10)]
        return series

    def test_entry_fills_at_next_bar_open_not_the_signal_close(self):
        series = self._breakout_series(bar(122, 105.0, 105.4, 104.6, 105.0))
        result = paper.simulate_symbol("TESTUSDT", series, engine="momentum",
                                       start_equity=1000.0, size_step=0.001)
        self.assertTrue(result.trades, "the breakout should have produced a trade")
        first = result.trades[0]
        self.assertAlmostEqual(first.entry, 105.4, places=6)   # bar 121 open
        self.assertNotAlmostEqual(first.entry, series[120][4], places=6)  # not the signal close

    def test_stop_wins_when_one_bar_contains_both_levels(self):
        # entry 105.4 -> stop ~103.7, target ~108.8. This bar spans 90..110, so it
        # contains BOTH levels and the pessimistic ordering must pick the stop.
        series = self._breakout_series(bar(122, 105.0, 110.0, 90.0, 100.0))
        result = paper.simulate_symbol("TESTUSDT", series, engine="momentum",
                                       start_equity=1000.0, size_step=0.001)
        self.assertTrue(result.trades)
        first = result.trades[0]
        self.assertEqual(first.exit_reason, "stop")
        self.assertLess(first.pnl, 0)
        self.assertLess(first.exit, first.entry)

    def test_target_hit_is_profitable_and_fee_adjusted(self):
        series = self._breakout_series(bar(122, 105.0, 109.5, 104.9, 109.0))
        result = paper.simulate_symbol("TESTUSDT", series, engine="momentum",
                                       start_equity=1000.0, size_step=0.001)
        first = result.trades[0]
        self.assertEqual(first.exit_reason, "target")
        self.assertGreater(first.pnl, 0)
        gross = (first.exit - first.entry) * first.size
        self.assertLess(first.pnl, gross)          # fees were charged
        self.assertAlmostEqual(first.fees, first.size * first.entry * 0.0006 * 2, places=6)

    def test_equity_curve_matches_the_trade_list(self):
        series = self._breakout_series(bar(122, 105.0, 108.0, 104.9, 107.8))
        result = paper.simulate_symbol("TESTUSDT", series, engine="momentum", start_equity=500.0,
                                       size_step=0.001)
        self.assertEqual(len(result.equity_curve), len(result.trades) + 1)
        self.assertAlmostEqual(result.equity_curve[-1],
                               500.0 + sum(t.pnl for t in result.trades), places=6)

    def test_chop_is_skipped_not_traded(self):
        series = flat_bars(300, 100.0, 0.2)        # dead flat: ER ~ 0
        result = paper.simulate_symbol("TESTUSDT", series, engine="momentum", er_floor=0.10)
        self.assertEqual(len(result.trades), 0)
        self.assertGreater(result.skipped_chop, 0)

    def test_metrics_use_pooled_trades(self):
        series = self._breakout_series(bar(122, 105.0, 108.0, 104.9, 107.8))
        out = paper.run_paper({"TESTUSDT": series}, engine="momentum", start_equity=1000.0)
        self.assertIn("aggregate", out)
        self.assertEqual(out["aggregate"]["trades"], 1)
        self.assertIn("sharpe", out["aggregate"])

    def test_aggregate_uses_a_consistent_pooled_basis(self):
        """A multi-account panel must not divide its pooled PnL by one account's capital."""
        series = self._breakout_series(bar(122, 105.0, 109.5, 104.9, 109.0))
        out = paper.run_paper({"A": series, "B": series}, engine="momentum",
                              start_equity=1000.0)
        agg = out["aggregate"]
        self.assertEqual(out["convention"]["pooledStartEquity"], 2000.0)
        self.assertAlmostEqual(agg["finalEquity"], 2000.0 + agg["expectancy"] * agg["trades"],
                               places=3)
        self.assertAlmostEqual(
            agg["totalReturn"], (agg["finalEquity"] - 2000.0) / 2000.0, places=4)

    def test_align_topics_keeps_only_common_bars(self):
        a = flat_bars(10, 100.0)
        b = flat_bars(10, 100.0)[3:]
        aligned = paper.align_topics({"A": a, "B": b})
        self.assertEqual(len(aligned["A"]), len(aligned["B"]))
        self.assertEqual(len(aligned["A"]), 7)


class TestShippedDefaults(unittest.TestCase):
    """The default configuration must be the one the research actually validated."""

    def test_agent_defaults_match_the_sweep_survivor(self):
        from nightshift.agent import AgentConfig

        cfg = AgentConfig()
        self.assertEqual(cfg.engine, "reversion")
        self.assertEqual(cfg.timeframe, "15m")
        self.assertAlmostEqual(cfg.min_stop_pct, 1.2)
        self.assertAlmostEqual(cfg.er_floor, 0.10)
        self.assertAlmostEqual(cfg.max_fee_ratio, 0.30)

    def test_cli_defaults_match_the_agent_defaults(self):
        from nightshift.agent import AgentConfig
        from nightshift.cli import DEFAULTS

        cfg = AgentConfig()
        self.assertEqual(DEFAULTS["engine"], cfg.engine)
        self.assertEqual(DEFAULTS["timeframe"], cfg.timeframe)
        self.assertAlmostEqual(DEFAULTS["min_stop_pct"], cfg.min_stop_pct)
        self.assertAlmostEqual(DEFAULTS["er_floor"], cfg.er_floor)

    def test_default_config_satisfies_its_own_fee_gate(self):
        # 1.2% stop at a 0.06% taker fee -> fees are 10% of risk, well inside the cap.
        # Target kept at roughly 2x the stop distance so the RR floor also passes.
        entry = 4130.0
        stop = entry * (1 - 1.2 / 100)
        target = entry + (entry - stop) * 2
        plan = build_plan("XAUUSDT", "long", entry, stop, target, equity=1000.0,
                          leverage=50, risk_pct=1.0, min_stop_pct=1.2, max_fee_ratio=0.30)
        self.assertAlmostEqual(plan.stop, stop, places=4)
        self.assertGreater(plan.rr, 1.5)
        self.assertLess(plan.margin_usd, 400.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
