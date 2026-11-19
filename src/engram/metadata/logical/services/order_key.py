from fractional_indexing import (
    generate_key_between,
    generate_n_keys_between,
    validate_order_key as _validate_order_key,
)


class OrderKeyGenerationError(Exception):
    pass


class OrderKeyService:

    @staticmethod
    def _validate(key: str | None) -> None:
        if key is None:
            return

        try:
            result = _validate_order_key(key)
        except Exception as exc:
            raise ValueError(f"Invalid OrderKey: {key!r}") from exc

        if result is False:
            raise ValueError(f"Invalid OrderKey: {key!r}")

    def generate_between(
        self,
        before: str | None,
        after: str | None,
    ) -> str:
        self._validate(before)
        self._validate(after)

        if before is not None and after is not None:
            if before >= after:
                raise ValueError("before must be lexicographically smaller than after")

        try:
            key = generate_key_between(before, after)
        except Exception as exc:
            raise OrderKeyGenerationError("Failed to generate OrderKey") from exc

        self._validate(key)

        if before is not None and not before < key:
            raise OrderKeyGenerationError(
                "Generated OrderKey is not greater than before"
            )

        if after is not None and not key < after:
            raise OrderKeyGenerationError(
                "Generated OrderKey is not smaller than after"
            )

        return key

    def generate_n_between(
        self,
        before: str | None,
        after: str | None,
        count: int,
    ) -> tuple[str, ...]:
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an integer")

        if count < 0:
            raise ValueError("count must be non-negative")

        if count == 0:
            return ()

        self._validate(before)
        self._validate(after)

        if before is not None and after is not None:
            if before >= after:
                raise ValueError("before must be lexicographically smaller than after")

        try:
            keys = tuple(generate_n_keys_between(before, after, count))
        except Exception as exc:
            raise OrderKeyGenerationError(
                "Failed to generate multiple OrderKeys"
            ) from exc

        if len(keys) != count:
            raise OrderKeyGenerationError(
                "Generated OrderKey count does not match requested count"
            )

        for key in keys:
            self._validate(key)

        if any(left >= right for left, right in zip(keys, keys[1:])):
            raise OrderKeyGenerationError(
                "Generated OrderKeys are not strictly increasing"
            )

        if before is not None and keys[0] <= before:
            raise OrderKeyGenerationError(
                "Generated OrderKeys are not greater than before"
            )

        if after is not None and keys[-1] >= after:
            raise OrderKeyGenerationError(
                "Generated OrderKeys are not smaller than after"
            )

        return keys
