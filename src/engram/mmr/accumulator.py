"""In-memory MMR accumulator with deterministic JSON snapshot support."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Mapping
import json
from .hashing import hash_leaf,hash_node,hash_root,canonical_manifest_bytes
from .types import MMRState
@dataclass(frozen=True)
class Peak:
    start_leaf:int
    height:int
    digest:bytes
class MMRAccumulator:
    SCHEMA_VERSION=1
    def __init__(self)->None:
        self.leaf_count=0; self.last_version=0; self._peaks:list[Peak]=[]
        self._nodes:dict[tuple[int,int],bytes]={}; self._manifests:list[dict[str,Any]]=[]
    @property
    def peaks(self)->tuple[Peak,...]: return tuple(self._peaks)
    @property
    def root(self)->bytes: return hash_root(self.leaf_count,[(p.height,p.digest) for p in self._peaks])
    def append(self,manifest:Mapping[str,Any],*,version:int|None=None)->bytes:
        expected=self.last_version+1; actual=expected if version is None else version
        if actual!=expected: raise ValueError(f"MMR versions must be contiguous: expected {expected}, got {actual}")
        encoded=canonical_manifest_bytes(manifest)
        item=json.loads(encoded.decode("utf-8"))
        digest=hash_leaf(encoded); start=self.leaf_count; height=0
        self._nodes[(start,height)]=digest
        while self._peaks and self._peaks[-1].height==height:
            left=self._peaks.pop(); digest=hash_node(left.digest,digest); start=left.start_leaf; height+=1
            self._nodes[(start,height)]=digest
        self._peaks.append(Peak(start,height,digest)); self._manifests.append(item)
        self.leaf_count+=1; self.last_version=actual
        return self.root
    def get_node(self,start_leaf:int,height:int)->bytes:
        try:return self._nodes[(start_leaf,height)]
        except KeyError as exc: raise KeyError(f"Unknown MMR node ({start_leaf}, {height})") from exc
    def to_dict(self)->MMRState:
        return {"schema_version":self.SCHEMA_VERSION,"leaf_count":self.leaf_count,"last_version":self.last_version,
          "peaks":[{"start_leaf":p.start_leaf,"height":p.height,"hash":p.digest.hex()} for p in self._peaks],
          "root":self.root.hex(),"nodes":[{"start_leaf":s,"height":h,"hash":d.hex()} for (s,h),d in sorted(self._nodes.items())],
          "manifests":list(self._manifests)}
    @classmethod
    def from_dict(cls,state:Mapping[str,Any])->"MMRAccumulator":
        if state.get("schema_version")!=cls.SCHEMA_VERSION: raise ValueError("Unsupported MMR state schema version")
        count=state.get("leaf_count"); last=state.get("last_version"); manifests=state.get("manifests")
        if not isinstance(count,int) or count<0 or not isinstance(last,int): raise ValueError("Invalid MMR counters")
        if not isinstance(manifests,list) or len(manifests)!=count: raise ValueError("MMR manifest count mismatch")
        acc=cls()
        for i,manifest in enumerate(manifests,1):
            if not isinstance(manifest,dict): raise ValueError("Invalid MMR manifest entry")
            acc.append(manifest,version=i)
        if acc.last_version!=last: raise ValueError("MMR last_version mismatch")
        rebuilt=acc.to_dict()
        for field in ("peaks","nodes","root"):
            if rebuilt[field]!=state.get(field): raise ValueError(f"MMR {field} metadata mismatch")
        return acc
