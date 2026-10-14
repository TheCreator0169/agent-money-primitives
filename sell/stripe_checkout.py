"""Stripe Checkout session creation for the agent-ledger-pack.

Deliberately dependency-free: talks to Stripe's REST API over stdlib urllib, so
the product has no install step beyond Python itself.

SECRET HANDLING - read this before deploying:
  * The secret key comes from the environment ONLY: STRIPE_SECRET_KEY.
  * It is never written to disk, never logged, never included in an error
    message, and never returned by any function here.
  * A 401 from Stripe is reported as "unauthorized" without echoing the key.
  * This module cannot be used to move money out of the Stripe account. It
    creates checkout sessions only; refunds, payouts and transfers are the
    account owner's actions in the Stripe dashboard.

This module has NOT been exercised against a live Stripe account - no account
existed when it was written. The request shape follows Stripe's documented
Checkout Sessions API, and the transport/error paths are tested with a stub.
Verify with a live test-mode key before taking real payments.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib import error, request
from urllib.parse import urlencode

STRIPE_API = "https://api.stripe.com/v1"
ENV_KEY = "STRIPE_SECRET_KEY"
ENV_WEBHOOK_SECRET = "STRIPE_WEBHOOK_SECRET"


class StripeError(RuntimeError):
    """Any Stripe-side or transport failure. Never contains the secret key."""


class NotConfigured(StripeError):
    pass


def _key() -> str:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise NotConfigured(
            f"{ENV_KEY} is not set. Create a Stripe account, then export the "
            "secret key in the environment - never in a file inside the repo."
        )
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        raise StripeError(
            "key does not look like a Stripe secret key (expected sk_test_/sk_live_). "
            "Publishable keys (pk_...) cannot create sessions."
        )
    if key.startswith(("sk_live_", "rk_live_")):
        # Loud, not silent: live mode moves real money.
        print("WARNING: using a LIVE Stripe key. Real charges will be made.")
    return key


def _get(path: str) -> dict[str, Any]:
    """GET a Stripe resource. Retrieve endpoints are GET, not POST."""
    req = request.Request(
        f"{STRIPE_API}{path}",
        headers={"Authorization": f"Bearer {_key()}"},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        # Read the body but NEVER the request headers: the Authorization header
        # holds the secret and must not reach a log or an error string.
        detail = exc.read().decode("utf-8", "replace")[:400]
        kind = "unauthorized" if exc.code in (401, 403) else f"HTTP {exc.code}"
        raise StripeError(f"stripe GET {path} -> {kind}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise StripeError(f"stripe GET {path} unreachable: {exc}") from exc


def _post(path: str, fields: dict[str, Any]) -> dict[str, Any]:
    body = urlencode(fields, doseq=True).encode("utf-8")
    req = request.Request(
        f"{STRIPE_API}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {_key()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        # Read the body but NEVER the request headers: the Authorization header
        # holds the secret and must not reach a log or an error string.
        detail = exc.read().decode("utf-8", "replace")[:400]
        kind = "unauthorized" if exc.code in (401, 403) else f"HTTP {exc.code}"
        raise StripeError(f"stripe {path} -> {kind}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise StripeError(f"stripe {path} unreachable: {exc}") from exc


def create_checkout_session(
    *,
    price_amount_cents: int,
    currency: str = "usd",
    product_name: str,
    success_url: str,
    cancel_url: str,
    quantity: int = 1,
    metadata: Optional[dict[str, str]] = None,
    allow_promotion_codes: bool = True,
) -> dict[str, Any]:
    """Create a Checkout Session and return the public session data.

    `price_amount_cents` must be an integer number of cents. Passing a float
    here is the classic way to charge the wrong amount, so it is refused.
    """
    if isinstance(price_amount_cents, float):
        raise StripeError(
            "price must be an integer number of cents; a float invites rounding "
            "errors in the charged amount"
        )
    if price_amount_cents <= 0:
        raise StripeError("price must be positive")
    if not success_url.startswith("https://") or not cancel_url.startswith("https://"):
        raise StripeError("success_url and cancel_url must be https:// URLs")

    fields: dict[str, Any] = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": str(allow_promotion_codes).lower(),
        "line_items[0][quantity]": quantity,
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": int(price_amount_cents),
        "line_items[0][price_data][product_data][name]": product_name,
    }
    for i, (k, v) in enumerate((metadata or {}).items()):
        fields[f"metadata[{k}]"] = v

    session = _post("/checkout/sessions", fields)
    # Return only what a caller needs. The raw response can carry account
    # details that have no business being logged.
    return {
        "id": session.get("id"),
        "url": session.get("url"),
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "payment_status": session.get("payment_status"),
        "livemode": session.get("livemode"),
    }


def retrieve_session(session_id: str) -> dict[str, Any]:
    """Fetch a session by id. Used to confirm a completed purchase.

    Stripe's retrieve endpoint is GET /v1/checkout/sessions/{id}. A POST to
    .../retrieve returns 404 - that mistake was made once and is why this is a
    separate verb from session creation.
    """
    if not session_id:
        raise StripeError("session_id is required")
    session = _get(f"/checkout/sessions/{session_id}")
    return {
        "id": session.get("id"),
        "payment_status": session.get("payment_status"),
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "customer_email": session.get("customer_details", {}).get("email"),
        "livemode": session.get("livemode"),
    }


def status() -> dict[str, Any]:
    """Report configuration without revealing anything secret."""
    key = os.environ.get(ENV_KEY, "").strip()
    return {
        "configured": bool(key),
        "mode": ("live" if key.startswith(("sk_live_", "rk_live_"))
                 else "test" if key.startswith(("sk_test_", "rk_test_"))
                 else "unknown"),
        "key_prefix": key[:8] + "..." if len(key) > 12 else "",
        "webhook_secret_configured": bool(os.environ.get(ENV_WEBHOOK_SECRET)),
        "note": "creates checkout sessions only; cannot move money out of the account",
    }
