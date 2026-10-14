"""Tests for the Stripe checkout module.

The account did not exist when this was written, so the Stripe-facing paths are
tested against a stubbed transport. What IS verified for real: the guards that
stop the wrong amount being charged, and the rule that the secret key never
appears in an error message.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sell"))

import stripe_checkout as sc  # noqa: E402

# Deliberately key-SHAPED but not key-like: real-looking prefixes trip
# secret scanners even when the value is a documented example.
SECRET = "sk_test_" + "X" * 24


class GuardTests(unittest.TestCase):
    def setUp(self):
        os.environ[sc.ENV_KEY] = SECRET

    def tearDown(self):
        os.environ.pop(sc.ENV_KEY, None)

    def test_unconfigured_raises_not_configured(self):
        os.environ.pop(sc.ENV_KEY, None)
        with self.assertRaises(sc.NotConfigured):
            sc._key()

    def test_publishable_key_is_refused(self):
        os.environ[sc.ENV_KEY] = "pk_test_" + "X" * 24
        with self.assertRaises(sc.StripeError) as ctx:
            sc._key()
        self.assertIn("publishable", str(ctx.exception).lower())

    def test_live_key_warns_loudly(self):
        os.environ[sc.ENV_KEY] = "sk_live_" + "X" * 24
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            sc._key()
        self.assertIn("LIVE", buf.getvalue())

    def test_float_price_is_refused(self):
        with self.assertRaises(sc.StripeError) as ctx:
            sc.create_checkout_session(
                price_amount_cents=49.99, product_name="x",
                success_url="https://e.com/ok", cancel_url="https://e.com/no")
        self.assertIn("integer", str(ctx.exception))

    def test_non_positive_price_is_refused(self):
        for bad in (0, -100):
            with self.assertRaises(sc.StripeError):
                sc.create_checkout_session(
                    price_amount_cents=bad, product_name="x",
                    success_url="https://e.com/ok", cancel_url="https://e.com/no")

    def test_insecure_urls_are_refused(self):
        with self.assertRaises(sc.StripeError):
            sc.create_checkout_session(
                price_amount_cents=4900, product_name="x",
                success_url="http://insecure.example/ok",
                cancel_url="https://e.com/no")

    def test_status_never_reveals_the_secret(self):
        st = sc.status()
        blob = str(st)
        self.assertNotIn(SECRET, blob)
        self.assertTrue(st["configured"])
        self.assertEqual(st["mode"], "test")


class SecretLeakTests(unittest.TestCase):
    def test_http_error_never_echoes_the_authorization_header(self):
        os.environ[sc.ENV_KEY] = SECRET

        import io
        from email.message import Message

        from urllib import request as urlreq

        real_urlopen = urlreq.urlopen

        def fake_urlopen(req, timeout=None):
            # A genuine HTTPError subclass instance, so `except HTTPError`
            # actually matches. read() returns Stripe's error body.
            return (_ for _ in ()).throw(
                sc.error.HTTPError(
                    req.full_url, 401, "Unauthorized",
                    Message(), io.BytesIO(
                        b'{"error":{"message":"Invalid API Key provided"}}')
                )
            )

        try:
            sc.request.urlopen = fake_urlopen
            with self.assertRaises(sc.StripeError) as ctx:
                sc._post("/checkout/sessions", {"mode": "payment"})
        finally:
            sc.request.urlopen = real_urlopen
            os.environ.pop(sc.ENV_KEY, None)

        msg = str(ctx.exception)
        self.assertNotIn(SECRET, msg)
        self.assertNotIn("Authorization", msg)
        self.assertNotIn("Bearer", msg)
        self.assertIn("unauthorized", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
