pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

// ENGRAM-SHARD-MERKLE/1
// Public inputs: globalRoot, shardIndex, chunkIndex.
// Private inputs: chunkValue, chunkPath, shardPath.
// All shards in a Generation have the same byte length.
template EngramShardPoSt(
    nShards,
    shardBytesLen,
    nChunks,
    chunkDepth,
    shardDepth
) {
    assert(nShards >= 2);
    assert(shardBytesLen > 31);
    assert(nChunks >= 2);
    assert(nChunks == (shardBytesLen + 30) \ 31);
    assert(chunkDepth >= 1);
    assert(shardDepth >= 1);
    assert(nChunks <= 2 ** chunkDepth);
    assert(nShards <= 2 ** shardDepth);
    assert(nChunks > 2 ** (chunkDepth - 1));
    assert(nShards > 2 ** (shardDepth - 1));

    signal input globalRoot;
    signal input shardIndex;
    signal input chunkIndex;

    signal input chunkValue;
    signal input chunkPath[chunkDepth];
    signal input shardPath[shardDepth];

    // Indices must be in range; the bit decompositions are also used to
    // select left/right ordering at each Merkle level.
    component chunkIndexBits = Num2Bits(chunkDepth);
    chunkIndexBits.in <== chunkIndex;
    component shardIndexBits = Num2Bits(shardDepth);
    shardIndexBits.in <== shardIndex;

    component chunkIndexBound = LessThan(chunkDepth + 1);
    chunkIndexBound.in[0] <== chunkIndex;
    chunkIndexBound.in[1] <== nChunks;
    chunkIndexBound.out === 1;

    component shardIndexBound = LessThan(shardDepth + 1);
    shardIndexBound.in[0] <== shardIndex;
    shardIndexBound.in[1] <== nShards;
    shardIndexBound.out === 1;

    // The final chunk may be shorter than 31 bytes. Chunk values are
    // interpreted as unsigned big-endian integers, exactly as in Python.
    var lastChunkBytes = shardBytesLen % 31;
    if (lastChunkBytes == 0) {
        lastChunkBytes = 31;
    }

    signal chunkLength;
    component isLastChunk = IsEqual();
    isLastChunk.in[0] <== chunkIndex;
    isLastChunk.in[1] <== nChunks - 1;
    chunkLength <== 31 - isLastChunk.out * (31 - lastChunkBytes);

    component chunkValueBits = Num2Bits(248);
    chunkValueBits.in <== chunkValue;

    // If the selected chunk is the short final chunk, all bits above its
    // actual byte length must be zero.
    for (var b = 8 * lastChunkBytes; b < 248; b++) {
        chunkValueBits.out[b] * isLastChunk.out === 0;
    }

    // Inner tree: Poseidon(1, chunkLength, chunkValue), then
    // Poseidon(2, left, right), duplicating the final node on odd widths.
    component chunkLeaf = Poseidon(3);
    chunkLeaf.inputs[0] <== 1;
    chunkLeaf.inputs[1] <== chunkLength;
    chunkLeaf.inputs[2] <== chunkValue;

    signal chunkCurrent[chunkDepth + 1];
    chunkCurrent[0] <== chunkLeaf.out;

    signal chunkIndexAtLevel[chunkDepth];
    signal innerLeft[chunkDepth];
    signal innerRight[chunkDepth];
    component innerDuplicateCheck[chunkDepth];
    component innerNode[chunkDepth];

    var innerWidth = nChunks;
    for (var level = 0; level < chunkDepth; level++) {
        var idx = 0;
        for (var bit = level; bit < chunkDepth; bit++) {
            idx += chunkIndexBits.out[bit] * (2 ** (bit - level));
        }
        chunkIndexAtLevel[level] <== idx;

        innerDuplicateCheck[level] = IsEqual();
        innerDuplicateCheck[level].in[0] <== chunkIndexAtLevel[level];
        innerDuplicateCheck[level].in[1] <== innerWidth - 1;

        if (innerWidth % 2 == 1) {
            (chunkPath[level] - chunkCurrent[level])
                * innerDuplicateCheck[level].out === 0;
        }

        innerLeft[level] <== chunkCurrent[level]
                            + chunkIndexBits.out[level]
                            * (chunkPath[level] - chunkCurrent[level]);

        innerRight[level] <== chunkPath[level]
                             + chunkIndexBits.out[level]
                             * (chunkCurrent[level] - chunkPath[level]);

        innerNode[level] = Poseidon(3);
        innerNode[level].inputs[0] <== 2;
        innerNode[level].inputs[1] <== innerLeft[level];
        innerNode[level].inputs[2] <== innerRight[level];
        chunkCurrent[level + 1] <== innerNode[level].out;

        innerWidth = (innerWidth + 1) \ 2;
    }

    component shardRootHasher = Poseidon(3);
    shardRootHasher.inputs[0] <== 3;
    shardRootHasher.inputs[1] <== nChunks;
    shardRootHasher.inputs[2] <== chunkCurrent[chunkDepth];
    signal shardRoot;
    shardRoot <== shardRootHasher.out;

    // Outer tree: bind the shard index into each leaf, then use the same
    // ordered parent hash and duplicate-last rule as the Python commitment.
    component globalLeaf = Poseidon(3);
    globalLeaf.inputs[0] <== 4;
    globalLeaf.inputs[1] <== shardIndex;
    globalLeaf.inputs[2] <== shardRoot;

    signal globalCurrent[shardDepth + 1];
    globalCurrent[0] <== globalLeaf.out;

    signal shardIndexAtLevel[shardDepth];
    signal outerLeft[shardDepth];
    signal outerRight[shardDepth];
    component outerDuplicateCheck[shardDepth];
    component outerNode[shardDepth];

    var outerWidth = nShards;
    for (var level = 0; level < shardDepth; level++) {
        var idx = 0;
        for (var bit = level; bit < shardDepth; bit++) {
            idx += shardIndexBits.out[bit] * (2 ** (bit - level));
        }
        shardIndexAtLevel[level] <== idx;

        outerDuplicateCheck[level] = IsEqual();
        outerDuplicateCheck[level].in[0] <== shardIndexAtLevel[level];
        outerDuplicateCheck[level].in[1] <== outerWidth - 1;

        if (outerWidth % 2 == 1) {
            (shardPath[level] - globalCurrent[level])
                * outerDuplicateCheck[level].out === 0;
        }

        outerLeft[level] <== globalCurrent[level]
                            + shardIndexBits.out[level]
                            * (shardPath[level] - globalCurrent[level]);
        
        outerRight[level] <== shardPath[level]
                             + shardIndexBits.out[level]
                             * (globalCurrent[level] - shardPath[level]);

        outerNode[level] = Poseidon(3);
        outerNode[level].inputs[0] <== 2;
        outerNode[level].inputs[1] <== outerLeft[level];
        outerNode[level].inputs[2] <== outerRight[level];
        globalCurrent[level + 1] <== outerNode[level].out;

        outerWidth = (outerWidth + 1) \ 2;
    }

    component globalRootHasher = Poseidon(3);
    globalRootHasher.inputs[0] <== 5;
    globalRootHasher.inputs[1] <== nShards;
    globalRootHasher.inputs[2] <== globalCurrent[shardDepth];
    globalRootHasher.out === globalRoot;
}
