"""Tests for fulfillment integrity.

The property that matters: a test-mode payment must never be summable into
revenue. This is the same discipline as the x402 mock/live receipts split, and
it exists because a demo run once reported $0.004 of revenue that never existed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sell"))

import fulfill as fl  # noqa: E402


class RevenueIntegrityTests(unittest.TestCase):
    """Test-mode payments are recorded but never counted as income."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "sales.jsonl"
        self._real = fl.SALES_FILE
        fl.SALES_FILE = self.tmp

    def tearDown(self):
        fl.SALES_FILE = self._real

    def _write(self, **kw):
        rec = {"session_id": "s", "amount_usd": "0", "currency": "usd",
               "livemode": True, "payment_status": "paid"}
        rec.update(kw)
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        with self.tmp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_test_mode_payment_is_excluded_from_revenue(self):
        self._write(session_id="t1", amount_usd="49", livemode=False)
        rev = fl.realized_revenue()
        self.assertEqual(rev["live_revenue_usd"], "0")
        self.assertEqual(rev["live_sales"], 0)
        self.assertEqual(rev["test_payments_usd"], "49")
        self.assertEqual(rev["test_payments"], 1)

    def test_live_payment_is_counted(self):
        self._write(session_id="l1", amount_usd="49", livemode=True)
        rev = fl.realized_revenue()
        self.assertEqual(rev["live_revenue_usd"], "49")
        self.assertEqual(rev["live_sales"], 1)

    def test_only_live_payments_sum_into_revenue(self):
        self._write(session_id="l1", amount_usd="49", livemode=True)
        self._write(session_id="t1", amount_usd="49", livemode=False)
        self._write(session_id="l2", amount_usd="99", livemode=True)
        rev = fl.realized_revenue()
        self.assertEqual(rev["live_revenue_usd"], "148")   # 49 + 99, never 197
        self.assertEqual(rev["live_sales"], 2)
        self.assertEqual(rev["test_payments_usd"], "49")

    def test_empty_ledger_is_zero_not_an_error(self):
        rev = fl.realized_revenue()
        self.assertEqual(rev["live_revenue_usd"], "0")
        self.assertEqual(rev["live_sales"], 0)

    def test_corrupt_lines_are_skipped(self):
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self.tmp.write_text('{"session_id":"ok","amount_usd":"10","livemode":true}\n'
                            'not json\n', encoding="utf-8")
        rev = fl.realized_revenue()
        self.assertEqual(rev["live_revenue_usd"], "10")

    def test_amount_conversion_is_decimal_not_float(self):
        # 4900 cents -> Decimal("49"), never 49.00000000000001
        self.assertEqual(Decimal(4900) / 100, Decimal("49"))
        self.assertEqual(str(Decimal(199) / 100), "1.99")

    def test_load_delivered_is_idempotent(self):
        fl._record_sale({"id": "cs_test_x", "amount_total": 4900,
                         "currency": "usd", "livemode": False,
                         "customer_details": {"email": "a@b.c"}})
        first = fl._load_delivered()
        self.assertIn("cs_test_x", first)
        self.assertEqual(fl._load_delivered(), first)

    def test_recorded_sale_defaults_to_not_delivered(self):
        fl._record_sale({"id": "cs_test_y", "amount_total": 4900,
                         "currency": "usd", "livemode": False,
                         "customer_details": {"email": "a@b.c"}})
        rec = json.loads(self.tmp.read_text().strip())
        self.assertFalse(rec["delivered"])
        self.assertEqual(rec["amount_usd"], "49")


if __name__ == "__main__":
    unittest.main(verbosity=2)
