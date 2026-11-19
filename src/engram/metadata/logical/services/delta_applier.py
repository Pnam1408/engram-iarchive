from engram.metadata.logical.models import ChunkRef, VersionDelta


class DeltaApplyError(Exception):
    """Base error for applying a VersionDelta."""


class InvalidCurrentStateError(DeltaApplyError):
    """The current Object Chunk Index is invalid."""


class InvalidDeltaError(DeltaApplyError):
    """The VersionDelta cannot be applied to the current state."""


class DeltaApplier:
    """
    Applies a VersionDelta to the current Object Chunk Index.

    This service is pure:
    - No database access.
    - No physical storage access.
    - No mutation of existing ChunkRef instances.
    """

    def apply(
        self,
        current: tuple[ChunkRef, ...],
        delta: VersionDelta,
    ) -> tuple[ChunkRef, ...]:

        self._validate_input(current, delta)

        current_by_id = {ref.chunk_ref_id: ref for ref in current}

        removed_ids = set(delta.removed_chunk_ref_ids)

        missing_ids = removed_ids - current_by_id.keys()

        if missing_ids:
            raise InvalidDeltaError(
                "Delta removes unknown ChunkRefID(s): " f"{sorted(missing_ids)}"
            )

        retained = [ref for ref in current if ref.chunk_ref_id not in removed_ids]

        retained_ids = {ref.chunk_ref_id for ref in retained}

        retained_order_keys = {ref.order_key for ref in retained}

        for added in delta.added_chunks:
            if added.chunk_ref_id in retained_ids:
                raise InvalidDeltaError(
                    f"ChunkRefID already exists: {added.chunk_ref_id}"
                )

            if added.order_key in retained_order_keys:
                raise InvalidDeltaError(f"OrderKey already exists: {added.order_key}")

            retained_ids.add(added.chunk_ref_id)
            retained_order_keys.add(added.order_key)

        result = retained + list(delta.added_chunks)

        result.sort(key=lambda ref: ref.order_key)

        return tuple(result)

    @staticmethod
    def _validate_input(
        current: tuple[ChunkRef, ...],
        delta: VersionDelta,
    ) -> None:

        if not isinstance(current, tuple):
            raise TypeError("current must be a tuple")

        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")

        if any(not isinstance(ref, ChunkRef) for ref in current):
            raise TypeError("current must contain only ChunkRef instances")

        current_ids = [ref.chunk_ref_id for ref in current]

        current_order_keys = [ref.order_key for ref in current]

        if len(current_ids) != len(set(current_ids)):
            raise InvalidCurrentStateError(
                "Current state contains duplicate ChunkRefID"
            )

        if len(current_order_keys) != len(set(current_order_keys)):
            raise InvalidCurrentStateError("Current state contains duplicate OrderKey")

        if any(
            left >= right
            for left, right in zip(
                current_order_keys,
                current_order_keys[1:],
            )
        ):
            raise InvalidCurrentStateError("Current state is not ordered by OrderKey")

        if delta.sequence == 1 and current:
            raise InvalidDeltaError("The first Version must start with an empty state")
