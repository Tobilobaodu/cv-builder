#!/usr/bin/env node
// Bundle-size CI gate (perf addendum §3.2 "quick wins": "Add a bundle-size
// CI check"). Deliberately avoids adding a new dependency (size-limit,
// bundlesize) for this — Node's built-in zlib and a walk of
// .next/static/chunks is enough to catch an unreviewed size regression,
// and the addendum's own backlog item #5 also warns about carelessly
// growing the dependency tree.
//
// Budget is a total-gzipped-JS ceiling across every emitted chunk (not
// just one route's first load) — coarser than a per-route budget, but
// simple, and this app's dashboard routes share most of their chunks
// anyway. Run after `next build`.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import path from "node:path";

const CHUNKS_DIR = path.join(process.cwd(), ".next", "static", "chunks");
// ~34% headroom over the ~373KB baseline measured when this check was
// added — enough room for real growth, tight enough to catch an
// accidental heavy-dependency import. Raise deliberately, with review,
// not by editing this silently when it starts failing.
const BUDGET_BYTES = 500 * 1024;

function walk(dir) {
  let files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files = files.concat(walk(full));
    } else if (entry.name.endsWith(".js")) {
      files.push(full);
    }
  }
  return files;
}

let totalGzipBytes = 0;
let files;
try {
  files = walk(CHUNKS_DIR);
} catch {
  console.error(`Could not read ${CHUNKS_DIR} — run "next build" first.`);
  process.exit(1);
}

for (const file of files) {
  const raw = readFileSync(file);
  totalGzipBytes += gzipSync(raw).length;
}

const totalKb = (totalGzipBytes / 1024).toFixed(1);
const budgetKb = (BUDGET_BYTES / 1024).toFixed(0);
const pct = ((totalGzipBytes / BUDGET_BYTES) * 100).toFixed(0);

console.log(`Bundle size: ${totalKb} KB gzipped across ${files.length} chunks (${pct}% of ${budgetKb} KB budget)`);

if (totalGzipBytes > BUDGET_BYTES) {
  console.error(`FAIL: bundle size exceeds the ${budgetKb} KB budget.`);
  process.exit(1);
}
