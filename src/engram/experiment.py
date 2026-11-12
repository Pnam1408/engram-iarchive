"""Backward-compatible entry point for the baseline pipeline benchmark."""

from benchmarks.scenarios.baseline_pipeline import main, one_run

__all__ = ["main", "one_run"]


if __name__ == "__main__":
    main()
