from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import GenerationManifest, GenerationManifestEntry, GenerationStatus

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import (
        GenerationManifestEntryRepository,
        GenerationManifestRepository,
        GenerationRepository,
    )


class GenerationManifestServiceError(Exception):
    """Base error for Generation Manifest operations."""


class GenerationManifestGenerationNotFoundError(GenerationManifestServiceError):
    """The target Generation does not exist."""


class GenerationManifestAlreadyRegisteredError(GenerationManifestServiceError):
    """A Manifest or Manifest entries have already been registered."""


class GenerationManifestPreconditionError(GenerationManifestServiceError):
    """The Generation is not in a state that permits Manifest registration."""


class GenerationManifestNotFoundError(GenerationManifestServiceError):
    """The Generation has no registered Manifest."""


class GenerationManifestIntegrityError(GenerationManifestServiceError):
    """Persisted Manifest metadata is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class GenerationManifestBundle:
    manifest: GenerationManifest
    entries: tuple[GenerationManifestEntry, ...]


class GenerationManifestService:
    """Manage immutable Generation Manifest metadata only.

    The service does not create entries, calculate record hashes or manifest
    roots, inspect payload bytes, or access Logical Metadata / Processing.
    Writes are staged into the caller-owned batch and are never committed here.
    """

    def __init__(
        self,
        generation_repository: GenerationRepository,
        manifest_repository: GenerationManifestRepository,
        manifest_entry_repository: GenerationManifestEntryRepository,
    ) -> None:
        for name, repository in (
            ("generation_repository", generation_repository),
            ("manifest_repository", manifest_repository),
            ("manifest_entry_repository", manifest_entry_repository),
        ):
            if repository is None:
                raise TypeError(f"{name} is required")

        self._generations = generation_repository
        self._manifests = manifest_repository
        self._entries = manifest_entry_repository

    def register(
        self,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
        *,
        batch: MetadataBatch,
    ) -> GenerationManifestBundle:
        """Stage a complete Manifest and its entries for an OPEN Generation."""
        if not isinstance(manifest, GenerationManifest):
            raise TypeError("manifest must be a GenerationManifest")
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, GenerationManifestEntry) for entry in entries
        ):
            raise TypeError("entries must be a tuple of GenerationManifestEntry")
        if batch is None:
            raise TypeError("batch is required")

        generation_id = manifest.generation_id
        generation = self._generations.get(generation_id)
        if generation is None:
            raise GenerationManifestGenerationNotFoundError(generation_id)
        if generation.status is not GenerationStatus.OPEN:
            raise GenerationManifestPreconditionError(
                "Manifest can only be registered while Generation is OPEN"
            )

        # Validate all caller-supplied data before staging any writes.
        try:
            ordered_entries = manifest.validate_entries(entries)
        except (TypeError, ValueError) as exc:
            raise GenerationManifestPreconditionError(
                "Manifest entries are invalid or incomplete"
            ) from exc

        if self._manifests.get(generation_id) is not None:
            raise GenerationManifestAlreadyRegisteredError(
                f"Manifest for Generation {generation_id!r} already exists"
            )
        existing_entries = self._entries.list_for_generation(generation_id)
        if existing_entries:
            raise GenerationManifestAlreadyRegisteredError(
                f"Manifest entries for Generation {generation_id!r} already exist"
            )

        # Repositories stage into the same caller-owned batch. If a repository
        # raises after staging has begun, caller must discard the batch.
        self._manifests.put(manifest, batch)
        self._entries.put_all(manifest, ordered_entries, batch)
        return GenerationManifestBundle(manifest, ordered_entries)

    def get_manifest(self, generation_id: str) -> GenerationManifest | None:
        return self._manifests.get(generation_id)

    def list_entries(self, generation_id: str) -> tuple[GenerationManifestEntry, ...]:
        return self._entries.list_for_generation(generation_id)

    def get_bundle(self, generation_id: str) -> GenerationManifestBundle | None:
        manifest = self._manifests.get(generation_id)
        entries = self._entries.list_for_generation(generation_id)
        if manifest is None:
            if entries:
                raise GenerationManifestIntegrityError(
                    "Manifest entries exist without a Generation Manifest"
                )
            return None
        try:
            ordered = manifest.validate_entries(entries)
        except (TypeError, ValueError) as exc:
            raise GenerationManifestIntegrityError(
                "Stored Generation Manifest and entries are inconsistent"
            ) from exc
        return GenerationManifestBundle(manifest, ordered)

    def validate(self, generation_id: str) -> bool:
        """Validate persisted structural invariants; does not recompute root."""
        bundle = self.get_bundle(generation_id)
        if bundle is None:
            raise GenerationManifestNotFoundError(generation_id)
        return True
