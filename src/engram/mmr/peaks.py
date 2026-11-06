"""Canonical left-to-right mountain decomposition for an MMR size."""
from __future__ import annotations
def peak_heights(leaf_count:int)->list[int]:
    if leaf_count<0: raise ValueError("leaf_count cannot be negative")
    return [bit for bit in range(leaf_count.bit_length()-1,-1,-1) if leaf_count&(1<<bit)]
def peak_layout(leaf_count:int)->list[tuple[int,int]]:
    start=0; result=[]
    for height in peak_heights(leaf_count): result.append((start,height)); start+=1<<height
    return result
