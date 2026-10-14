"""agent_money - money primitives, fee curves, and reference-bound paywalls.

Extracted from PROJECT LAST STAND V2. Every guard here exists because its
absence caused a real defect during that build; see docs/patterns.md.
"""

from .money import MoneyError, clamp_zero, parse, quantize, refuse_sub_cent
from .fee_curve import billable_days, daily_fee, lifetime_fees, month_key
from .paywall import Paywall, PaymentError

__version__ = "1.0.0"
__all__ = [
    "MoneyError", "parse", "quantize", "refuse_sub_cent", "clamp_zero",
    "daily_fee", "lifetime_fees", "billable_days", "month_key",
    "Paywall", "PaymentError", "__version__",
]
