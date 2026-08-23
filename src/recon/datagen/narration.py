"""Real settlement narration is deliberately boring and non-identifying.

The UTR already provides the exact bank-to-summary join. Adding order IDs or
synthetic corruption here would turn a control-total benchmark back into the
regex benchmark whose measured failure motivated the redesign.
"""

from __future__ import annotations


def settlement_narration(bank: str, utr: str) -> str:
    """Return the sole narration format present in the benchmark."""
    return f"NEFT CR: {bank} {utr} RAZORPAY SETTLEMENT"
