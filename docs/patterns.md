# Patterns: why each guard exists

Every rule in `agent_ledger` is here because its absence caused a real defect
during the build this package was extracted from. None of them are stylistic
preferences. If you only read one section, read **The four that cost money**.

---

## The four that cost money

### 1. Money is `Decimal` end to end, never `REAL`

SQLite `REAL` is a double. Summing cent-precision values in SQL drifts, and the
sum renders as `15.0` instead of `15.00`. Fetch the rows, sum in Python as
`Decimal`.

```python
# WRONG - silent precision loss
SELECT COALESCE(SUM(CAST(amount_usd AS REAL)), 0) FROM events;

# RIGHT
total = sum_decimals(row["amount_usd"] for row in rows)
```

The drift is small per row and invisible in a report, which is exactly what
makes it dangerous. It surfaces as a reconciliation failure weeks later.

### 2. Normalise sign at the write boundary, in ONE function

If a CLI helper negates expenses but the library does not, any other caller
writes an inconsistent ledger — and every downstream sum silently inverts
meaning. No error is raised. The totals just look plausible and are wrong.

```python
NEGATIVE_KINDS = ("expense", "other_liability", "fee", "payout")

def normalise_sign(kind, amount):
    value = parse(amount)
    if kind in NEGATIVE_KINDS:
        return -abs(value)      # magnitude in, sign out
    ...
```

`abs()` on the way in matters: a caller passing `-25.50` for an expense and
another passing `25.50` must produce the same stored value.

### 3. `max(Decimal("0"), x)` is not a money zero-clamp

When `x == Decimal("0.00")`, `max` returns `Decimal("0")`, whose `str()` is
`"0"`. That breaks two-decimal formatting everywhere downstream, and it only
shows up at exactly zero — the most common balance there is.

```python
def clamp_zero(amount):
    value = parse(amount).quantize(CENT)
    return ZERO if value < 0 else value    # ZERO == Decimal("0.00")
```

### 4. A payment is only real if it is bound to the request

This one cost a live incident. The first payment verifier asked only "did value
arrive at this address". The operator's own funding transaction was submitted as
a payment proof; the product was served for free and recorded as revenue. Any
past transfer into the address could be replayed the same way.

Four checks, none sufficient alone:

| Check | Failure it prevents |
|---|---|
| **reference** — single-use nonce issued with the 402, must appear in the tx memo | replaying any past transfer as proof of payment |
| **exact** — amount must equal the price | an overpayment silently passing |
| **recency** — transaction must be recent | replaying an old but valid transfer |
| **signature** — spendable exactly once | the same proof twice |

See `paywall.py` and `tests/test_paywall.py::UnboundPaymentTests`, which
reproduces the incident in miniature. If any of those tests pass when it should
fail, the paywall is broken.

---

## The rest

### Never report an unavailable external value as zero

An unreachable RPC and a genuinely empty account are different facts. Reporting
`$0.00` for a failed read invents solvency. Return `None`, render `UNKNOWN`.

### Mock/test data never shares a state path with production accounting

A demo run wrote test payments into the production receipts file and the
dashboard reported **$0.004 of revenue that never existed**. Split the stores,
and in tests redirect the module's default path constant in `setUp` so the real
code path runs without touching production files.

### Round once, at the boundary

Quantize on write and refuse anything finer than a cent. Rounding at display
time lets error accumulate invisibly.

### Duplicate protection belongs in the schema

A partial unique index refuses a second accrual for the same period at the
database level, so a retried or double-run job cannot double-bill:

```sql
CREATE UNIQUE INDEX ux_fee_accrual_day
    ON events (experiment_day) WHERE kind = 'fee_accrual';
```

Discipline is not a constraint. The database is.

### Month keys need the settlement timezone, not UTC

23:30 UTC on 31 January is already 1 February in Zurich. A UTC-derived month
silently misattributes the settlement. Use `month_key()` with an explicit
timezone.

### Mark non-authoritative figures explicitly

When a local ledger mirrors an external authoritative system, stamp every
derived figure `authoritative=false` with a caveat. A projection must never be
mistakable for the official liability.

### Assert security invariants with tests that scan the source tree

A test that greps the package for private-key markers, signing calls, or
forbidden credential env-var names catches a regression no unit test can see —
and documents the boundary as executable policy.

---

## Build order that finds bugs cheaply

1. Model the fee/accrual rules as **pure functions first** — no I/O. Test period
   0, the first billable period, month rollover, and negative input. Arithmetic
   bugs found here cost nothing; found in a report, they cost credibility.
2. Schema with a partial unique index on the accrual key.
3. One write boundary that maps kind to sign and refuses sub-cent precision.
4. Derive reporting from that boundary; emit the calculation, not just the number.
5. External reads return `None` on failure.
6. Tests: arithmetic boundaries, sign normalisation *through the library API*,
   duplicate refusal, mock/live segregation, and a source-tree secret scan.
