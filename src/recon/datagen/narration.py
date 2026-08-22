"""Bank narration synthesis, including the deliberate corruption of it.

The narration is the only field that carries a reference back to the gateway,
so how it is written -- and how it is mangled -- determines which matching
strategy can succeed:

  * intact reference        -> deterministic exact match, no LLM needed
  * recoverably corrupted   -> fuzzy/LLM adjudication has a real chance
  * reference destroyed     -> only amount+date evidence remains

The generator records that distinction in the answer key so the report can
separate "the LLM recovered a mangled reference" from "the LLM guessed".
"""

from __future__ import annotations

import random

BANK_CODES = ["HDFC0000060", "ICIC0000104", "UTIB0000009", "KKBK0000958", "YESB0000262"]

CLEAN_TEMPLATES = [
    "NEFT-RAZORPAY SOFTWARE PVT LTD-{ref}-SETTLEMENT",
    "IMPS/P2A/{digits}/RZPY/{ref}",
    "UPI-RAZORPAYSOFTWARE-{ref}-SETTL",
    "NEFT CR-{ifsc}-RAZORPAY SOFTWARE PVT LTD-{ref}",
    "RTGS-RAZORPAY-{ref}-MERCHANT SETTLEMENT",
    "MMT/IMPS/{digits}/RAZORPAY/{ref}",
]

BATCH_TEMPLATES = [
    "NEFT-RAZORPAY SOFTWARE PVT LTD-{ref}-BULK SETTLEMENT",
    "RZPY BULK SETTLEMENT REF {ref}",
    "NEFT CR-{ifsc}-RAZORPAY SOFTWARE-BATCH {ref}",
]

# Credits that have no gateway counterpart at all: chargeback reversals, manual
# credits, interest, another merchant's money landing in the wrong account.
FOREIGN_TEMPLATES = [
    "NEFT CR-{ifsc}-CHARGEBACK REVERSAL-{digits}",
    "INT.PD:{digits} SAVINGS INTEREST",
    "NEFT CR-{ifsc}-ACME RETAIL PVT LTD-INV{digits}",
    "IMPS/P2A/{digits}/REFUND CR",
    "BY CASH DEPOSIT {digits}",
    "NEFT CR-{ifsc}-PAYU PAYMENTS PVT LTD-{digits}",
]

# Confusable substitutions, the way an OCR pass or a careless keying operator
# would produce them.  All are reversible by an LLM that knows the alphabet of
# valid references, which is exactly the capability being tested.
CONFUSABLES = {
    "0": "O", "O": "0",
    "1": "I", "I": "1",
    "5": "S", "S": "5",
    "8": "B", "B": "8",
    "2": "Z", "Z": "2",
    "6": "G", "G": "6",
}


def _digits(rng: random.Random, n: int = 12) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def make_reference(order_id: str) -> str:
    """Bank systems uppercase everything; the matcher must normalise case."""
    return order_id.replace("order_", "").upper()


def clean_narration(rng: random.Random, ref: str, batch: bool = False) -> str:
    template = rng.choice(BATCH_TEMPLATES if batch else CLEAN_TEMPLATES)
    return template.format(ref=ref, digits=_digits(rng), ifsc=rng.choice(BANK_CODES))


def foreign_narration(rng: random.Random) -> str:
    """Narration for a credit that legitimately has no gateway source."""
    return rng.choice(FOREIGN_TEMPLATES).format(
        digits=_digits(rng), ifsc=rng.choice(BANK_CODES)
    )


def _confusable_typos(rng: random.Random, ref: str, n: int = 2) -> str:
    chars = list(ref)
    positions = [i for i, c in enumerate(chars) if c.upper() in CONFUSABLES]
    rng.shuffle(positions)
    for i in positions[:n]:
        chars[i] = CONFUSABLES[chars[i].upper()]
    return "".join(chars)


def garble(rng: random.Random, narration: str, ref: str) -> tuple[str, bool]:
    """Corrupt a narration.

    Returns (garbled_narration, reference_recoverable).  `reference_recoverable`
    is the ground truth for whether *any* amount of clever reading could get the
    reference back -- when it is False, a matcher that produces a confident
    reference-based match is by definition hallucinating, and a correct agent
    should fall back to amount+date evidence or abstain.

    Only the `destroyed` mode sets it False, and it does so by construction: the
    narration is rebuilt from scratch with no reference in it.  Every other mode
    leaves the reference readable in principle, however mangled it looks.  The
    self-validation suite re-derives this claim from the emitted strings rather
    than trusting the label.
    """
    mode = rng.choices(
        ["typo", "merge", "truncate", "noise", "wrapped", "destroyed"],
        weights=[0.24, 0.16, 0.18, 0.12, 0.10, 0.20],
    )[0]

    if mode == "typo":
        return narration.replace(ref, _confusable_typos(rng, ref)), True

    if mode == "merge":
        # Separators eaten by a downstream system: the reference is still all
        # there, just no longer delimited.
        return narration.replace("-", "").replace("/", "").replace(" ", ""), True

    if mode == "truncate":
        # Field-width truncation.  Always recoverable, and the arithmetic says
        # so: the reference alphabet is 62 chars, so a surviving 6-char prefix
        # spans 62**6 ~= 5.7e10 possibilities.  Across a batch of a few thousand
        # orders the chance of two sharing a 6-char prefix is negligible, so a
        # prefix lookup resolves it uniquely.  An earlier version of this
        # function labelled short truncations "destroyed", which would have
        # scored a perfectly sound prefix match as a hallucination.
        cut = rng.randint(6, 11)
        kept = ref[:cut]
        truncated = narration.replace(ref, kept)
        limit = truncated.find(kept) + len(kept)
        return truncated[:limit], True

    if mode == "noise":
        # Stray characters injected mid-reference.
        pos = rng.randint(1, max(1, len(ref) - 1))
        noisy = ref[:pos] + rng.choice(["*", "#", " ", "."]) + ref[pos:]
        return narration.replace(ref, noisy), True

    if mode == "wrapped":
        # Reference buried in a much longer free-text blob.
        return (
            f"NEFT CR-{rng.choice(BANK_CODES)}-RAZORPAY SOFTWARE PRIVATE LIMITED "
            f"MERCHANT PAYOUT AGAINST INVOICE {_digits(rng, 8)} {ref} "
            f"VALUE DATE {_digits(rng, 6)} CHRG NIL",
            True,
        )

    # destroyed: the reference is simply not in the string any more.
    return (
        f"NEFT CR-{rng.choice(BANK_CODES)}-RAZORPAY SOFTWARE PVT LTD SETTLEMENT",
        False,
    )
