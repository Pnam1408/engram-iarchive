from uuid import uuid4

import pytest

from engram.metadata.logical.models import Object, Version, VersionDelta
from engram.metadata.logical.services import (
    Canonicalizer,
    Ed25519VersionSigner,
    VersionBuilder,
    VersionDeltaMismatchError,
    VersionHeadMismatchError,
    verify_ed25519_signature,
)


def uid():
    return str(uuid4())


def make_delta(object_id, sequence, *, version_id=None, added=(), removed=(), created_at=100):
    return VersionDelta(
        object_id=object_id,
        version_id=version_id or uid(),
        sequence=sequence,
        added_chunks=tuple(added),
        removed_chunk_ref_ids=tuple(removed),
        created_at=created_at,
    )


def verify_version(version):
    payload = Canonicalizer.encode_version_signing_payload(version)
    return verify_ed25519_signature(
        version.writer_public_key,
        payload,
        version.signature,
    )


def test_build_first_version():
    object_id = "object-A"
    obj = Object(object_id=object_id, created_at=1, updated_at=1)
    delta = make_delta(object_id, 1)
    signer = Ed25519VersionSigner.generate()

    result = VersionBuilder().build(
        object=obj,
        parent_version=None,
        delta=delta,
        signer=signer,
    )

    assert result.delta is delta
    assert result.version.version_id == delta.version_id
    assert result.version.object_id == object_id
    assert result.version.sequence == 1
    assert result.version.parent_version_id is None
    assert result.version.previous_version_hash is None
    assert result.version.delta_root == Canonicalizer.hash_delta(delta)
    assert result.version.writer_public_key == signer.public_key
    assert verify_version(result.version)


def test_build_second_version_links_parent_hash():
    signer = Ed25519VersionSigner.generate()
    canonicalizer = Canonicalizer()
    object_id = "object-A"

    parent = Version(
        version_id=uid(),
        object_id=object_id,
        sequence=1,
        parent_version_id=None,
        delta_root=b"a" * 32,
        previous_version_hash=None,
        codec="ENGRAM-LOGICAL-METADATA/1",
        writer_public_key=signer.public_key,
        signature=b"parent-signature",
        created_at=100,
    )
    obj = Object(
        object_id=object_id,
        head_sequence=1,
        head_version_id=parent.version_id,
        created_at=1,
        updated_at=100,
    )
    delta = make_delta(object_id, 2)

    result = VersionBuilder(canonicalizer).build(
        object=obj,
        parent_version=parent,
        delta=delta,
        signer=signer,
    )

    assert result.version.parent_version_id == parent.version_id
    assert result.version.previous_version_hash == canonicalizer.hash_version(parent)
    assert verify_version(result.version)


def test_reject_parent_missing_for_non_initial_object():
    obj = Object(
        object_id="object-A",
        head_sequence=1,
        head_version_id=uid(),
        created_at=1,
        updated_at=1,
    )
    with pytest.raises(VersionHeadMismatchError):
        VersionBuilder().build(
            object=obj,
            parent_version=None,
            delta=make_delta(obj.object_id, 2),
            signer=Ed25519VersionSigner.generate(),
        )


def test_reject_parent_not_matching_object_head():
    signer = Ed25519VersionSigner.generate()
    parent = Version(
        version_id=uid(),
        object_id="object-A",
        sequence=1,
        parent_version_id=None,
        delta_root=b"a" * 32,
        previous_version_hash=None,
        codec="test",
        writer_public_key=signer.public_key,
        signature=b"sig",
        created_at=1,
    )
    obj = Object(
        object_id="object-A",
        head_sequence=1,
        head_version_id=uid(),
        created_at=1,
        updated_at=1,
    )
    with pytest.raises(VersionHeadMismatchError):
        VersionBuilder().build(
            object=obj,
            parent_version=parent,
            delta=make_delta(obj.object_id, 2),
            signer=signer,
        )


@pytest.mark.parametrize(
    "delta",
    [
        lambda oid: make_delta("another-object", 1),
        lambda oid: make_delta(oid, 2),
    ],
)
def test_reject_delta_for_wrong_object_or_sequence(delta):
    obj = Object(object_id="object-A", created_at=1, updated_at=1)
    with pytest.raises(VersionDeltaMismatchError):
        VersionBuilder().build(
            object=obj,
            parent_version=None,
            delta=delta(obj.object_id),
            signer=Ed25519VersionSigner.generate(),
        )


def test_signature_fails_if_signed_fields_are_changed():
    signer = Ed25519VersionSigner.generate()
    obj = Object(object_id="object-A", created_at=1, updated_at=1)
    version = VersionBuilder().build(
        object=obj,
        parent_version=None,
        delta=make_delta(obj.object_id, 1),
        signer=signer,
    ).version

    assert verify_version(version)
    changed_payload = Canonicalizer.encode_version_signing_payload(
        Version(
            version_id=version.version_id,
            object_id=version.object_id,
            sequence=version.sequence,
            parent_version_id=version.parent_version_id,
            delta_root=b"z" * 32,
            previous_version_hash=version.previous_version_hash,
            codec=version.codec,
            writer_public_key=version.writer_public_key,
            signature=version.signature,
            created_at=version.created_at,
        )
    )
    assert not verify_ed25519_signature(
        version.writer_public_key,
        changed_payload,
        version.signature,
    )


def test_ed25519_signer_rejects_invalid_private_key_length():
    with pytest.raises(ValueError):
        Ed25519VersionSigner.from_private_bytes(b"too-short")
