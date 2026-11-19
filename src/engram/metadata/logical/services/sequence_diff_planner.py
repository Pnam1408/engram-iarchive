from array import array
from collections.abc import Callable
from uuid import uuid4

from engram.metadata.logical.models import (
    ChunkRef,
    VersionDelta,
)
from engram.metadata.logical.models._validation import (
    validate_uuid,
    validate_non_empty_string,
    validate_positive_integer,
    validate_timestamp,
)

from .order_key import OrderKeyService


class SequenceDiffError(Exception):
    pass


class InvalidSequenceError(SequenceDiffError):
    pass


class SequenceDiffTooLargeError(SequenceDiffError):
    pass


class SequenceDiffPlanner:
    """
    Builds a VersionDelta by matching old and new ChunkID sequences
    using Longest Common Subsequence (LCS).

    Tie-breaking:
    - Match immediately when equal and the match preserves optimal LCS.
    - Otherwise skip the old element if doing so gives a longer LCS.
    - On equal alternatives, skip the new element.

    This prefers earlier old occurrences, then earlier new positions.
    """

    DEFAULT_MAX_DP_CELLS = 16_000_000

    def __init__(
        self,
        order_key_service: OrderKeyService | None = None,
        id_factory: Callable[[], str] | None = None,
        *,
        max_dp_cells: int = DEFAULT_MAX_DP_CELLS,
    ) -> None:
        if (
            isinstance(max_dp_cells, bool)
            or not isinstance(max_dp_cells, int)
            or max_dp_cells <= 0
        ):
            raise ValueError("max_dp_cells must be a positive integer")

        self._order_keys = order_key_service or OrderKeyService()
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._max_dp_cells = max_dp_cells

    def plan(
        self,
        current: tuple[ChunkRef, ...],
        new_chunk_ids: tuple[str, ...],
        *,
        object_id: str,
        version_id: str,
        sequence: int,
        created_at: int,
    ) -> VersionDelta:
        self._validate_input(
            current,
            new_chunk_ids,
            object_id=object_id,
            version_id=version_id,
            sequence=sequence,
            created_at=created_at,
        )

        old_chunk_ids = tuple(ref.chunk_id for ref in current)

        matches = self._find_lcs_matches(
            old_chunk_ids,
            new_chunk_ids,
        )

        matched_old_indices = {old_index for old_index, _ in matches}

        matched_new_to_old = {new_index: old_index for old_index, new_index in matches}

        new_chunk_refs = self._build_new_chunk_refs(
            current,
            new_chunk_ids,
            matched_new_to_old,
        )

        self._validate_result(
            sequence,
            current,
            new_chunk_refs,
            matched_new_to_old,
        )

        added_chunks = tuple(
            ref
            for ref in new_chunk_refs
            if ref.chunk_ref_id not in {old_ref.chunk_ref_id for old_ref in current}
        )

        removed_ids = tuple(
            ref.chunk_ref_id
            for index, ref in enumerate(current)
            if index not in matched_old_indices
        )

        return VersionDelta(
            object_id=object_id,
            version_id=version_id,
            sequence=sequence,
            added_chunks=added_chunks,
            removed_chunk_ref_ids=removed_ids,
            created_at=created_at,
        )

    def _validate_input(
        self,
        current: tuple[ChunkRef, ...],
        new_chunk_ids: tuple[str, ...],
        *,
        object_id: str,
        version_id: str,
        sequence: int,
        created_at: int,
    ) -> None:
        if not isinstance(current, tuple):
            raise TypeError("current must be a tuple")

        if not isinstance(new_chunk_ids, tuple):
            raise TypeError("new_chunk_ids must be a tuple")

        validate_non_empty_string(object_id, "object_id")
        validate_uuid(version_id, "version_id")
        validate_positive_integer(sequence, "sequence")
        validate_timestamp(created_at, "created_at")

        for ref in current:
            if not isinstance(ref, ChunkRef):
                raise TypeError("current must contain only ChunkRef instances")

        for chunk_id in new_chunk_ids:
            validate_uuid(chunk_id, "chunk_id")

        old_ref_ids = [ref.chunk_ref_id for ref in current]
        old_order_keys = [ref.order_key for ref in current]

        if len(old_ref_ids) != len(set(old_ref_ids)):
            raise InvalidSequenceError("Current sequence contains duplicate ChunkRefID")

        if len(old_order_keys) != len(set(old_order_keys)):
            raise InvalidSequenceError("Current sequence contains duplicate OrderKey")

        if any(
            left >= right
            for left, right in zip(
                old_order_keys,
                old_order_keys[1:],
            )
        ):
            raise InvalidSequenceError("Current ChunkRefs must be ordered by OrderKey")

    def _find_lcs_matches(
        self,
        old: tuple[str, ...],
        new: tuple[str, ...],
    ) -> tuple[tuple[int, int], ...]:
        n = len(old)
        m = len(new)

        if n == 0 or m == 0:
            return ()

        cells = (n + 1) * (m + 1)

        if cells > self._max_dp_cells:
            raise SequenceDiffTooLargeError(
                f"LCS requires {cells} DP cells; "
                f"configured limit is {self._max_dp_cells}"
            )

        # Compact unsigned integer rows reduce DP memory consumption.
        dp = [array("I", [0]) * (m + 1) for _ in range(n + 1)]

        for i in range(n - 1, -1, -1):
            row = dp[i]
            next_row = dp[i + 1]

            for j in range(m - 1, -1, -1):
                if old[i] == new[j]:
                    row[j] = 1 + next_row[j + 1]
                else:
                    skip_old = next_row[j]
                    skip_new = row[j + 1]

                    row[j] = max(skip_old, skip_new)

        matches: list[tuple[int, int]] = []

        i = 0
        j = 0

        while i < n and j < m:
            if old[i] == new[j] and dp[i][j] == 1 + dp[i + 1][j + 1]:
                matches.append((i, j))
                i += 1
                j += 1
                continue

            skip_old = dp[i + 1][j]
            skip_new = dp[i][j + 1]

            if skip_old > skip_new:
                i += 1
            else:
                # Deterministic tie-break: skip new on equality.
                j += 1

        return tuple(matches)

    def _build_new_chunk_refs(
        self,
        current: tuple[ChunkRef, ...],
        new_chunk_ids: tuple[str, ...],
        matched_new_to_old: dict[int, int],
    ) -> tuple[ChunkRef, ...]:
        result: list[ChunkRef] = []

        j = 0
        new_count = len(new_chunk_ids)

        while j < new_count:
            old_index = matched_new_to_old.get(j)

            if old_index is not None:
                result.append(current[old_index])
                j += 1
                continue

            # Find a contiguous run of unmatched new occurrences.
            run_start = j

            while j < new_count and j not in matched_new_to_old:
                j += 1

            run_end = j
            count = run_end - run_start

            before = None
            after = None

            if run_start > 0:
                previous_old_index = matched_new_to_old.get(run_start - 1)
                if previous_old_index is None:
                    raise InvalidSequenceError("Missing previous LCS anchor")

                before = current[previous_old_index].order_key

            if run_end < new_count:
                next_old_index = matched_new_to_old.get(run_end)
                if next_old_index is None:
                    raise InvalidSequenceError("Missing next LCS anchor")

                after = current[next_old_index].order_key

            order_keys = self._order_keys.generate_n_between(
                before,
                after,
                count,
            )

            for offset, order_key in enumerate(order_keys):
                new_index = run_start + offset

                result.append(
                    ChunkRef(
                        chunk_ref_id=self._id_factory(),
                        chunk_id=new_chunk_ids[new_index],
                        order_key=order_key,
                    )
                )

        return tuple(result)

    def _validate_result(
        self,
        sequence: int,
        current: tuple[ChunkRef, ...],
        refs: tuple[ChunkRef, ...],
        matched_new_to_old: dict[int, int],
    ) -> None:
        retained_ids = {
            current[old_index].chunk_ref_id for old_index in matched_new_to_old.values()
        }

        generated_ids = {
            refs[new_index].chunk_ref_id
            for new_index in range(len(refs))
            if new_index not in matched_new_to_old
        }

        if sequence == 1 and current:
            raise InvalidSequenceError(
                "The first Version must start with an empty ChunkRef sequence"
            )

        if retained_ids & generated_ids:
            raise InvalidSequenceError(
                "Generated ChunkRefID collides with a retained ChunkRefID"
            )

        all_ids = [ref.chunk_ref_id for ref in refs]
        if len(all_ids) != len(set(all_ids)):
            raise InvalidSequenceError(
                "Generated sequence contains duplicate ChunkRefID"
            )

        order_keys = [ref.order_key for ref in refs]
        if len(order_keys) != len(set(order_keys)):
            raise InvalidSequenceError("Generated sequence contains duplicate OrderKey")

        if any(a >= b for a, b in zip(order_keys, order_keys[1:])):
            raise InvalidSequenceError("Generated sequence is not ordered by OrderKey")
