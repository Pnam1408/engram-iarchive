//engram_shard_post_template.circom
pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

template EngramShardPoSt(
    nShards,
    shardBytesLen,
    nChunks,
    chunkDepth,
    shardDepth
) {
    var LEAF_DOMAIN = 1;
    var NODE_DOMAIN = 2;

    var lastChunkBytes = shardBytesLen % 31;
    if (lastChunkBytes == 0) {
        lastChunkBytes = 31;
    }

    // Public inputs
    signal input merkleRoot;
    signal input shardIndex;
    signal input chunkIndex;

    // Private witness
    signal input chunkValue;
    signal input chunkLength;
    signal input chunkPath[chunkDepth];
    signal input shardPath[shardDepth];

    // Index constraints
    component shardIndexBits = Num2Bits(shardDepth);
    component chunkIndexBits = Num2Bits(chunkDepth);

    shardIndexBits.in <== shardIndex;
    chunkIndexBits.in <== chunkIndex;

    component chunkRange = LessThan(chunkDepth + 1);
    chunkRange.in[0] <== chunkIndex;
    chunkRange.in[1] <== nChunks;
    chunkRange.out === 1;

    // Circuit configuration constraints
    assert(nShards >= 2);
    assert(nShards == 2 ** shardDepth);
    assert(shardBytesLen > 31);
    assert(nChunks == (shardBytesLen + 30) \ 31);
    assert(chunkDepth >= 1);
    assert(nChunks <= 2 ** chunkDepth);

    // Enforce canonical chunk length
    component lastChunkCheck = IsEqual();
    lastChunkCheck.in[0] <== chunkIndex;
    lastChunkCheck.in[1] <== nChunks - 1;

    chunkLength ===
        31 + lastChunkCheck.out * (lastChunkBytes - 31);

    // A chunk contains at most 31 bytes = 248 bits
    component chunkValueBits = Num2Bits(248);
    chunkValueBits.in <== chunkValue;

    // A short final chunk must not contain extra bytes
    if (lastChunkBytes < 31) {
        for (var b = lastChunkBytes * 8; b < 248; b++) {
            chunkValueBits.out[b] * lastChunkCheck.out === 0;
        }
    }

    // --------------------------------------------------
    // Step 1: Compute chunk leaf hash
    // leaf = Poseidon(Poseidon(LEAF_DOMAIN, length), value)
    // --------------------------------------------------

    component leafStart = Poseidon(2);
    leafStart.inputs[0] <== LEAF_DOMAIN;
    leafStart.inputs[1] <== chunkLength;

    component leafHash = Poseidon(2);
    leafHash.inputs[0] <== leafStart.out;
    leafHash.inputs[1] <== chunkValue;

    // --------------------------------------------------
    // Step 2: Verify chunk Merkle path -> shard root
    // --------------------------------------------------

    signal innerCurrent[chunkDepth + 1];
    signal innerLeft[chunkDepth];
    signal innerRight[chunkDepth];

    component innerTag[chunkDepth];
    component innerHash[chunkDepth];
    component beforeLastGroup[chunkDepth];

    innerCurrent[0] <== leafHash.out;

    for (var level = 0; level < chunkDepth; level++) {
        innerLeft[level] <==
            innerCurrent[level]
            + chunkIndexBits.out[level]
              * (chunkPath[level] - innerCurrent[level]);

        innerRight[level] <==
            chunkPath[level]
            + chunkIndexBits.out[level]
              * (innerCurrent[level] - chunkPath[level]);

        innerTag[level] = Poseidon(2);
        innerTag[level].inputs[0] <== NODE_DOMAIN;
        innerTag[level].inputs[1] <== innerLeft[level];

        innerHash[level] = Poseidon(2);
        innerHash[level].inputs[0] <== innerTag[level].out;
        innerHash[level].inputs[1] <== innerRight[level];

        innerCurrent[level + 1] <== innerHash[level].out;

        // Handle duplicated final node for odd-width trees
        var widthAtLevel = nChunks;
        var groupSize = 1;

        for (var t = 0; t < level; t++) {
            widthAtLevel = (widthAtLevel + 1) \ 2;
            groupSize = groupSize * 2;
        }

        var lastGroupStart = (widthAtLevel - 1) * groupSize;

        beforeLastGroup[level] = LessThan(chunkDepth + 1);
        beforeLastGroup[level].in[0] <== chunkIndex;
        beforeLastGroup[level].in[1] <== lastGroupStart;

        if (widthAtLevel % 2 == 1) {
            (1 - beforeLastGroup[level].out)
                * (chunkPath[level] - innerCurrent[level]) === 0;
        }
    }

    // --------------------------------------------------
    // Step 3: Verify shard Merkle path -> global root
    // --------------------------------------------------

    signal outerCurrent[shardDepth + 1];
    signal outerLeft[shardDepth];
    signal outerRight[shardDepth];

    component outerTag[shardDepth];
    component outerHash[shardDepth];

    outerCurrent[0] <== innerCurrent[chunkDepth];

    for (var level = 0; level < shardDepth; level++) {
        outerLeft[level] <==
            outerCurrent[level]
            + shardIndexBits.out[level]
              * (shardPath[level] - outerCurrent[level]);

        outerRight[level] <==
            shardPath[level]
            + shardIndexBits.out[level]
              * (outerCurrent[level] - shardPath[level]);

        outerTag[level] = Poseidon(2);
        outerTag[level].inputs[0] <== NODE_DOMAIN;
        outerTag[level].inputs[1] <== outerLeft[level];

        outerHash[level] = Poseidon(2);
        outerHash[level].inputs[0] <== outerTag[level].out;
        outerHash[level].inputs[1] <== outerRight[level];

        outerCurrent[level + 1] <== outerHash[level].out;
    }

    // Final commitment check
    outerCurrent[shardDepth] === merkleRoot;
}
