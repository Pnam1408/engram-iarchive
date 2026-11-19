from uuid import uuid4

import pytest

from engram.metadata.logical.models import ChunkRef
from engram.metadata.logical.services.sequence_diff_planner import (
    SequenceDiffPlanner,
    InvalidSequenceError,
    SequenceDiffTooLargeError,
)


def uid() -> str:
    return str(uuid4())


def make_ref(chunk_id: str, order_key: str) -> ChunkRef:
    return ChunkRef(
        chunk_ref_id=uid(),
        chunk_id=chunk_id,
        order_key=order_key,
    )


def make_planner():
    return SequenceDiffPlanner()


def plan(
    current: tuple[ChunkRef, ...],
    new_chunk_ids: tuple[str, ...],
):
    return make_planner().plan(
        current,
        new_chunk_ids,
        object_id="object-001",
        version_id=uid(),
        sequence=2 if current else 1,
        created_at=2000,
    )


def test_identical_sequences_produce_empty_delta():
    a, b = uid(), uid()

    current = (
        make_ref(a, "a0"),
        make_ref(b, "a1"),
    )

    delta = plan(current, (a, b))

    assert delta.added_chunks == ()
    assert delta.removed_chunk_ref_ids == ()


def test_insert_chunk_between_existing_chunks():
    a, b, x = uid(), uid(), uid()

    ref_a = make_ref(a, "a0")
    ref_b = make_ref(b, "a2")

    delta = plan(
        (ref_a, ref_b),
        (a, x, b),
    )

    assert len(delta.added_chunks) == 1
    assert delta.added_chunks[0].chunk_id == x
    assert delta.removed_chunk_ref_ids == ()

    assert ref_a.chunk_ref_id not in delta.removed_chunk_ref_ids
    assert ref_b.chunk_ref_id not in delta.removed_chunk_ref_ids


def test_remove_chunk():
    a, b, c = uid(), uid(), uid()

    ref_a = make_ref(a, "a0")
    ref_b = make_ref(b, "a1")
    ref_c = make_ref(c, "a2")

    delta = plan(
        (ref_a, ref_b, ref_c),
        (a, c),
    )

    assert delta.added_chunks == ()
    assert delta.removed_chunk_ref_ids == (ref_b.chunk_ref_id,)


def test_replace_chunk():
    a, b, x = uid(), uid(), uid()

    ref_a = make_ref(a, "a0")
    ref_b = make_ref(b, "a1")

    delta = plan(
        (ref_a, ref_b),
        (a, x),
    )

    assert len(delta.added_chunks) == 1
    assert delta.added_chunks[0].chunk_id == x

    assert delta.removed_chunk_ref_ids == (ref_b.chunk_ref_id,)


def test_duplicate_chunk_occurrences_are_preserved():
    a, b, c = uid(), uid(), uid()

    ref_a1 = make_ref(a, "a0")
    ref_b = make_ref(b, "a1")
    ref_a2 = make_ref(a, "a2")
    ref_c = make_ref(c, "a3")

    delta = plan(
        (ref_a1, ref_b, ref_a2, ref_c),
        (a, a, b, c),
    )

    # Tie-breaking giữ occurrence A đầu tiên.
    assert ref_a1.chunk_ref_id not in delta.removed_chunk_ref_ids

    # Occurrence A thứ hai bị loại bỏ.
    assert ref_a2.chunk_ref_id in delta.removed_chunk_ref_ids

    assert len(delta.added_chunks) == 1
    assert delta.added_chunks[0].chunk_id == a


def test_empty_new_sequence_removes_all_refs():
    a, b = uid(), uid()

    ref_a = make_ref(a, "a0")
    ref_b = make_ref(b, "a1")

    delta = plan(
        (ref_a, ref_b),
        (),
    )

    assert delta.added_chunks == ()
    assert delta.removed_chunk_ref_ids == (
        ref_a.chunk_ref_id,
        ref_b.chunk_ref_id,
    )


def test_empty_old_sequence_adds_all_chunks():
    a, b = uid(), uid()

    delta = plan((), (a, b))

    assert len(delta.added_chunks) == 2
    assert delta.removed_chunk_ref_ids == ()


def test_reject_unsorted_current_refs():
    a, b = uid(), uid()

    current = (
        make_ref(a, "a1"),
        make_ref(b, "a0"),
    )

    with pytest.raises(InvalidSequenceError):
        plan(current, (a, b))


def test_reject_non_empty_current_for_first_version():
    a = uid()
    current = (make_ref(a, "a0"),)

    with pytest.raises(InvalidSequenceError):
        make_planner().plan(
            current,
            (a,),
            object_id="object-001",
            version_id=uid(),
            sequence=1,
            created_at=1000,
        )


def test_reject_excessive_dp_size():
    planner = SequenceDiffPlanner(max_dp_cells=4)

    a = uid()
    current = (
        make_ref(a, "a0"),
        make_ref(uid(), "a1"),
    )

    with pytest.raises(SequenceDiffTooLargeError):
        planner.plan(
            current,
            (a, uid()),
            object_id="object-001",
            version_id=uid(),
            sequence=2,
            created_at=2000,
        )


def test_lcs_tie_break_prefers_earlier_old_occurrence():
    a, b = uid(), uid()

    ref_a = make_ref(a, "a0")
    ref_b = make_ref(b, "a1")

    delta = plan(
        (ref_a, ref_b),
        (b, a),
    )

    assert delta.removed_chunk_ref_ids == (ref_b.chunk_ref_id,)

    assert len(delta.added_chunks) == 1
    added_ref = delta.added_chunks[0]

    assert added_ref.chunk_id == b
    assert added_ref.chunk_ref_id != ref_b.chunk_ref_id
    assert added_ref.order_key < ref_a.order_key
