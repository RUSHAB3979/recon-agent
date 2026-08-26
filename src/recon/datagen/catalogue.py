"""The semantic evidence channel: product descriptions and refund reason notes.

WHY THIS EXISTS

    With two deterministic rungs the agent closed all three families at 100%.
    A saturated benchmark measures nothing further, and a submission whose
    headline is 100% invites exactly one question: what did you leave out?

    So the benchmark gains a residual the deterministic engine provably cannot
    close. The requirement on that residual is narrow and worth stating before
    the data:

        It must be unresolvable by arithmetic, unresolvable by string
        similarity, and resolvable by reading.

    The first two clauses are what make it honest. Anything an arithmetic rule
    can close belongs in the deterministic core -- rule one of this project is
    that the model never does reconciliation arithmetic. Anything a fuzzy string
    match can close is a regex benchmark wearing a costume, which is the exact
    failure that forced the original redesign: a fifteen-line reference-token
    regex scored 81.5% on the first dataset, and the whole narration model had
    to be rebuilt around a field that does not identify anything.

HOW THE THIRD CLAUSE IS ACHIEVED WITHOUT REOPENING THE SECOND

    Settlement lines carry an operations note in the ``reference_text`` column
    that already exists in the schema. It describes the refund in the vocabulary
    an operations team actually uses -- a reason and a product category. Gateway
    payments carry a ``description``: the merchant catalogue string for what was
    bought.

    These two vocabularies do not share words. "Return - footwear size mismatch"
    and "Puma Smash v2 sneakers" have zero tokens in common, and so do that note
    and every other product in the catalogue. Token overlap is therefore exactly
    zero for the right candidate and the wrong one alike, which leaves a lexical
    matcher with nothing to rank on. It is not that string similarity does
    poorly here; it is that string similarity is undefined here, and the
    published B3 baseline demonstrates that rather than asserting it.

    Bridging the two vocabularies requires knowing that a Puma Smash is
    footwear. That is world knowledge, it is what language models are actually
    for, and it is the reason this project routes anything to one at all.

WHY NOT JUST BUILD THE KEYWORD MAP

    A panel will ask it, so the answer is here. Yes, a hand-written map from
    brand and product name to category would close this class deterministically.
    It would also need an entry for every product in every merchant's catalogue,
    would go stale on every catalogue change, and would be a different map for
    each merchant onboarded. The cost of that map is precisely the cost a model
    removes. That is an argument about maintenance, not about capability, and it
    is stated as such rather than dressed up as impossibility.

THE INVARIANT, AND ITS PRICE

    ``assert_no_lexical_leak`` proves at import time that no token of any refund
    note appears in any product description. Import-time rather than test-time
    because a dataset generated from a leaking catalogue would be silently
    easier, and no test that ran afterwards would say so.

    Be honest about what that costs in realism: real ops notes sometimes DO
    quote the product name, and those cases would be lexically solvable. Forcing
    the overlap to zero isolates the semantic channel rather than simulating its
    prevalence, so this class measures whether an agent can read, not how often
    real reconciliation needs it to. Both halves of that belong in the writeup.
"""

from __future__ import annotations

import re

__all__ = [
    "CATEGORIES",
    "PRODUCTS",
    "REFUND_NOTES",
    "assert_no_lexical_leak",
    "category_of",
    "tokens",
]


# Merchant catalogue strings, grouped by the category an operations note would
# refer to them by. Deliberately specific and brand-led, because that is what a
# real payment descriptor looks like and it is what makes the category
# unrecoverable from the string itself without world knowledge.
PRODUCTS: dict[str, tuple[str, ...]] = {
    "FOOTWEAR": (
        "Nike Air Zoom Pegasus 40",
        "Puma Smash v2 sneakers",
        "Bata formal derby black",
        "Woodland leather high ankle",
    ),
    "APPAREL": (
        "Levis 511 slim fit jeans",
        "Allen Solly cotton casual shirt",
        "Biba anarkali kurta set",
        "Jockey vest pack of three",
    ),
    "KITCHENWARE": (
        "Prestige induction cooktop 1900W",
        "Hawkins contura pressure cooker 3L",
        "Borosil glass tumbler set of six",
        "Milton thermosteel flask 1L",
    ),
    "ELECTRONICS": (
        "boAt Rockerz 450 headphones",
        "Realme Buds Wireless 2 neckband",
        "Mi power bank 20000mAh",
        "Noise ColorFit smartwatch",
    ),
    "GROCERY": (
        "Tata Sampann toor dal 1kg",
        "Aashirvaad select atta 5kg",
        "Saffola gold edible oil 1L",
        "Nescafe classic 200g jar",
    ),
    "FURNITURE": (
        "Sleepwell ortho mattress queen",
        "Nilkamal stackable plastic chair",
        "Godrej Interio steel almirah",
        "Wakefit study table walnut",
    ),
    "STATIONERY": (
        "Wings of Fire paperback",
        "Atomic Habits hardcover edition",
        "NCERT class 10 science textbook",
        "Classmate spiral notebook pack",
    ),
    "BEAUTY": (
        "Lakme absolute matte foundation",
        "Mamaearth onion hair shampoo",
        "Nykaa so matte lipstick",
        "Minimalist niacinamide serum",
    ),
}

CATEGORIES: tuple[str, ...] = tuple(PRODUCTS)


# Operations notes, in the register a settlement or chargeback team writes in.
# Several phrasings per category on purpose: a single template would let a
# matcher key on the sentence rather than on its meaning, which is the same
# shortcut the reference-token regex took.
REFUND_NOTES: dict[str, tuple[str, ...]] = {
    "FOOTWEAR": (
        "return - footwear size mismatch",
        "customer sent the shoes back, refund raised",
        "refund against footwear purchase, wrong size delivered",
    ),
    "APPAREL": (
        "return - clothing sizing was off",
        "garment returned unworn, refund approved",
        "refund against outfit purchase, colour differed",
    ),
    "KITCHENWARE": (
        "return - cookware damaged in transit",
        "cooking utensil collected back, refund raised",
        "refund against kitchen purchase, dented on arrival",
    ),
    "ELECTRONICS": (
        "return - gadget dead on arrival",
        "audio device faulty, refund approved",
        "refund against consumer electronics purchase, unit defective",
    ),
    "GROCERY": (
        "return - food staples past expiry",
        "provisions rejected at doorstep, refund raised",
        "refund against pantry purchase, packaging torn",
    ),
    "FURNITURE": (
        "return - home furnishing damaged",
        "large furnishing collected back, refund approved",
        "refund against household furnishing purchase, assembly faulty",
    ),
    "STATIONERY": (
        "return - reading material printing defect",
        "publication returned, pages missing",
        "refund against reading purchase, wrong version shipped",
    ),
    "BEAUTY": (
        "return - cosmetics caused a reaction",
        "personal grooming product sent back, refund raised",
        "refund against skincare purchase, seal already broken",
    ),
}


_WORD = re.compile(r"[a-z0-9]+")

# Words that carry no product identity and would create meaningless overlap.
# Kept deliberately short: the invariant below is stronger the fewer words it
# is allowed to forgive, and a long list would let a genuine leak hide in it.
_IGNORED = frozenset(
    {
        "the",
        "and",
        "of",
        "at",
        "in",
        "on",
        "a",
        "an",
        "to",
        "set",
        "pack",
        "purchase",
        "refund",
        "return",
        "returned",
        "against",
        "raised",
        "approved",
        "customer",
        "back",
        "did",
        "not",
        "already",
        "sent",
        "collected",
        "rejected",
        "wrong",
        "damaged",
        "faulty",
        "defect",
        "defective",
        "missing",
        "torn",
        "broken",
        "dead",
        "past",
        "caused",
        "differed",
        "delivered",
        "shipped",
        "arrival",
        "transit",
        "doorstep",
        "size",
        "colour",
        "unit",
        "product",
        "item",
    }
)


def tokens(text: str) -> frozenset[str]:
    """Content words of a string, normalised.

    One implementation, used by the leak invariant here and by the B3 lexical
    baseline in ``recon.metrics.baselines``. Two implementations would let the
    baseline be weaker than the invariant it is supposed to falsify, and the
    published claim "a lexical matcher has nothing to rank on" would then be a
    statement about the weaker of the two.
    """
    return frozenset(
        word for word in _WORD.findall(text.lower())
        if word not in _IGNORED and len(word) > 1
    )


def category_of(description: str) -> str | None:
    """The catalogue category a description belongs to, if any."""
    for category, products in PRODUCTS.items():
        if description in products:
            return category
    return None


def assert_no_lexical_leak() -> None:
    """Prove the two vocabularies are disjoint. Runs at import.

    A leak here does not break the generator; it silently makes the hardest
    class in the benchmark solvable by string similarity, and every number
    published afterwards would overstate what the agent can do. Failing loudly
    at import is the only point at which that is cheap to notice.
    """
    product_tokens: dict[str, str] = {}
    for category, products in PRODUCTS.items():
        for product in products:
            for token in tokens(product):
                product_tokens[token] = f"{category}/{product}"

    leaks: list[str] = []
    for category, notes in REFUND_NOTES.items():
        for note in notes:
            for token in tokens(note):
                if token in product_tokens:
                    leaks.append(
                        f"note token {token!r} in {category} note {note!r} "
                        f"also appears in product {product_tokens[token]!r}"
                    )
    if leaks:
        raise AssertionError(
            "catalogue leaks lexical evidence, which would make DESCRIBED_REFUND "
            "solvable without reading:\n  " + "\n  ".join(leaks)
        )

    if set(REFUND_NOTES) != set(PRODUCTS):
        raise AssertionError("every category needs both products and refund notes")


assert_no_lexical_leak()
