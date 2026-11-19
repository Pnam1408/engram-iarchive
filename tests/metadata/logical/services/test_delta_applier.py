from uuid import uuid4

import pytest

from engram.metadata.logical.models import ChunkRef, VersionDelta
from engram.metadata.logical.services.delta_applier import (
    DeltaApplier,
    InvalidCurrentStateError,
    InvalidDeltaError,
)
from engram.metadata.logical.services.order_key import OrderKeyService

order_keys = OrderKeyService()


def uid() -> str:
    return str(uuid4())


def make_ref(
    chunk_id: str | None = None,
    *,
    chunk_ref_id: str | None = None,
    order_key: str | None = None,
) -> ChunkRef:
    return ChunkRef(
        chunk_ref_id=chunk_ref_id or uid(),
        chunk_id=chunk_id or uid(),
        order_key=order_key or order_keys.generate_between(None, None),
    )


def make_delta(
    *,
    sequence: int = 2,
    added_chunks: tuple[ChunkRef, ...] = (),
    removed_chunk_ref_ids: tuple[str, ...] = (),
) -> VersionDelta:
    return VersionDelta(
        object_id="test-object",
        version_id=uid(),
        sequence=sequence,
        added_chunks=added_chunks,
        removed_chunk_ref_ids=removed_chunk_ref_ids,
        created_at=1_700_000_000_000,
    )


def make_ordered_refs(count: int) -> tuple[ChunkRef, ...]:
    keys = order_keys.generate_n_between(None, None, count)
    return tuple(make_ref(order_key=key) for key in keys)


@pytest.fixture
def applier() -> DeltaApplier:
    return DeltaApplier()


class TestDeltaApplierValidCases:

    def test_apply_add_only(self, applier: DeltaApplier):
        current = make_ordered_refs(2)

        added = make_ref(
            order_key=order_keys.generate_between(
                current[-1].order_key,
                None,
            )
        )

        delta = make_delta(added_chunks=(added,))

        result = applier.apply(current, delta)

        assert result == (*current, added)
        assert len(result) == 3

    def test_apply_remove_only(self, applier: DeltaApplier):
        current = make_ordered_refs(3)
        target = current[1]

        delta = make_delta(removed_chunk_ref_ids=(target.chunk_ref_id,))

        result = applier.apply(current, delta)

        assert result == (current[0], current[2])
        assert target not in result

    def test_apply_add_and_remove(self, applier: DeltaApplier):
        current = make_ordered_refs(3)

        removed = current[1]
        added = make_ref(
            order_key=order_keys.generate_between(
                current[0].order_key,
                current[2].order_key,
            )
        )

        delta = make_delta(
            added_chunks=(added,),
            removed_chunk_ref_ids=(removed.chunk_ref_id,),
        )

        result = applier.apply(current, delta)

        assert result == (current[0], added, current[2])
        assert removed not in result

    def test_apply_empty_delta_preserves_state(
        self,
        applier: DeltaApplier,
    ):
        current = make_ordered_refs(3)
        delta = make_delta()

        result = applier.apply(current, delta)

        assert result == current

    def test_apply_first_version_to_empty_state(
        self,
        applier: DeltaApplier,
    ):
        added = make_ordered_refs(2)

        delta = make_delta(
            sequence=1,
            added_chunks=added,
        )

        result = applier.apply((), delta)

        assert result == added

    def test_result_is_sorted_by_order_key(
        self,
        applier: DeltaApplier,
    ):
        current = make_ordered_refs(2)

        added = make_ref(
            order_key=order_keys.generate_between(
                current[0].order_key,
                current[1].order_key,
            )
        )

        delta = make_delta(added_chunks=(added,))

        result = applier.apply(current, delta)

        assert tuple(ref.order_key for ref in result) == tuple(
            sorted(ref.order_key for ref in result)
        )

    def test_existing_chunk_refs_are_preserved(
        self,
        applier: DeltaApplier,
    ):
        current = make_ordered_refs(3)
        delta = make_delta(removed_chunk_ref_ids=(current[1].chunk_ref_id,))

        result = applier.apply(current, delta)

        # Retained occurrences must preserve their identities and keys.
        assert result[0] is current[0]
        assert result[1] is current[2]


class TestDeltaApplierInvalidDelta:

    def test_remove_unknown_chunk_ref_id(
        self,
        applier: DeltaApplier,
    ):
        current = make_ordered_refs(2)

        delta = make_delta(removed_chunk_ref_ids=(uid(),))

        with pytest.raises(InvalidDeltaError):
            applier.apply(current, delta)

    def test_add_existing_chunk_ref_id(
        self,
        applier: DeltaApplier,
    ):
        existing = make_ordered_refs(1)[0]

        added = make_ref(
            chunk_ref_id=existing.chunk_ref_id,
            order_key=order_keys.generate_between(
                existing.order_key,
                None,
            ),
        )

        delta = make_delta(added_chunks=(added,))

        with pytest.raises(InvalidDeltaError):
            applier.apply((existing,), delta)

    def test_add_duplicate_order_key(
        self,
        applier: DeltaApplier,
    ):
        existing = make_ordered_refs(1)[0]

        added = make_ref(
            order_key=existing.order_key,
        )

        delta = make_delta(added_chunks=(added,))

        with pytest.raises(InvalidDeltaError):
            applier.apply((existing,), delta)

    def test_first_version_rejects_non_empty_current_state(
        self,
        applier: DeltaApplier,
    ):
        current = make_ordered_refs(1)
        delta = make_delta(sequence=1)

        with pytest.raises(InvalidDeltaError):
            applier.apply(current, delta)


class TestDeltaApplierInvalidCurrentState:

    def test_reject_duplicate_chunk_ref_ids(
        self,
        applier: DeltaApplier,
    ):
        ref_id = uid()

        current = (
            make_ref(chunk_ref_id=ref_id),
            make_ref(chunk_ref_id=ref_id),
        )

        delta = make_delta()

        with pytest.raises(InvalidCurrentStateError):
            applier.apply(current, delta)

    def test_reject_duplicate_order_keys(
        self,
        applier: DeltaApplier,
    ):
        key = order_keys.generate_between(None, None)

        current = (
            make_ref(order_key=key),
            make_ref(order_key=key),
        )

        delta = make_delta()

        with pytest.raises(InvalidCurrentStateError):
            applier.apply(current, delta)

    def test_reject_unsorted_current_state(
        self,
        applier: DeltaApplier,
    ):
        keys = order_keys.generate_n_between(None, None, 2)

        current = (
            make_ref(order_key=keys[1]),
            make_ref(order_key=keys[0]),
        )

        delta = make_delta()

        with pytest.raises(InvalidCurrentStateError):
            applier.apply(current, delta)


class TestDeltaApplierInputValidation:

    def test_current_must_be_tuple(
        self,
        applier: DeltaApplier,
    ):
        with pytest.raises(TypeError):
            applier.apply([], make_delta())

    def test_current_must_contain_chunk_refs(
        self,
        applier: DeltaApplier,
    ):
        with pytest.raises(TypeError):
            applier.apply(("invalid",), make_delta())

    def test_delta_must_be_version_delta(
        self,
        applier: DeltaApplier,
    ):
        with pytest.raises(TypeError):
            applier.apply((), object())
