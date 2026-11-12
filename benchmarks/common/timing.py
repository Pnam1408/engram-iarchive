# benchmarks/common/timing.py
"""Shared timing utilities for ENGRAM benchmarks."""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")


def measure_ms(func: Callable[..., T], /, *args, **kwargs) -> tuple[T, float]:
    """Run func and return (result, elapsed_ms).

    If func raises an exception, the exception propagates.
    """
    start = perf_counter()
    result = func(*args, **kwargs)
    elapsed_ms = (perf_counter() - start) * 1000
    return result, elapsed_ms


@contextmanager
def Timer() -> Iterator[dict[str, float]]:
    """Measure a code block and expose elapsed_ms after the block.

    Example:
        with Timer() as timer:
            do_work()
        print(timer["elapsed_ms"])
    """
    result: dict[str, float] = {}
    start = perf_counter()
    try:
        yield result
    finally:
        result["elapsed_ms"] = (perf_counter() - start) * 1000
