from __future__ import annotations

import hashlib
import sys
import types
import unittest
from unittest.mock import patch

from engram.chunking import FastCDCChunker, FastCDCConfig, FixedSizeChunker


class FixedSizeChunkerTests(unittest.TestCase):
    def test_splits_data_into_fixed_size_chunks(self) -> None:
        data = b"abcdefghij"
        chunks = FixedSizeChunker(chunk_size=4).chunk(data)

        self.assertEqual([chunk.data for chunk in chunks], [b"abcd", b"efgh", b"ij"])
        self.assertEqual([(chunk.offset, chunk.size) for chunk in chunks], [(0, 4), (4, 4), (8, 2)])
        self.assertEqual([chunk.index for chunk in chunks], [0, 1, 2])
        self.assertEqual(chunks[0].sha256, hashlib.sha256(b"abcd").hexdigest())
        self.assertEqual(b"".join(chunk.data for chunk in chunks), data)

    def test_empty_input_returns_no_chunks(self) -> None:
        self.assertEqual(FixedSizeChunker(4).chunk(b""), ())

    def test_rejects_invalid_chunk_size(self) -> None:
        for size in (0, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                FixedSizeChunker(size)

    def test_rejects_non_bytes_input(self) -> None:
        with self.assertRaises(TypeError):
            FixedSizeChunker(4).chunk("not bytes")  # type: ignore[arg-type]


class FastCDCConfigTests(unittest.TestCase):
    def test_default_configuration(self) -> None:
        config = FastCDCConfig()
        self.assertEqual((config.min_size, config.avg_size, config.max_size), (2048, 4096, 8192))

    def test_rejects_invalid_configuration(self) -> None:
        invalid = (
            {"min_size": 32, "avg_size": 64, "max_size": 128},
            {"min_size": 128, "avg_size": 256, "max_size": 128},
            {"min_size": 128, "avg_size": 300, "max_size": 512},
            {"min_size": 128, "avg_size": 256, "max_size": 0x1_0000_0000},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FastCDCConfig(**kwargs)


class FastCDCChunkerTests(unittest.TestCase):
    def test_empty_input_does_not_require_native_extension(self) -> None:
        self.assertEqual(FastCDCChunker().chunk(b""), ())

    def test_requires_native_extension_without_silent_fallback(self) -> None:
        with patch.dict(sys.modules, {"engram.chunking._fastcdc": None}):
            with self.assertRaisesRegex(RuntimeError, "no fallback implementation"):
                FastCDCChunker().chunk(b"payload")

    def test_wraps_native_boundaries_and_builds_hashes(self) -> None:
        native = types.SimpleNamespace(
            chunk_boundaries=lambda data, min_size, avg_size, max_size: [(0, 3), (3, 5)]
        )
        with patch.dict(sys.modules, {"engram.chunking._fastcdc": native}):
            chunks = FastCDCChunker().chunk(b"abcdefgh")

        self.assertEqual([chunk.data for chunk in chunks], [b"abc", b"defgh"])
        self.assertEqual([chunk.sha256 for chunk in chunks], [
            hashlib.sha256(b"abc").hexdigest(),
            hashlib.sha256(b"defgh").hexdigest(),
        ])

    def test_rejects_invalid_native_boundaries(self) -> None:
        native = types.SimpleNamespace(
            chunk_boundaries=lambda *_args: [(0, 3), (4, 4)]
        )
        with patch.dict(sys.modules, {"engram.chunking._fastcdc": native}):
            with self.assertRaisesRegex(ValueError, "contiguous"):
                FastCDCChunker().chunk(b"abcdefgh")


if __name__ == "__main__":
    unittest.main()
