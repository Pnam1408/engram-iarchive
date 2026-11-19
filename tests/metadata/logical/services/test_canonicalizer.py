import hashlib
import struct
from uuid import uuid4

import pytest

from engram.metadata.logical.models import (
    Checkpoint,
    CheckpointEntry,
    ChunkRef,
    Version,
    VersionDelta,
)
from engram.metadata.logical.services.canonicalizer import (
    CanonicalizationError,
    Canonicalizer,
)
from engram.metadata.logical.services.order_key import OrderKeyService

order_keys = OrderKeyService()
TIMESTAMP = 1_700_000_000_000


def uid() -> str:
    return str(uuid4())


def make_ref(
    *,
    chunk_ref_id: str | None = None,
    chunk_id: str | None = None,
    order_key: str | None = None,
) -> ChunkRef:
    return ChunkRef(
        chunk_ref_id=chunk_ref_id or uid(),
        chunk_id=chunk_id or uid(),
        order_key=order_key or order_keys.generate_between(None, None),
    )


def make_delta(
    *,
    object_id: str = "object-1",
    version_id: str | None = None,
    sequence: int = 1,
    added_chunks: tuple[ChunkRef, ...] = (),
    removed_chunk_ref_ids: tuple[str, ...] = (),
    created_at: int = TIMESTAMP,
) -> VersionDelta:
    return VersionDelta(
        object_id=object_id,
        version_id=version_id or uid(),
        sequence=sequence,
        added_chunks=added_chunks,
        removed_chunk_ref_ids=removed_chunk_ref_ids,
        created_at=created_at,
    )


def make_version(
    *,
    version_id: str | None = None,
    object_id: str = "object-1",
    sequence: int = 1,
    parent_version_id: str | None = None,
    delta_root: bytes | None = None,
    previous_version_hash: bytes | None = None,
    codec: str = "engram-v1",
    writer_public_key: bytes = b"public-key",
    signature: bytes = b"signature",
    created_at: int = TIMESTAMP,
) -> Version:
    return Version(
        version_id=version_id or uid(),
        object_id=object_id,
        sequence=sequence,
        parent_version_id=parent_version_id,
        delta_root=delta_root or b"\x11" * 32,
        previous_version_hash=previous_version_hash,
        codec=codec,
        writer_public_key=writer_public_key,
        signature=signature,
        created_at=created_at,
    )


def make_checkpoint_entry(
    checkpoint_id: str,
    *,
    chunk_ref_id: str | None = None,
    chunk_id: str | None = None,
    order_key: str | None = None,
) -> CheckpointEntry:
    return CheckpointEntry(
        checkpoint_id=checkpoint_id,
        chunk_ref_id=chunk_ref_id or uid(),
        chunk_id=chunk_id or uid(),
        order_key=order_key or order_keys.generate_between(None, None),
    )


def make_checkpoint(
    *,
    checkpoint_id: str | None = None,
    object_id: str = "object-1",
    sequence: int = 1,
    version_id: str | None = None,
    entry_count: int = 0,
    snapshot_root: bytes = b"\x22" * 32,
) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id or uid(),
        object_id=object_id,
        sequence=sequence,
        version_id=version_id or uid(),
        entry_count=entry_count,
        snapshot_root=snapshot_root,
        created_at=TIMESTAMP,
    )


# ============================================================
# Primitive encoding
# ============================================================


class TestPrimitiveEncoding:

    def test_encode_u32_uses_big_endian(self):
        assert Canonicalizer._encode_u32(0x01020304) == b"\x01\x02\x03\x04"

    def test_encode_u64_uses_big_endian(self):
        assert Canonicalizer._encode_u64(1) == b"\x00" * 7 + b"\x01"

    def test_encode_bytes_uses_u32_length_prefix(self):
        assert Canonicalizer._encode_bytes(b"abc") == (b"\x00\x00\x00\x03abc")

    def test_encode_string_uses_utf8(self):
        value = "ENGRAM"
        expected = struct.pack(">I", len(value.encode("utf-8"))) + b"ENGRAM"

        assert Canonicalizer._encode_string(value) == expected

    def test_encode_uuid_uses_16_binary_bytes(self):
        value = uid()

        encoded = Canonicalizer._encode_uuid(value)

        assert len(encoded) == 16
        assert encoded == __import__("uuid").UUID(value).bytes

    @pytest.mark.parametrize("value", [-1, 1 << 32])
    def test_encode_u32_rejects_out_of_range(self, value):
        with pytest.raises(ValueError):
            Canonicalizer._encode_u32(value)

    @pytest.mark.parametrize("value", [-1, 1 << 64])
    def test_encode_u64_rejects_out_of_range(self, value):
        with pytest.raises(ValueError):
            Canonicalizer._encode_u64(value)

    @pytest.mark.parametrize("value", [True, False, 1.5, "1"])
    def test_encode_u64_rejects_non_integer(self, value):
        with pytest.raises(TypeError):
            Canonicalizer._encode_u64(value)

    def test_encode_bytes_rejects_non_bytes(self):
        with pytest.raises(TypeError):
            Canonicalizer._encode_bytes("abc")

    def test_encode_string_rejects_non_string(self):
        with pytest.raises(TypeError):
            Canonicalizer._encode_string(b"abc")

    def test_encode_uuid_rejects_invalid_uuid(self):
        with pytest.raises(ValueError):
            Canonicalizer._encode_uuid("not-a-uuid")


# ============================================================
# ChunkRef
# ============================================================


class TestChunkRefCanonicalization:

    def test_same_chunk_ref_produces_same_bytes(self):
        ref = make_ref()

        first = Canonicalizer.encode_chunk_ref(ref)
        second = Canonicalizer.encode_chunk_ref(ref)

        assert first == second

    def test_chunk_ref_encoding_contains_domain_tag(self):
        ref = make_ref()

        encoded = Canonicalizer.encode_chunk_ref(ref)

        assert encoded.startswith(Canonicalizer.DOMAIN_CHUNK_REF)

    def test_different_chunk_ref_id_changes_encoding(self):
        ref = make_ref()

        changed = ChunkRef(
            chunk_ref_id=uid(),
            chunk_id=ref.chunk_id,
            order_key=ref.order_key,
        )

        assert Canonicalizer.encode_chunk_ref(ref) != Canonicalizer.encode_chunk_ref(
            changed
        )

    def test_different_chunk_id_changes_encoding(self):
        ref = make_ref()

        changed = ChunkRef(
            chunk_ref_id=ref.chunk_ref_id,
            chunk_id=uid(),
            order_key=ref.order_key,
        )

        assert Canonicalizer.encode_chunk_ref(ref) != Canonicalizer.encode_chunk_ref(
            changed
        )

    def test_different_order_key_changes_encoding(self):
        keys = order_keys.generate_n_between(None, None, 2)

        ref = make_ref(order_key=keys[0])
        changed = ChunkRef(
            chunk_ref_id=ref.chunk_ref_id,
            chunk_id=ref.chunk_id,
            order_key=keys[1],
        )

        assert Canonicalizer.encode_chunk_ref(ref) != Canonicalizer.encode_chunk_ref(
            changed
        )


# ============================================================
# VersionDelta
# ============================================================


class TestDeltaCanonicalization:

    def test_same_delta_produces_same_bytes(self):
        delta = make_delta(added_chunks=(make_ref(),))

        assert Canonicalizer.encode_delta(delta) == Canonicalizer.encode_delta(delta)

    def test_delta_contains_domain_tag(self):
        delta = make_delta()

        assert Canonicalizer.encode_delta(delta).startswith(Canonicalizer.DOMAIN_DELTA)

    def test_added_chunks_order_does_not_affect_encoding(self):
        keys = order_keys.generate_n_between(None, None, 2)

        ref_a = make_ref(order_key=keys[0])
        ref_b = make_ref(order_key=keys[1])

        version_id = uid()

        delta_a = make_delta(
            version_id=version_id,
            sequence=2,
            added_chunks=(ref_a, ref_b),
        )
        delta_b = make_delta(
            version_id=version_id,
            sequence=2,
            added_chunks=(ref_b, ref_a),
        )

        assert Canonicalizer.encode_delta(delta_a) == Canonicalizer.encode_delta(
            delta_b
        )

    def test_removed_ids_order_does_not_affect_encoding(self):
        id_a = uid()
        id_b = uid()
        version_id = uid()

        delta_a = make_delta(
            version_id=version_id,
            sequence=2,
            removed_chunk_ref_ids=(id_a, id_b),
        )
        delta_b = make_delta(
            version_id=version_id,
            sequence=2,
            removed_chunk_ref_ids=(id_b, id_a),
        )

        assert Canonicalizer.encode_delta(delta_a) == Canonicalizer.encode_delta(
            delta_b
        )

    def test_changed_object_id_changes_encoding(self):
        delta = make_delta(object_id="object-1")

        changed = make_delta(
            object_id="object-2",
            version_id=delta.version_id,
        )

        assert Canonicalizer.encode_delta(delta) != Canonicalizer.encode_delta(changed)

    def test_changed_timestamp_changes_encoding(self):
        delta = make_delta(created_at=TIMESTAMP)

        changed = make_delta(
            version_id=delta.version_id,
            created_at=TIMESTAMP + 1,
        )

        assert Canonicalizer.encode_delta(delta) != Canonicalizer.encode_delta(changed)

    def test_hash_delta_is_sha256_of_encoded_delta(self):
        delta = make_delta()

        assert (
            Canonicalizer.hash_delta(delta)
            == hashlib.sha256(Canonicalizer.encode_delta(delta)).digest()
        )

    def test_hash_delta_changes_when_delta_changes(self):
        delta_a = make_delta(added_chunks=(make_ref(),))
        delta_b = make_delta(
            version_id=delta_a.version_id,
            added_chunks=(make_ref(),),
        )

        assert Canonicalizer.hash_delta(delta_a) != Canonicalizer.hash_delta(delta_b)


# ============================================================
# Version
# ============================================================


class TestVersionCanonicalization:

    def test_signing_payload_is_deterministic(self):
        version = make_version()

        assert Canonicalizer.encode_version_signing_payload(
            version
        ) == Canonicalizer.encode_version_signing_payload(version)

    def test_signing_payload_has_domain_tag(self):
        version = make_version()

        assert Canonicalizer.encode_version_signing_payload(version).startswith(
            Canonicalizer.DOMAIN_VERSION_SIGNING
        )

    def test_signature_does_not_affect_signing_payload(self):
        version = make_version(signature=b"signature-a")

        changed = make_version(
            version_id=version.version_id,
            signature=b"signature-b",
        )

        assert Canonicalizer.encode_version_signing_payload(
            version
        ) == Canonicalizer.encode_version_signing_payload(changed)

    def test_signature_affects_full_version_encoding(self):
        version = make_version(signature=b"signature-a")

        changed = make_version(
            version_id=version.version_id,
            signature=b"signature-b",
        )

        assert Canonicalizer.encode_version(version) != Canonicalizer.encode_version(
            changed
        )

    def test_full_version_has_record_domain_tag(self):
        version = make_version()

        assert Canonicalizer.encode_version(version).startswith(
            Canonicalizer.DOMAIN_VERSION_RECORD
        )

    def test_hash_version_is_sha256_of_full_encoding(self):
        version = make_version()

        assert (
            Canonicalizer.hash_version(version)
            == hashlib.sha256(Canonicalizer.encode_version(version)).digest()
        )

    def test_hash_version_changes_when_signature_changes(self):
        version = make_version(signature=b"signature-a")

        changed = make_version(
            version_id=version.version_id,
            signature=b"signature-b",
        )

        assert Canonicalizer.hash_version(version) != Canonicalizer.hash_version(
            changed
        )

    def test_v2_version_encodes_parent_and_previous_hash(self):
        parent_id = uid()
        previous_hash = b"\x33" * 32

        version = make_version(
            sequence=2,
            parent_version_id=parent_id,
            previous_version_hash=previous_hash,
        )

        encoded = Canonicalizer.encode_version_signing_payload(version)

        assert Canonicalizer._encode_uuid(parent_id) in encoded
        assert previous_hash in encoded


# ============================================================
# CheckpointEntry
# ============================================================


class TestCheckpointEntryCanonicalization:

    def test_same_entry_produces_same_bytes(self):
        entry = make_checkpoint_entry(uid())

        assert Canonicalizer.encode_checkpoint_entry(
            entry
        ) == Canonicalizer.encode_checkpoint_entry(entry)

    def test_checkpoint_entry_contains_domain_tag(self):
        entry = make_checkpoint_entry(uid())

        assert Canonicalizer.encode_checkpoint_entry(entry).startswith(
            Canonicalizer.DOMAIN_CHECKPOINT_ENTRY
        )

    def test_changed_order_key_changes_encoding(self):
        checkpoint_id = uid()
        keys = order_keys.generate_n_between(None, None, 2)

        entry = make_checkpoint_entry(
            checkpoint_id,
            order_key=keys[0],
        )

        changed = CheckpointEntry(
            checkpoint_id=checkpoint_id,
            chunk_ref_id=entry.chunk_ref_id,
            chunk_id=entry.chunk_id,
            order_key=keys[1],
        )

        assert Canonicalizer.encode_checkpoint_entry(
            entry
        ) != Canonicalizer.encode_checkpoint_entry(changed)


# ============================================================
# Snapshot
# ============================================================


class TestSnapshotCanonicalization:

    def test_snapshot_is_deterministic(self):
        checkpoint_id = uid()
        entries = (make_checkpoint_entry(checkpoint_id),)
        version_id = uid()

        kwargs = dict(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=entries,
        )

        assert Canonicalizer.encode_snapshot(**kwargs) == Canonicalizer.encode_snapshot(
            **kwargs
        )

    def test_entry_order_does_not_affect_snapshot(self):
        checkpoint_id = uid()
        keys = order_keys.generate_n_between(None, None, 2)

        entry_a = make_checkpoint_entry(
            checkpoint_id,
            order_key=keys[0],
        )
        entry_b = make_checkpoint_entry(
            checkpoint_id,
            order_key=keys[1],
        )

        version_id = uid()

        encoded_a = Canonicalizer.encode_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=(entry_a, entry_b),
        )

        encoded_b = Canonicalizer.encode_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=(entry_b, entry_a),
        )

        assert encoded_a == encoded_b

    def test_checkpoint_id_does_not_affect_snapshot(self):
        checkpoint_a = uid()
        checkpoint_b = uid()

        ref_id = uid()
        chunk_id = uid()
        order_key = order_keys.generate_between(None, None)

        entry_a = make_checkpoint_entry(
            checkpoint_a,
            chunk_ref_id=ref_id,
            chunk_id=chunk_id,
            order_key=order_key,
        )

        entry_b = make_checkpoint_entry(
            checkpoint_b,
            chunk_ref_id=ref_id,
            chunk_id=chunk_id,
            order_key=order_key,
        )

        version_id = uid()

        root_a = Canonicalizer.hash_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=(entry_a,),
        )

        root_b = Canonicalizer.hash_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=(entry_b,),
        )

        assert root_a == root_b

    def test_changed_chunk_ref_changes_snapshot_hash(self):
        checkpoint_id = uid()
        entry = make_checkpoint_entry(checkpoint_id)

        changed = CheckpointEntry(
            checkpoint_id=checkpoint_id,
            chunk_ref_id=uid(),
            chunk_id=entry.chunk_id,
            order_key=entry.order_key,
        )

        kwargs = dict(
            object_id="object-1",
            sequence=1,
            version_id=uid(),
        )

        root_a = Canonicalizer.hash_snapshot(
            **kwargs,
            entries=(entry,),
        )

        root_b = Canonicalizer.hash_snapshot(
            **kwargs,
            entries=(changed,),
        )

        assert root_a != root_b

    def test_snapshot_hash_is_sha256_of_encoding(self):
        checkpoint_id = uid()
        entries = (make_checkpoint_entry(checkpoint_id),)
        version_id = uid()

        encoded = Canonicalizer.encode_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=entries,
        )

        actual = Canonicalizer.hash_snapshot(
            object_id="object-1",
            sequence=1,
            version_id=version_id,
            entries=entries,
        )

        assert actual == hashlib.sha256(encoded).digest()

    def test_snapshot_rejects_duplicate_chunk_ref_ids(self):
        checkpoint_id = uid()
        ref_id = uid()

        entries = (
            make_checkpoint_entry(
                checkpoint_id,
                chunk_ref_id=ref_id,
            ),
            make_checkpoint_entry(
                checkpoint_id,
                chunk_ref_id=ref_id,
            ),
        )

        with pytest.raises(CanonicalizationError):
            Canonicalizer.encode_snapshot(
                object_id="object-1",
                sequence=1,
                version_id=uid(),
                entries=entries,
            )

    def test_snapshot_rejects_duplicate_order_keys(self):
        checkpoint_id = uid()
        key = order_keys.generate_between(None, None)

        entries = (
            make_checkpoint_entry(checkpoint_id, order_key=key),
            make_checkpoint_entry(checkpoint_id, order_key=key),
        )

        with pytest.raises(CanonicalizationError):
            Canonicalizer.encode_snapshot(
                object_id="object-1",
                sequence=1,
                version_id=uid(),
                entries=entries,
            )

    def test_snapshot_rejects_entries_from_different_checkpoints(self):
        entries = (
            make_checkpoint_entry(uid()),
            make_checkpoint_entry(uid()),
        )

        with pytest.raises(CanonicalizationError):
            Canonicalizer.encode_snapshot(
                object_id="object-1",
                sequence=1,
                version_id=uid(),
                entries=entries,
            )

    def test_snapshot_rejects_non_tuple_entries(self):
        with pytest.raises(TypeError):
            Canonicalizer.encode_snapshot(
                object_id="object-1",
                sequence=1,
                version_id=uid(),
                entries=[],
            )


# ============================================================
# Checkpoint
# ============================================================


class TestCheckpointCanonicalization:

    def test_checkpoint_encoding_is_deterministic(self):
        checkpoint = make_checkpoint()

        assert Canonicalizer.encode_checkpoint(
            checkpoint
        ) == Canonicalizer.encode_checkpoint(checkpoint)

    def test_checkpoint_contains_domain_tag(self):
        checkpoint = make_checkpoint()

        assert Canonicalizer.encode_checkpoint(checkpoint).startswith(
            Canonicalizer.DOMAIN_CHECKPOINT
        )

    def test_checkpoint_hash_is_sha256_of_encoding(self):
        checkpoint = make_checkpoint()

        assert (
            Canonicalizer.hash_checkpoint(checkpoint)
            == hashlib.sha256(Canonicalizer.encode_checkpoint(checkpoint)).digest()
        )

    def test_changed_snapshot_root_changes_checkpoint_encoding(self):
        checkpoint = make_checkpoint(snapshot_root=b"\x11" * 32)

        changed = make_checkpoint(
            checkpoint_id=checkpoint.checkpoint_id,
            snapshot_root=b"\x22" * 32,
        )

        assert Canonicalizer.encode_checkpoint(
            checkpoint
        ) != Canonicalizer.encode_checkpoint(changed)
