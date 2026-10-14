"""Reference-bound payment verification for pay-per-call endpoints.

This is the module whose absence caused a live security incident. During the
PROJECT LAST STAND V2 build, the first version of payment verification asked
only "did value arrive at this address". The operator's own funding
transaction was submitted as a payment proof and the product was handed over
for free, then recorded as revenue. Any past transfer into the address could be
replayed the same way.

A payment is only trustworthy if it is bound to the request that asked for it.
Every check below exists because omitting it was exploitable:

  reference   - single-use nonce issued with the 402, must appear in the tx memo
  exact       - amount must equal the price; an overpayment must not pass
  recency     - transaction must be recent, so past transfers cannot be replayed
  signature   - a signature may be spent exactly once

None of these alone is sufficient. All four together are.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from .money import parse

DEFAULT_TTL_SECONDS = 900       # time the payer has, after the 402
DEFAULT_MAX_AGE_SECONDS = 900   # how old the paying transaction may be


class PaymentError(RuntimeError):
    """Any verification failure. Never contains credentials."""


@dataclass
class Challenge:
    reference: str
    resource: str
    amount: str          # exact price, in the asset's smallest unit
    created: float = field(default_factory=time.time)
    used: bool = False

    def expired(self, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
        return (time.time() - self.created) > ttl


class Paywall:
    """Issue challenges and verify payments against them.

    Pluggable chain reader: pass any object exposing
    `transaction(signature) -> dict` shaped like a Solana jsonParsed
    getTransaction result. That keeps this module chain-agnostic and testable
    without a network.
    """

    def __init__(
        self,
        *,
        payee: str,
        price: str,
        asset: str = "USDC",
        decimals: int = 6,
        chain_reader: Optional[Any] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_tx_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        if not payee:
            raise PaymentError("payee address is required")
        self.payee = payee
        self.price_smallest = str(int(parse(price) * (10 ** decimals)))
        self.price = str(parse(price))
        self.asset = asset
        self.decimals = decimals
        self.chain_reader = chain_reader
        self.ttl_seconds = ttl_seconds
        self.max_tx_age_seconds = max_tx_age_seconds
        self._challenges: dict[str, Challenge] = {}
        self._spent_signatures: set[str] = set()

    # -- challenge ---------------------------------------------------------
    def issue_challenge(self, *, resource: str) -> dict[str, Any]:
        """Single-use payment reference for one unpaid request."""
        ref = uuid.uuid4().hex
        self._challenges[ref] = Challenge(reference=ref, resource=resource,
                                          amount=self.price_smallest)
        return {
            "payment_reference": ref,
            "amount": self.price,
            "amount_smallest_unit": self.price_smallest,
            "asset": self.asset,
            "payee": self.payee,
            "memo_required": ref,
            "expires_in_seconds": self.ttl_seconds,
        }

    def _take_challenge(self, reference: Optional[str], resource: str) -> Challenge:
        if not reference:
            raise PaymentError(
                "missing payment reference: every payment must carry the "
                "payment_reference returned with the 402"
            )
        ch = self._challenges.get(reference)
        if ch is None:
            raise PaymentError("unknown or expired payment reference")
        if ch.used:
            raise PaymentError("payment reference already spent")
        if ch.resource != resource:
            raise PaymentError("payment reference was issued for a different resource")
        if ch.expired(self.ttl_seconds):
            raise PaymentError("payment reference expired")
        ch.used = True  # burn before any network call
        return ch

    # -- verification ------------------------------------------------------
    def verify(self, signature: str, *, resource: str,
               reference: Optional[str] = None,
               amount_paid: Optional[str] = None,
               memo: Optional[str] = None,
               block_time: Optional[int] = None) -> dict[str, Any]:
        """Verify a payment. Raises PaymentError on any failed check.

        `amount_paid`, `memo` and `block_time` may be supplied directly (useful
        for tests and for chain readers that return them separately). When they
        are omitted, they are read from the injected chain_reader.
        """
        if not signature:
            raise PaymentError("no payment proof supplied")
        if signature in self._spent_signatures:
            raise PaymentError("payment proof already used (replay refused)")

        challenge = self._take_challenge(reference, resource)

        if self.chain_reader is not None and amount_paid is None:
            tx = self.chain_reader.transaction(signature)
            if not tx:
                raise PaymentError("transaction not found or not yet confirmed")
            if (tx.get("meta") or {}).get("err"):
                raise PaymentError("transaction failed on-chain")
            amount_paid = str(self._paid_to_payee(tx))
            memo = memo if memo is not None else self._memo(tx)
            block_time = block_time if block_time is not None else tx.get("blockTime")

        if amount_paid is None:
            raise PaymentError("no payment amount could be established")
        if parse(amount_paid) != parse(challenge.amount):
            raise PaymentError(
                f"paid {amount_paid} but {challenge.amount} required "
                "(exact amount only; an overpayment is not a valid payment)"
            )

        if block_time is None:
            raise PaymentError("no block time; cannot confirm the payment is recent")
        age = time.time() - int(block_time)
        if age > self.max_tx_age_seconds:
            raise PaymentError(
                f"transaction is {int(age)}s old; must be under "
                f"{self.max_tx_age_seconds}s so past transfers cannot be replayed"
            )

        if memo != challenge.reference:
            raise PaymentError(
                "transaction memo does not match the issued payment reference; "
                "the payment is not bound to this request"
            )

        self._spent_signatures.add(signature)
        return {
            "verified": True,
            "signature": signature,
            "payment_reference": challenge.reference,
            "amount": self.price,
            "amount_smallest_unit": challenge.amount,
            "asset": self.asset,
        }

    # -- chain-shape helpers (Solana jsonParsed) ---------------------------
    def _paid_to_payee(self, tx: dict) -> int:
        """Sum tokens credited to the payee across pre/post balances."""
        meta = tx.get("meta") or {}
        pre = {(b.get("owner"), b.get("mint")): int(b.get("uiTokenAmount", {}).get("amount", 0))
               for b in (meta.get("preTokenBalances") or [])}
        post = {(b.get("owner"), b.get("mint")): int(b.get("uiTokenAmount", {}).get("amount", 0))
                for b in (meta.get("postTokenBalances") or [])}
        total = 0
        for key, after in post.items():
            owner, _mint = key
            if owner != self.payee:
                continue
            total += max(0, after - pre.get(key, 0))
        return total

    def _memo(self, tx: dict) -> Optional[str]:
        """Extract a memo string from top-level and inner instructions."""
        out: list[str] = []
        msg = (tx.get("transaction") or {}).get("message") or {}
        for ix in (msg.get("instructions") or []):
            parsed = ix.get("parsed")
            if isinstance(parsed, str):
                out.append(parsed)
            elif isinstance(parsed, dict) and isinstance(parsed.get("memo"), str):
                out.append(parsed["memo"])
        for inner in ((tx.get("meta") or {}).get("innerInstructions") or []):
            for ix in (inner.get("instructions") or []):
                parsed = ix.get("parsed")
                if isinstance(parsed, str):
                    out.append(parsed)
                elif isinstance(parsed, dict) and isinstance(parsed.get("memo"), str):
                    out.append(parsed["memo"])
        return out[0] if out else None


def new_reference() -> str:
    """Cryptographically strong reference, if you would rather roll your own."""
    return secrets.token_hex(16)
