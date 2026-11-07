"""Tests for the agent-ledger-pack money and fee primitives.

Every assertion maps to a defect that actually occurred during the build these
modules were extracted from. They are regression cover, not coverage theatre.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_money.money import (  # noqa: E402
    MoneyError, clamp_zero, fmt, normalise_sign, parse, quantize,
    refuse_sub_cent, sum_decimals,
)
from agent_money.fee_curve import (  # noqa: E402
    billable_days, daily_fee, lifetime_fees, month_key, parse_iso8601,
    preparation_remaining_seconds, projection, runway_days,
)

START = "2026-01-01T00:00:00Z"
PREP_END = "2026-01-08T00:00:00Z"  # exactly 168h later


class MoneyTests(unittest.TestCase):
    def test_float_never_enters_decimal_directly(self):
        # Decimal(0.1) is 0.1000000000000000055511151231257827...
        self.assertNotEqual(Decimal(0.1), Decimal("0.1"))
        self.assertEqual(parse(0.1), Decimal("0.1"))

    def test_quantize_rounds_half_up(self):
        self.assertEqual(quantize("1.005"), Decimal("1.01"))
        self.assertEqual(quantize("2.674"), Decimal("2.67"))

    def test_sub_cent_amounts_are_refused_not_rounded(self):
        with self.assertRaises(MoneyError):
            refuse_sub_cent("1.089559")
        self.assertEqual(refuse_sub_cent("1.08"), Decimal("1.08"))

    def test_clamp_zero_preserves_two_decimals(self):
        # The pitfall: max(Decimal("0"), Decimal("0.00")) returns Decimal("0").
        self.assertEqual(str(clamp_zero(Decimal("0.00"))), "0.00")
        self.assertEqual(str(clamp_zero(Decimal("-5"))), "0.00")
        self.assertEqual(str(clamp_zero(Decimal("3.5"))), "3.50")

    def test_sign_is_normalised_by_kind_not_by_caller(self):
        self.assertEqual(normalise_sign("expense", "25.50"), Decimal("-25.50"))
        self.assertEqual(normalise_sign("expense", "-25.50"), Decimal("-25.50"))
        self.assertEqual(normalise_sign("revenue", "-25.50"), Decimal("25.50"))
        self.assertEqual(normalise_sign("other_liability", "10"), Decimal("-10.00"))

    def test_fmt_always_two_decimals(self):
        self.assertEqual(fmt(0), "0.00")
        self.assertEqual(fmt("5"), "5.00")
        self.assertEqual(fmt(Decimal("1.5")), "1.50")

    def test_sum_uses_decimal_not_float(self):
        total = sum_decimals(["0.1", "0.2", "0.3"])
        self.assertEqual(total, Decimal("0.6"))
        self.assertEqual(str(total.quantize(Decimal("0.01"))), "0.60")

    def test_bad_input_raises_money_error(self):
        for bad in ("abc", "", None, [], {}):
            with self.assertRaises(MoneyError):
                parse(bad)


class FeeCurveTests(unittest.TestCase):
    def test_daily_fee_is_5n(self):
        self.assertEqual(daily_fee(1), Decimal("5.00"))
        self.assertEqual(daily_fee(10), Decimal("50.00"))
        with self.assertRaises(ValueError):
            daily_fee(0)

    def test_lifetime_fees_is_5n_n_plus_1_over_2(self):
        self.assertEqual(lifetime_fees(0), Decimal("0"))
        self.assertEqual(lifetime_fees(1), Decimal("5.00"))
        self.assertEqual(lifetime_fees(2), Decimal("15.00"))
        self.assertEqual(lifetime_fees(3), Decimal("30.00"))
        self.assertEqual(lifetime_fees(10), Decimal("275.00"))

    def test_custom_step(self):
        self.assertEqual(daily_fee(3, Decimal("2")), Decimal("6"))
        self.assertEqual(lifetime_fees(3, Decimal("2")), Decimal("12"))

    def test_free_window_is_exactly_168h(self):
        self.assertEqual(billable_days(START, PREP_END), 0)
        self.assertEqual(preparation_remaining_seconds(START, START), 168 * 3600)
        self.assertEqual(preparation_remaining_seconds(START, PREP_END), 0)

    def test_first_billable_day_starts_after_free_window(self):
        self.assertEqual(billable_days(START, "2026-01-07T23:59:59Z"), 0)
        self.assertEqual(billable_days(START, PREP_END), 0)
        self.assertEqual(billable_days(START, "2026-01-09T00:00:00Z"), 1)
        self.assertEqual(billable_days(START, "2026-01-16T00:00:00Z"), 8)

    def test_day_count_never_resets_at_month_boundary(self):
        # 40 completed billable days from START spans January and February.
        self.assertEqual(billable_days(START, "2026-02-17T00:00:00Z"), 40)
        self.assertGreater(lifetime_fees(40), lifetime_fees(30))

    def test_month_key_uses_settlement_timezone_not_utc(self):
        # 23:30 UTC on 31 Jan is 00:30 on 1 Feb in Zurich (CET, +01:00).
        self.assertEqual(month_key("2026-01-31T23:30:00Z"), "2026-02")
        self.assertEqual(month_key("2026-01-31T22:30:00Z"), "2026-01")

    def test_projection_is_labelled_non_authoritative(self):
        p = projection(START, "2026-01-16T00:00:00Z")
        self.assertTrue(p["projection"])
        self.assertEqual(p["billable_days_completed"], 8)
        self.assertEqual(Decimal(p["projected_accrued_fees"]), lifetime_fees(8))

    def test_runway_is_zero_when_revenue_cannot_cover_day_one(self):
        # day-1 fee is 5, so a constant 4/day never covers even the first billable
        # day: the position is insolvent immediately, which is 0 days, not None.
        self.assertEqual(runway_days("4", START, START), 0)
        self.assertEqual(runway_days("2.5", START, START), 0)

    def test_runway_is_none_only_with_no_revenue_at_all(self):
        self.assertIsNone(runway_days("0", START, START))
        self.assertIsNone(runway_days("-5", START, START))

    def test_runway_is_positive_when_revenue_exceeds_the_curve(self):
        # at 100/day the curve is overtaken well before day 20
        self.assertGreater(runway_days("100", START, START), 0)

    def test_naive_timestamp_is_treated_as_utc_not_guessed(self):
        self.assertEqual(parse_iso8601("2026-01-01T00:00:00").utcoffset().total_seconds(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
