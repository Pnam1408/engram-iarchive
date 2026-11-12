"""Benchmark-only instrumentation for IncrementalStore."""

from __future__ import annotations

from typing import Any

from benchmarks.common.timing import measure_ms
from engram.incremental.store import IncrementalStore

STAGE_NAMES = (
    "encrypt",
    "encode",
    "commit",
    "write",
    "verify",
    "publish",
)


class TimedIncrementalStore(IncrementalStore):
    """IncrementalStore that records stage timings for benchmarks only."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stage_timings: dict[str, float] = {}

    def reset_stage_timings(self) -> None:
        self.stage_timings = {}

    def _record(self, name: str, function: Any, *args: Any) -> Any:
        result, elapsed_ms = measure_ms(function, *args)
        self.stage_timings[name] = self.stage_timings.get(name, 0.0) + elapsed_ms
        return result

    def _encrypt_generation(self, *args: Any, **kwargs: Any) -> None:
        self._record(
            "encrypt",
            super()._encrypt_generation,
            *args,
            **kwargs,
        )

    def _encode_generation(self, *args: Any, **kwargs: Any) -> Any:
        return self._record(
            "encode",
            super()._encode_generation,
            *args,
            **kwargs,
        )

    def _commit_generation(self, *args: Any, **kwargs: Any) -> Any:
        return self._record(
            "commit",
            super()._commit_generation,
            *args,
            **kwargs,
        )

    def _write_generation(self, *args: Any, **kwargs: Any) -> None:
        self._record(
            "write",
            super()._write_generation,
            *args,
            **kwargs,
        )

    def _verify_generation(self, *args: Any, **kwargs: Any) -> bytes:
        return self._record(
            "verify",
            super()._verify_generation,
            *args,
            **kwargs,
        )

    def _publish_generation_state(self, *args: Any, **kwargs: Any) -> None:
        self._record(
            "publish",
            super()._publish_generation_state,
            *args,
            **kwargs,
        )
