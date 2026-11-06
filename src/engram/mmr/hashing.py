"""Canonical manifest encoding and domain-separated SHA-256 hashing."""
from __future__ import annotations
import hashlib, json, unicodedata
from collections.abc import Mapping
from typing import Any
LEAF_DOMAIN=b"ENGRAM_MMR_LEAF_V1"
NODE_DOMAIN=b"ENGRAM_MMR_NODE_V1"
ROOT_DOMAIN=b"ENGRAM_MMR_ROOT_V1"
HASH_SIZE=32

def _normalize(value: Any) -> Any:
    if value is None or isinstance(value,(bool,int)): return value
    if isinstance(value,float): raise TypeError("Floats are forbidden in canonical manifests")
    if isinstance(value,str): return unicodedata.normalize("NFC",value)
    if isinstance(value,(list,tuple)): return [_normalize(x) for x in value]
    if isinstance(value,Mapping):
        if not all(isinstance(k,str) for k in value): raise TypeError("Manifest keys must be strings")
        return {unicodedata.normalize("NFC",k):_normalize(v) for k,v in value.items()}
    raise TypeError(f"Unsupported manifest value: {type(value).__name__}")

def canonical_manifest_bytes(manifest: Mapping[str,Any]) -> bytes:
    if not isinstance(manifest,Mapping): raise TypeError("manifest must be a mapping")
    return json.dumps(_normalize(manifest),ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")

def hash_leaf(manifest_bytes: bytes) -> bytes:
    return hashlib.sha256(LEAF_DOMAIN+manifest_bytes).digest()

def manifest_hash(manifest: Mapping[str,Any]) -> bytes:
    return hash_leaf(canonical_manifest_bytes(manifest))

def hash_node(left: bytes,right: bytes) -> bytes:
    if len(left)!=HASH_SIZE or len(right)!=HASH_SIZE: raise ValueError("MMR child hashes must be 32 bytes")
    return hashlib.sha256(NODE_DOMAIN+left+right).digest()

def hash_root(leaf_count: int,peaks: list[tuple[int,bytes]]) -> bytes:
    if not 0<=leaf_count<2**64: raise ValueError("leaf_count must fit unsigned 64-bit")
    payload=bytearray(ROOT_DOMAIN+leaf_count.to_bytes(8,"big"))
    for height,digest in peaks:
        if not 0<=height<2**32: raise ValueError("peak height must fit unsigned 32-bit")
        if len(digest)!=HASH_SIZE: raise ValueError("peak hashes must be 32 bytes")
        payload.extend(height.to_bytes(4,"big")); payload.extend(digest)
    return hashlib.sha256(payload).digest()
