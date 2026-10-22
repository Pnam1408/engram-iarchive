
pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

template EngramPoSt(
    nShards,
    nSelected,
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

    signal input merkleRoot;

    // Public challenge inputs.
    signal input idx0;
    signal input idx1;
    signal input idx2;
    signal input chunkIdx0;
    signal input chunkIdx1;
    signal input chunkIdx2;

    // Private witness.
    signal input chunkValues[nSelected];
    signal input chunkLengths[nSelected];
    signal input chunkPaths[nSelected][chunkDepth];
    signal input shardPaths[nSelected][shardDepth];

    signal shardIndices[nSelected];
    signal chunkIndices[nSelected];

    shardIndices[0] <== idx0;
    shardIndices[1] <== idx1;
    shardIndices[2] <== idx2;

    chunkIndices[0] <== chunkIdx0;
    chunkIndices[1] <== chunkIdx1;
    chunkIndices[2] <== chunkIdx2;

    assert(nSelected == 3);
    assert(nShards >= 2);
    assert(nShards == 2 ** shardDepth);
    assert(shardBytesLen > 31);
    assert(nChunks == (shardBytesLen + 30) \ 31);
    assert(chunkDepth >= 1);
    assert(nChunks <= 2 ** chunkDepth);

    component shardIndexBits[nSelected];
    component chunkIndexBits[nSelected];
    component chunkRange[nSelected];
    component chunkValueBits[nSelected];
    component lastChunkCheck[nSelected];

    component leafStart[nSelected];
    component leafHash[nSelected];

    component innerTag[nSelected][chunkDepth];
    component innerHash[nSelected][chunkDepth];
    component outerTag[nSelected][shardDepth];
    component outerHash[nSelected][shardDepth];

    // Predeclare and instantiate all components outside conditional scopes.
    component beforeLastGroup[nSelected][chunkDepth];

    signal innerCurrent[nSelected][chunkDepth + 1];
    signal innerLeft[nSelected][chunkDepth];
    signal innerRight[nSelected][chunkDepth];

    signal outerCurrent[nSelected][shardDepth + 1];
    signal outerLeft[nSelected][shardDepth];
    signal outerRight[nSelected][shardDepth];

    // Selected shard indices must be distinct.
    component shardZero01 = IsZero();
    component shardZero02 = IsZero();
    component shardZero12 = IsZero();

    shardZero01.in <== idx0 - idx1;
    shardZero02.in <== idx0 - idx2;
    shardZero12.in <== idx1 - idx2;

    shardZero01.out === 0;
    shardZero02.out === 0;
    shardZero12.out === 0;

    for (var s = 0; s < nSelected; s++) {
        shardIndexBits[s] = Num2Bits(shardDepth);
        shardIndexBits[s].in <== shardIndices[s];

        chunkIndexBits[s] = Num2Bits(chunkDepth);
        chunkIndexBits[s].in <== chunkIndices[s];

        chunkRange[s] = LessThan(chunkDepth + 1);
        chunkRange[s].in[0] <== chunkIndices[s];
        chunkRange[s].in[1] <== nChunks;
        chunkRange[s].out === 1;

        // Enforce the canonical chunk length.
        lastChunkCheck[s] = IsEqual();
        lastChunkCheck[s].in[0] <== chunkIndices[s];
        lastChunkCheck[s].in[1] <== nChunks - 1;

        chunkLengths[s] ===
            31 + lastChunkCheck[s].out * (lastChunkBytes - 31);

        // Chunk bytes are packed as a big-endian integer.
        chunkValueBits[s] = Num2Bits(248);
        chunkValueBits[s].in <== chunkValues[s];

        // A short final chunk cannot contain bytes beyond its length.
        if (lastChunkBytes < 31) {
            for (var b = lastChunkBytes * 8; b < 248; b++) {
                chunkValueBits[s].out[b]
                    * lastChunkCheck[s].out === 0;
            }
        }

        // leaf = Poseidon(Poseidon(LEAF_DOMAIN, length), value)
        leafStart[s] = Poseidon(2);
        leafStart[s].inputs[0] <== LEAF_DOMAIN;
        leafStart[s].inputs[1] <== chunkLengths[s];

        leafHash[s] = Poseidon(2);
        leafHash[s].inputs[0] <== leafStart[s].out;
        leafHash[s].inputs[1] <== chunkValues[s];

        innerCurrent[s][0] <== leafHash[s].out;

        // Chunk leaf -> shard root.
        for (var level = 0; level < chunkDepth; level++) {
            innerLeft[s][level] <==
                innerCurrent[s][level]
                + chunkIndexBits[s].out[level]
                  * (chunkPaths[s][level] - innerCurrent[s][level]);

            innerRight[s][level] <==
                chunkPaths[s][level]
                + chunkIndexBits[s].out[level]
                  * (innerCurrent[s][level] - chunkPaths[s][level]);

            innerTag[s][level] = Poseidon(2);
            innerTag[s][level].inputs[0] <== NODE_DOMAIN;
            innerTag[s][level].inputs[1] <== innerLeft[s][level];

            innerHash[s][level] = Poseidon(2);
            innerHash[s][level].inputs[0] <== innerTag[s][level].out;
            innerHash[s][level].inputs[1] <== innerRight[s][level];

            innerCurrent[s][level + 1] <== innerHash[s][level].out;

            // Width of this tree level and the original index range
            // belonging to its final node.
            var widthAtLevel = nChunks;
            var groupSize = 1;

            for (var t = 0; t < level; t++) {
                widthAtLevel = (widthAtLevel + 1) \ 2;
                groupSize = groupSize * 2;
            }

            var lastGroupStart = (widthAtLevel - 1) * groupSize;

            beforeLastGroup[s][level] = LessThan(chunkDepth + 1);
            beforeLastGroup[s][level].in[0] <== chunkIndices[s];
            beforeLastGroup[s][level].in[1] <== lastGroupStart;

            // Python duplicates the final node when the level width is odd.
            if (widthAtLevel % 2 == 1) {
                (1 - beforeLastGroup[s][level].out)
                    * (
                        chunkPaths[s][level]
                        - innerCurrent[s][level]
                    ) === 0;
            }
        }

        outerCurrent[s][0] <== innerCurrent[s][chunkDepth];

        // Shard root -> global root.
        for (var level = 0; level < shardDepth; level++) {
            outerLeft[s][level] <==
                outerCurrent[s][level]
                + shardIndexBits[s].out[level]
                  * (shardPaths[s][level] - outerCurrent[s][level]);

            outerRight[s][level] <==
                shardPaths[s][level]
                + shardIndexBits[s].out[level]
                  * (outerCurrent[s][level] - shardPaths[s][level]);

            outerTag[s][level] = Poseidon(2);
            outerTag[s][level].inputs[0] <== NODE_DOMAIN;
            outerTag[s][level].inputs[1] <== outerLeft[s][level];

            outerHash[s][level] = Poseidon(2);
            outerHash[s][level].inputs[0] <== outerTag[s][level].out;
            outerHash[s][level].inputs[1] <== outerRight[s][level];

            outerCurrent[s][level + 1] <== outerHash[s][level].out;
        }

        outerCurrent[s][shardDepth] === merkleRoot;
    }
}
