"""Fulfillment without a webhook.

A webhook needs Stripe to reach me, which means public exposure - the thing we
deliberately have not done. This polls instead: list recent Checkout Sessions,
and for any that are `paid` and not yet delivered, mark them delivered and
record the sale.

Trade-off, stated honestly: polling has latency (minutes, not seconds) and costs
an API call per run. A webhook is better engineering. But a webhook requires
opening a door to this VM, and until the secret key lives somewhere other than
this box, that trade is not worth making. This works with zero exposure.

Delivery is a local record. Actually handing the buyer their files is a separate
step that needs a delivery channel (signed URL, email, or a download endpoint) -
that part still needs a decision about where it runs.

SECRET HANDLING: same rules as stripe_checkout.py. The key comes from the
environment only, is never written or logged, and never appears in an error.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib import error, request
from urllib.parse import urlencode

STRIPE_API = "https://api.stripe.com/v1"
ENV_KEY = "STRIPE_SECRET_KEY"

# Sales live in the project ledger, not in Stripe. Stripe is the payment record;
# this is the accounting record. They must never be conflated.
SALES_FILE = Path(__file__).resolve().parents[2] / "state" / "sales.jsonl"


class FulfillError(RuntimeError):
    """Never contains the secret key."""


def _key() -> str:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise FulfillError(f"{ENV_KEY} is not set")
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        raise FulfillError("key does not look like a Stripe secret key")
    return key


def _get(path: str, params: Optional[dict] = None) -> dict[str, Any]:
    url = f"{STRIPE_API}{path}"
    if params:
        url += "?" + urlencode(params)
    req = request.Request(url, headers={"Authorization": f"Bearer {_key()}"},
                          method="GET")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        kind = "unauthorized" if exc.code in (401, 403) else f"HTTP {exc.code}"
        raise FulfillError(f"stripe GET {path} -> {kind}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise FulfillError(f"stripe GET {path} unreachable: {exc}") from exc


def _load_delivered() -> set[str]:
    seen: set[str] = set()
    if SALES_FILE.exists():
        for line in SALES_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(json.loads(line)["session_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return seen


def _record_sale(session: dict) -> None:
    SALES_FILE.parent.mkdir(parents=True, exist_ok=True)
    amount = session.get("amount_total") or 0
    rec = {
        "session_id": session.get("id"),
        "amount_usd": str(Decimal(amount) / 100),   # cents -> Decimal, never float
        "currency": session.get("currency"),
        "customer_email": (session.get("customer_details") or {}).get("email"),
        "payment_status": session.get("payment_status"),
        "livemode": session.get("livemode"),
        "delivered": False,           # set True only when files actually go out
        "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with SALES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def poll(*, limit: int = 20) -> dict[str, Any]:
    """Find newly-paid sessions and record them as sales.

    Returns a summary. Never invents a sale: only sessions Stripe reports as
    `paid` are recorded, and test-mode sessions are flagged so they cannot be
    mistaken for revenue.
    """
    if _key().startswith("sk_test_"):
        mode = "test"
    else:
        mode = "live"

    data = _get("/checkout/sessions", {"limit": limit})
    delivered = _load_delivered()

    new_sales, skipped_test, already = [], 0, 0
    for s in data.get("data", []):
        sid = s.get("id")
        if s.get("payment_status") != "paid":
            continue
        if sid in delivered:
            already += 1
            continue
        if s.get("livemode") is False:
            # A test-mode charge is not revenue. Record it, but flag it hard so
            # it can never be summed into income.
            skipped_test += 1
        _record_sale(s)
        new_sales.append({
            "session_id": sid,
            "amount_usd": str(Decimal(s.get("amount_total") or 0) / 100),
            "livemode": s.get("livemode"),
        })

    return {
        "mode": mode,
        "checked": len(data.get("data", [])),
        "new_sales_recorded": len(new_sales),
        "already_recorded": already,
        "test_mode_payments_flagged": skipped_test,
        "sales": new_sales,
        "note": "test-mode payments are recorded but flagged and are never "
                "counted as revenue",
    }


def realized_revenue() -> dict[str, Any]:
    """Sum LIVE sales only. Test payments are excluded by construction."""
    live_total, test_total, live_n, test_n = Decimal("0"), Decimal("0"), 0, 0
    if SALES_FILE.exists():
        for line in SALES_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            amt = Decimal(rec.get("amount_usd", "0"))
            if rec.get("livemode"):
                live_total += amt
                live_n += 1
            else:
                test_total += amt
                test_n += 1
    return {
        "live_revenue_usd": str(live_total),
        "live_sales": live_n,
        "test_payments_usd": str(test_total),
        "test_payments": test_n,
        "note": "live_revenue_usd is the only figure that counts as income",
    }


def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        if argv and argv[0] == "revenue":
            print(json.dumps(realized_revenue(), indent=2))
            return 0
        print(json.dumps(poll(), indent=2))
        return 0
    except FulfillError as exc:
        print(f"fulfillment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
