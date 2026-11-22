#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const vectorPath = process.argv[2] ?? 'shard_merkle_vectors.json';
const caseName = process.argv[3] ?? 'three_shards_63_bytes';
const outputPath = process.argv[4] ?? 'build/engram_shard_post_main.circom';

const doc = JSON.parse(fs.readFileSync(vectorPath, 'utf8'));
if (doc.protocol !== 'ENGRAM-SHARD-MERKLE/1') throw new Error('Unsupported protocol');
const c = doc.cases.find(x => x.name === caseName);
if (!c) throw new Error(`Unknown case: ${caseName}`);

const ceilLog2 = n => Math.ceil(Math.log2(n));
const nShards = c.shard_count;
const shardBytesLen = c.shard_size_bytes;
const nChunks = Math.ceil(shardBytesLen / 31);
const chunkDepth = ceilLog2(nChunks);
const shardDepth = ceilLog2(nShards);

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
const source = `pragma circom 2.0.0;
include "../engram_shard_post.circom";

component main { public [globalRoot, shardIndex, chunkIndex] } =
    EngramShardPoSt(${nShards}, ${shardBytesLen}, ${nChunks}, ${chunkDepth}, ${shardDepth});
`;
fs.writeFileSync(outputPath, source);
console.log(`Wrote ${outputPath}`);
console.log({ caseName, nShards, shardBytesLen, nChunks, chunkDepth, shardDepth });
