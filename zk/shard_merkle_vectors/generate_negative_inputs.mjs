#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const inputPath = process.argv[2];
const outputDir = process.argv[3];
if (!inputPath || !outputDir) {
  throw new Error('Usage: node generate_negative_inputs.mjs <valid-input.json> <output-dir>');
}

const FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
const base = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
fs.mkdirSync(outputDir, { recursive: true });

function write(name, mutate) {
  const value = structuredClone(base);
  mutate(value);
  const file = path.join(outputDir, `${name}.json`);
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
  console.log(`Wrote ${file}`);
}
function plusOne(value) {
  return ((BigInt(value) + 1n) % FIELD).toString();
}

write('wrong_global_root', x => { x.globalRoot = plusOne(x.globalRoot); });
write('wrong_chunk_sibling', x => { x.chunkPath[0] = plusOne(x.chunkPath[0]); });
write('wrong_shard_sibling', x => { x.shardPath[1] = plusOne(x.shardPath[1]); });
write('out_of_range_chunk_index', x => { x.chunkIndex = '3'; });
write('out_of_range_shard_index', x => { x.shardIndex = '5'; });
