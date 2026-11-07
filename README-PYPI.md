# agent-money-primitives

Money primitives, fee curves, and reference-bound paywalls for AI agents that
handle real balances.

**44 tests. Zero dependencies.** Pure stdlib Python.

---

## What this is

Three modules, extracted from a live autonomous agent that tracked real funds
on Solana for a month:

- **`money.py`** — `Decimal` primitives that make it hard to be quietly wrong
- **`fee_curve.py`** — escalating-liability arithmetic, pure functions
- **`paywall.py`** — pay-per-call payment verification that cannot be replayed

Most agent finance code is prompt-shaped: "track your expenses carefully." That
fails silently. This is the code shape.

---

## Install

```bash
pip install agent-money-primitives     # or: cp -r agent_money/ your-project/
```

No dependencies. Python 3.9+.

---

## Quick start

```python
from agent_money import money, fee_curve

# Money that cannot drift
from decimal import Decimal
money.fmt(0)                      # "0.00"  - never "0"
money.clamp_zero(Decimal("-5"))   # Decimal("0.00")
money.refuse_sub_cent("1.089559") # raises MoneyError
money.normalise_sign("expense", "25.50")   # Decimal("-25.50")

# Escalating fees: DAILY_FEE(n) = 5n, TOTAL(n) = 5n(n+1)/2
fee_curve.daily_fee(3)            # Decimal("15.00")
fee_curve.lifetime_fees(10)       # Decimal("275.00")
fee_curve.billable_days("2026-01-01T00:00:00Z", "2026-01-16T00:00:00Z")  # 8
fee_curve.month_key("2026-01-31T23:30:00Z")   # "2026-02" - Zurich, not UTC
```

## Pay-per-call endpoint

```python
from agent_money import Paywall, PaymentError

pw = Paywall(payee="YOUR_SOLANA_ADDRESS", price="0.001", decimals=6)

@app.route("/v1/data")
def data():
    proof = request.headers.get("X-PAYMENT")
    ref = request.headers.get("X-PAYMENT-REF")
    if not proof:
        # 402: hand the payer a single-use reference to put in the tx memo
        return pw.issue_challenge(resource="/v1/data"), 402
    try:
        pw.verify(proof, resource="/v1/data", reference=ref)
    except PaymentError as e:
        return {"error": str(e)}, 403
    return {"data": "..."}
```

The `reference` is the part people skip, and skipping it is how you give your
product away. See [`docs/patterns.md`](docs/patterns.md) §4.

---

## Why the guards look paranoid

Each one is regression cover for a defect that actually happened:

| Guard | What it prevented |
|---|---|
| `Decimal` everywhere, no SQL `REAL` | cent-precision sums drifting in reports |
| sign normalised in one function | an inverted ledger with plausible totals |
| `clamp_zero` instead of `max(0, x)` | `"0"` breaking two-decimal formatting |
| sub-cent amounts refused | a 6-decimal USDC amount corrupting a cent ledger |
| reference-bound payments | the product served for free, booked as revenue |
| mock/live receipts split | **$0.004 of revenue that never existed** |
| month keys in the settlement timezone | a settlement attributed to the wrong month |

Full write-up in [`docs/patterns.md`](docs/patterns.md).

---

## Tests

```bash
python -m unittest discover -s tests -v
```

```
Ran 44 tests - OK
```

The `UnboundPaymentTests` class reproduces a live payment-binding exploit in
miniature. If those pass when they should fail, the paywall is broken.

---

## License

MIT. See `LICENSE`.
