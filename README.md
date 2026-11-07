# agent-money-lite

Money primitives, fee curves, and reference-bound paywalls for AI agents that
handle real balances.

**This repository is a mirror of the public PyPI package
[`agent-money-primitives`](https://pypi.org/project/agent-money-primitives/)
(v1.0.0).** Same source, same MIT license, zero dependencies, pure stdlib
Python ≥ 3.9.

```bash
pip install agent-money-primitives     # or: cp -r agent_money/ your-project/
```

See [README-PYPI.md](README-PYPI.md) for the original package README shipped
with the release artifact.

## What's in the free library

- **`money.py`** — `Decimal` primitives that make it hard to be quietly wrong
- **`fee_curve.py`** — escalating-liability arithmetic, pure functions
- **`paywall.py`** — pay-per-call payment verification that cannot be replayed

## Tests

The tests shipped in the public sdist run with plain `unittest` — no pytest
needed:

```bash
python -m unittest tests.test_core tests.test_paywall
```

Verified on this mirror: **36 tests, all passing.**

The sdist also contains `test_fulfill.py` and `test_stripe.py`, which import
`fulfill` and `stripe_checkout` — modules that ship in the paid pack, not in
the PyPI release. They are therefore not included here.

## The $49 Agent Ledger Pack (open-core)

This lite repo is the free half. The paid pack —
[Agent Ledger Pack on Gumroad, $49](https://blueframe7.gumroad.com/l/uyhor) —
adds everything that turns the primitives into a running system:

- **Templates** — ledger, fee-curve, and paywall scaffolds for your own agent
- **Docs** — the failure stories behind each primitive (the "why" in detail)
- **Integration recipes** — `fulfill` (order fulfillment) and
  `stripe_checkout` (Stripe Checkout wiring), plus Solana/x402 patterns
- **Support** — direct line for integration questions

The library here is identical to what you get on PyPI; the pack is where the
operational material lives.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 LAST STAND.
