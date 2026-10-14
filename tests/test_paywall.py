"""Tests for reference-bound payment verification.

The first four tests reproduce, in miniature, the exact incident this module
was written to prevent: a payment that arrives at the right address but is not
bound to the request being served. If any of these pass when it should fail,
the paywall is broken.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_money.paywall import Paywall, PaymentError  # noqa: E402

PAYEE = "5hQf3xsLKhSVWTvZLJGCsD1zJcZkH3PQk7DxjPw7ggAh"
RES = "/v1/agent-work-index"
PRICE = "0.001"


def _now():
    return int(time.time())


def _paywall(**kw):
    params = dict(payee=PAYEE, price=PRICE, decimals=6)
    params.update(kw)
    return Paywall(**params)


class UnboundPaymentTests(unittest.TestCase):
    """The incident: value arrived at the payee, product was served anyway."""

    def test_operator_funding_tx_with_no_reference_is_refused(self):
        # Exactly the shape of the original exploit: a bare signature, correct
        # address, correct-ish amount, no binding to the request.
        pw = _paywall()
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("SomeRealFundingSignature", resource=RES,
                      amount_paid="1089559", memo=None, block_time=_now())
        self.assertIn("payment reference", str(ctx.exception))

    def test_correct_amount_but_wrong_memo_is_refused(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-1", resource=RES, reference=ch["payment_reference"],
                      amount_paid="1000", memo="someone-elses-memo",
                      block_time=_now())
        self.assertIn("memo", str(ctx.exception).lower())

    def test_overpayment_does_not_pass(self):
        # An overpayment is not a valid payment for this price.
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-1", resource=RES, reference=ch["payment_reference"],
                      amount_paid="1089559", memo=ch["payment_reference"],
                      block_time=_now())
        self.assertIn("exact amount", str(ctx.exception))

    def test_old_transaction_cannot_be_replayed(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        stale = _now() - 3600  # an hour old
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-1", resource=RES, reference=ch["payment_reference"],
                      amount_paid="1000", memo=ch["payment_reference"],
                      block_time=stale)
        self.assertIn("old", str(ctx.exception).lower())

    def test_reference_cannot_be_spent_twice(self):
        # A DIFFERENT signature with the same reference: the signature replay
        # guard must not be what fires, so this exercises reference-level
        # protection specifically.
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        pw.verify("sig-first", resource=RES, reference=ch["payment_reference"],
                  amount_paid="1000", memo=ch["payment_reference"], block_time=_now())
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-second", resource=RES, reference=ch["payment_reference"],
                      amount_paid="1000", memo=ch["payment_reference"], block_time=_now())
        self.assertIn("spent", str(ctx.exception).lower())

    def test_same_signature_cannot_be_reused_with_a_new_reference(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        pw.verify("sig-reused", resource=RES, reference=ch["payment_reference"],
                  amount_paid="1000", memo=ch["payment_reference"], block_time=_now())
        ch2 = pw.issue_challenge(resource=RES)
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-reused", resource=RES, reference=ch2["payment_reference"],
                      amount_paid="1000", memo=ch2["payment_reference"], block_time=_now())
        self.assertIn("already used", str(ctx.exception).lower())

    def test_reference_is_bound_to_its_resource(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-1", resource="/v1/other", reference=ch["payment_reference"],
                      amount_paid="1000", memo=ch["payment_reference"], block_time=_now())
        self.assertIn("different resource", str(ctx.exception))


class HappyPathTests(unittest.TestCase):
    def test_valid_bound_payment_is_accepted(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        res = pw.verify("sig-good", resource=RES, reference=ch["payment_reference"],
                        amount_paid="1000", memo=ch["payment_reference"],
                        block_time=_now())
        self.assertTrue(res["verified"])
        self.assertEqual(res["payment_reference"], ch["payment_reference"])
        self.assertEqual(res["amount"], PRICE)

    def test_challenge_advertises_exact_price_and_memo(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        self.assertEqual(ch["amount"], PRICE)
        self.assertEqual(ch["amount_smallest_unit"], "1000")
        self.assertEqual(ch["memo_required"], ch["payment_reference"])
        self.assertEqual(ch["payee"], PAYEE)

    def test_two_challenges_are_distinct(self):
        pw = _paywall()
        a = pw.issue_challenge(resource=RES)
        b = pw.issue_challenge(resource=RES)
        self.assertNotEqual(a["payment_reference"], b["payment_reference"])

    def test_price_scales_with_decimals(self):
        # 6dp USDC vs 9dp SOL: the smallest-unit conversion must follow decimals.
        usdc = _paywall(price="1.5", decimals=6)
        self.assertEqual(usdc.issue_challenge(resource=RES)["amount_smallest_unit"], "1500000")
        sol = _paywall(price="0.001", decimals=9)
        self.assertEqual(sol.issue_challenge(resource=RES)["amount_smallest_unit"], "1000000")

    def test_payee_is_required(self):
        with self.assertRaises(PaymentError):
            Paywall(payee="", price=PRICE)


class ChainReaderTests(unittest.TestCase):
    """Amount, memo and block time read from an injected chain reader."""

    class Reader:
        def __init__(self, tx):
            self.tx = tx

        def transaction(self, signature):
            return self.tx

    def _tx(self, *, amount=1000, memo=None, block_time=None, owner=PAYEE):
        return {
            "meta": {
                "err": None,
                "preTokenBalances": [{"owner": owner, "mint": "MINT",
                                      "uiTokenAmount": {"amount": "0"}}],
                "postTokenBalances": [{"owner": owner, "mint": "MINT",
                                       "uiTokenAmount": {"amount": str(amount)}}],
                "innerInstructions": [],
            },
            "transaction": {"message": {"instructions": [
                {"parsed": memo} if memo else {"programIdIndex": 0}
            ]}},
            "blockTime": block_time if block_time is not None else _now(),
        }

    def test_reads_amount_memo_and_time_from_the_transaction(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        pw.chain_reader = self.Reader(self._tx(amount=1000,
                                               memo=ch["payment_reference"]))
        res = pw.verify("sig-chain", resource=RES,
                        reference=ch["payment_reference"])
        self.assertTrue(res["verified"])

    def test_payment_to_a_different_owner_is_not_ours(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        pw.chain_reader = self.Reader(self._tx(amount=1000, owner="SomeoneElse",
                                               memo=ch["payment_reference"]))
        with self.assertRaises(PaymentError):
            pw.verify("sig-chain", resource=RES, reference=ch["payment_reference"])

    def test_failed_transaction_is_refused(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)
        tx = self._tx(amount=1000, memo=ch["payment_reference"])
        tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        pw.chain_reader = self.Reader(tx)
        with self.assertRaises(PaymentError) as ctx:
            pw.verify("sig-chain", resource=RES, reference=ch["payment_reference"])
        self.assertIn("failed on-chain", str(ctx.exception))

    def test_missing_transaction_is_refused_not_assumed_paid(self):
        pw = _paywall()
        ch = pw.issue_challenge(resource=RES)

        class Empty:
            def transaction(self, signature):
                return None

        pw.chain_reader = Empty()
        with self.assertRaises(PaymentError):
            pw.verify("sig-chain", resource=RES, reference=ch["payment_reference"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
