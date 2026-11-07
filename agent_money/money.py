"""Money primitives for agents that track balances.

Extracted from PROJECT LAST STAND V2, where they were validated against real
on-chain funding and a live payment endpoint. Every guard here exists because
the absence of it caused a real defect during that build:

  * `quantize` + `refuse_sub_cent` - a 6-decimal USDC amount was once passed to
    a cent-precision ledger and silently corrupted the total.
  * `clamp_zero` - `max(Decimal("0"), x)` returns `Decimal("0")` when x is
    `Decimal("0.00")`, whose str() is "0" and breaks 2-decimal formatting.
  * `normalise_sign` - sign was flipped at the CLI but not in the library, so
    any other caller wrote an inverted ledger and every downstream sum
    silently reversed meaning.

These are the boring functions that stop money from being quietly wrong.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class MoneyError(ValueError):
    """Raised when an amount cannot be represented exactly in cents."""


def parse(amount: str | int | float | Decimal) -> Decimal:
    """Parse an amount to Decimal without going through binary float.

    Floats are accepted only via their string form, because
    `Decimal(0.1) != Decimal("0.1")` and that gap is where rounding bugs live.
    """
    if isinstance(amount, float):
        # str() first: Decimal(float) exposes the binary representation.
        return Decimal(str(amount))
    if isinstance(amount, int):
        return Decimal(amount)
    if isinstance(amount, Decimal):
        return amount
    if isinstance(amount, str):
        try:
            return Decimal(amount.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a number: {amount!r}") from exc
    raise MoneyError(f"unsupported amount type: {type(amount).__name__}")


def quantize(amount: str | int | float | Decimal) -> Decimal:
    """Round to cents, half-up, exactly once."""
    return parse(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def refuse_sub_cent(amount: str | int | float | Decimal) -> Decimal:
    """Return the amount in cents, or raise if it is finer than a cent.

    Rounding at write time is how error accumulates invisibly. Refuse instead:
    the caller decides how to handle precision it cannot represent.
    """
    value = parse(amount)
    rounded = value.quantize(CENT)
    if rounded != value:
        raise MoneyError(
            f"{value} has more than 2 decimal places; money is tracked in cents. "
            "Round explicitly at the boundary that owns the decision."
        )
    return rounded


def clamp_zero(amount: str | int | float | Decimal) -> Decimal:
    """Quantize to cents and clamp at zero, preserving 2-decimal formatting.

    Do NOT use `max(ZERO, x)`: when x == Decimal("0.00") it returns
    Decimal("0"), whose str() is "0" and breaks downstream formatting.
    """
    value = parse(amount).quantize(CENT)
    return ZERO if value < 0 else value


# Kinds whose magnitude must be stored as an outflow. Enforced in ONE place so
# no caller can write an inconsistent sign.
NEGATIVE_KINDS = ("expense", "other_liability", "fee", "payout")
POSITIVE_KINDS = ("revenue", "deposit", "refund_in")


def normalise_sign(kind: str, amount: str | int | float | Decimal) -> Decimal:
    """Map a kind to its sign so a single signed column is unambiguous."""
    value = parse(amount)
    if kind in NEGATIVE_KINDS:
        return -abs(value)
    if kind in POSITIVE_KINDS:
        return abs(value)
    return value


def fmt(amount: str | int | float | Decimal) -> str:
    """Render as a 2-decimal string. Always two decimals, never '0'."""
    return str(parse(amount).quantize(CENT))


def sum_decimals(values) -> Decimal:
    """Sum in Decimal. Never aggregate money in SQL with CAST(x AS REAL)."""
    total = ZERO
    for v in values:
        total += parse(v)
    return total
