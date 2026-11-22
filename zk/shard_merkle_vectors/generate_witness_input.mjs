#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const vectorPath = process.argv[2] ?? 'shard_merkle_vectors.json';
const caseName = process.argv[3] ?? 'three_shards_63_bytes';
const shardIndex = Number(process.argv[4] ?? 2);
const chunkIndex = Number(process.argv[5] ?? 2);
const outputPath = process.argv[6] ?? 'build/input.json';

const doc = JSON.parse(fs.readFileSync(vectorPath, 'utf8'));
const c = doc.cases.find(x => x.name === caseName);
if (!c) throw new Error(`Unknown case: ${caseName}`);
const shard = c.shards.find(x => x.shard_index === shardIndex);
if (!shard) throw new Error(`Invalid shard index ${shardIndex}`);
const proof = shard.proofs.find(x => x.chunk_index === chunkIndex);
if (!proof) throw new Error(`Invalid chunk index ${chunkIndex}`);

const chunkBytes = Buffer.from(proof.chunk_hex, 'hex');
if (chunkBytes.length < 1 || chunkBytes.length > 31) throw new Error('Invalid chunk byte length');
const chunkValue = BigInt(`0x${proof.chunk_hex}`).toString();

const input = {
  globalRoot: c.global_root,
  shardIndex: String(shardIndex),
  chunkIndex: String(chunkIndex),
  chunkValue,
  chunkPath: proof.chunk_siblings,
  shardPath: proof.shard_siblings
};
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, JSON.stringify(input, null, 2) + '\n');
console.log(`Wrote ${outputPath}`);
console.log(`case=${caseName}, shard=${shardIndex}, chunk=${chunkIndex}, chunkLength=${chunkBytes.length}`);
