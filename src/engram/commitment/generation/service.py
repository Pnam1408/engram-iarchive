"""Application-facing operations for Generation Manifest commitments."""

from engram.metadata.physical.models import (
    Generation,
    GenerationManifest,
    GenerationManifestEntry,
)

from .canonicalizer import GenerationCanonicalizer


class GenerationCommitmentService:
    def __init__(self, canonicalizer: GenerationCanonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or GenerationCanonicalizer()

    def compute_manifest_root(
        self,
        *,
        generation: Generation,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
    ) -> bytes:
        return self._canonicalizer.hash(
            generation=generation, manifest=manifest, entries=entries
        )

    def verify_manifest_root(
        self,
        *,
        generation: Generation,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
        expected_root: bytes,
    ) -> bool:
        if type(expected_root) is not bytes or len(expected_root) != 32:
            raise ValueError("expected_root must be exactly 32 bytes")
        actual = self.compute_manifest_root(
            generation=generation, manifest=manifest, entries=entries
        )
        return actual == expected_root

    def verify_manifest(
        self,
        *,
        generation: Generation,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
    ) -> bool:
        """Verify supplied Generation and entries against the stored manifest root."""
        return self.verify_manifest_root(
            generation=generation,
            manifest=manifest,
            entries=entries,
            expected_root=manifest.manifest_root,
        )
