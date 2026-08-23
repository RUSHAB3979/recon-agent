from __future__ import annotations

from recon.match.fuzzy import (
    MAX_CONFUSABLE_VARIANTS,
    edit_distance_at_most,
    expand_confusables,
    extract_candidate_tokens,
    recover_reference,
)


REFERENCE = "FOZUNFOLWYMQ2T"


def _index(*references: str) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for reference in references:
        index.setdefault(reference[:6], []).append(reference)
    return index


def test_clean_reference_recovers_exactly() -> None:
    result = recover_reference(
        f"NEFT CR-YESB0000262-RAZORPAY-{REFERENCE}",
        {REFERENCE},
        _index(REFERENCE),
    )

    assert result.candidates == [REFERENCE]
    assert result.strategy == "exact"
    assert result.scores == {REFERENCE: 0}


def test_single_confusable_substitution_recovers() -> None:
    damaged = REFERENCE.replace("O", "0", 1)

    result = recover_reference(damaged, {REFERENCE}, _index(REFERENCE))

    assert result.candidates == [REFERENCE]
    assert result.strategy == "confusable"
    assert result.scores == {REFERENCE: 1}


def test_double_confusable_substitution_recovers() -> None:
    damaged = REFERENCE.replace("O", "0", 1).replace("2", "Z", 1)

    result = recover_reference(damaged, {REFERENCE}, _index(REFERENCE))

    assert result.candidates == [REFERENCE]
    assert result.strategy == "confusable"
    assert result.scores == {REFERENCE: 2}


def test_leading_seven_character_truncation_recovers_by_prefix() -> None:
    result = recover_reference(REFERENCE[:7], {REFERENCE}, _index(REFERENCE))

    assert result.candidates == [REFERENCE]
    assert result.strategy == "prefix"
    assert result.scores == {REFERENCE: len(REFERENCE) - 7}


def test_inserted_punctuation_leaves_a_recoverable_prefix_token() -> None:
    narration = f"NEFT-{REFERENCE[:7]}/{REFERENCE[7:]}"

    assert extract_candidate_tokens(narration)[-2:] == [REFERENCE[:7], REFERENCE[7:]]
    result = recover_reference(narration, {REFERENCE}, _index(REFERENCE))

    assert result.candidates == [REFERENCE]
    assert result.strategy == "prefix"


def test_destroyed_reference_returns_no_survivors_without_raising() -> None:
    narration = "NEFT CR-YESB-FOZ-UNF-OLW-YMQ-2T"

    result = recover_reference(narration, {REFERENCE}, _index(REFERENCE))

    assert result.candidates == []
    assert result.strategy is None
    assert result.scores == {}


def test_confusable_expansion_is_capped() -> None:
    variants = expand_confusables("O1S8Z6" * 100, max_substitutions=10_000)

    assert "O1S8Z6" * 100 in variants
    assert len(variants) == MAX_CONFUSABLE_VARIANTS


def test_edit_distance_returns_none_when_bound_is_exceeded() -> None:
    assert edit_distance_at_most("ABCDEFGHIJKLMN", "ZYXWVUTSRQPONM", 2) is None
    assert edit_distance_at_most("ABC", "AXC", 2) == 1


def test_non_confusable_damage_uses_bucketed_edit_distance() -> None:
    reference = "ABCDEF12345678"
    damaged = "ABCDEF1X345678"

    result = recover_reference(damaged, {reference}, _index(reference))

    assert result.candidates == [reference]
    assert result.strategy == "edit_distance"
    assert result.scores == {reference: 1}


def test_all_references_sharing_prefix_survive() -> None:
    first = "ABCDEF12345678"
    second = "ABCDEF87654321"
    known = {first, second}

    result = recover_reference("ABCDEF", known, _index(second, first))

    assert result.candidates == sorted([first, second])
    assert result.strategy == "prefix"
    assert set(result.scores) == known
