#!/usr/bin/env node
/**
 * WS1-05: Frontend production bundle budget check (raw/uncompressed build sizes).
 *
 * Uses Vite's build manifest and generated index.html to identify the initial
 * static import graph (entry + recursive static imports + their CSS). Dynamic
 * imports are excluded from initial-load totals. No external dependencies.
 *
 * Usage:
 *   node scripts/check-bundle-budget.mjs [outDir]
 * Default outDir: dist (relative to frontend/)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(__dirname, "..");
const outDir = path.resolve(frontendDir, process.argv[2] || "dist");

// Budgets (raw/uncompressed bytes). Set from measured baseline (2026-11-14,
// Vite 8/Rolldown production build, develop @ WS1-05) with ~10-15% headroom:
// - Initial JS: 437,149 B measured -> 512,000 B (500 KiB)
// - Initial CSS: 70,704 B measured -> 81,920 B (80 KiB)
// - Combined initial JS+CSS: 507,853 B measured -> 593,920 B (580 KiB)
// - Largest individual JS chunk: 1,613,703 B (ChartView, lazy) measured -> 1,835,008 B (1.75 MiB)
// Initial-load and individual-chunk budgets are separate; lazy chunks (including
// ChartView/echarts) are excluded from initial-load totals but bounded by the
// individual-chunk budget.
const BUDGETS = {
  initialJs: 512_000,
  initialCss: 81_920,
  initialTotal: 593_920,
  largestJsChunk: 1_835_008,
};

function fail(msg) {
  console.error(`BUNDLE BUDGET FAIL: ${msg}`);
  process.exitCode = 1;
}

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MiB (${bytes} B)`;
  return `${(bytes / 1024).toFixed(2)} KiB (${bytes} B)`;
}

function readJson(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing ${label}: ${filePath}`);
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function collectStaticGraph(manifest, entryKey) {
  const visited = new Set();
  const stack = [entryKey];
  while (stack.length) {
    const key = stack.pop();
    if (!key || visited.has(key)) continue;
    const chunk = manifest[key];
    if (!chunk) {
      throw new Error(`Manifest entry not found for key: ${key}`);
    }
    visited.add(key);
    for (const imp of chunk.imports || []) {
      if (!visited.has(imp)) stack.push(imp);
    }
  }
  return visited;
}

function fileSizeSafe(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing build artifact for ${label}: ${filePath}`);
  }
  return fs.statSync(filePath).size;
}

function reportLine(name, actual, budget) {
  const ok = actual <= budget;
  const headroom = budget - actual;
  const status = ok ? "PASS" : "FAIL";
  console.log(
    `${status}  ${name}: actual ${formatBytes(actual)} | budget ${formatBytes(budget)} | headroom ${formatBytes(headroom)}`,
  );
  return ok;
}

try {
  const indexPath = path.join(outDir, "index.html");
  const manifestPath = path.join(outDir, ".vite", "manifest.json");
  if (!fs.existsSync(indexPath)) {
    throw new Error(`Missing build artifact: ${indexPath}`);
  }
  const manifest = readJson(manifestPath, "Vite manifest (.vite/manifest.json)");

  // Find HTML entry in manifest.
  const entryKey = Object.keys(manifest).find((k) => k.endsWith("index.html") && manifest[k]?.isEntry);
  if (!entryKey) {
    throw new Error("Could not find HTML entry (index.html, isEntry) in manifest");
  }

  const initialKeys = collectStaticGraph(manifest, entryKey);
  const initialJsFiles = new Set();
  const initialCssFiles = new Set();

  for (const key of initialKeys) {
    const chunk = manifest[key];
    if (chunk.file && chunk.file.endsWith(".js")) initialJsFiles.add(chunk.file);
    for (const css of chunk.css || []) {
      if (css.endsWith(".css")) initialCssFiles.add(css);
    }
  }

  let initialJs = 0;
  for (const file of initialJsFiles) {
    initialJs += fileSizeSafe(path.join(outDir, file), `initial JS ${file}`);
  }
  let initialCss = 0;
  for (const file of initialCssFiles) {
    initialCss += fileSizeSafe(path.join(outDir, file), `initial CSS ${file}`);
  }
  const initialTotal = initialJs + initialCss;

  // Largest individual JS chunk across all emitted JS assets (initial + lazy).
  let largestJsChunk = { file: "(none)", size: 0 };
  for (const [key, chunk] of Object.entries(manifest)) {
    if (!chunk.file || !chunk.file.endsWith(".js")) continue;
    const size = fileSizeSafe(path.join(outDir, chunk.file), `JS chunk ${chunk.file}`);
    if (size > largestJsChunk.size) largestJsChunk = { file: chunk.file, size };
  }

  // Verify echarts chunk remains outside initial static load graph.
  const echartsInitial = [...initialJsFiles].filter((f) => /echarts/i.test(path.basename(f)));
  if (echartsInitial.length > 0) {
    throw new Error(
      `ECharts chunk must not be in initial static load graph, found: ${echartsInitial.join(", ")}`,
    );
  }
  const echartsChunks = Object.values(manifest)
    .map((c) => c.file)
    .filter((f) => typeof f === "string" && f.endsWith(".js") && /echarts/i.test(path.basename(f)));
  if (echartsChunks.length === 0) {
    throw new Error("Expected at least one ECharts JS chunk in build output (lazy-loaded), found none");
  }

  console.log("Frontend bundle budget report (raw/uncompressed build sizes)");
  console.log(`Output: ${outDir}`);
  console.log(`Initial JS files (${initialJsFiles.size}): ${[...initialJsFiles].sort().join(", ")}`);
  console.log(`Initial CSS files (${initialCssFiles.size}): ${[...initialCssFiles].sort().join(", ")}`);
  console.log(`Largest JS chunk: ${largestJsChunk.file} (${formatBytes(largestJsChunk.size)})`);
  console.log(`ECharts chunks (lazy, excluded from initial): ${echartsChunks.sort().join(", ")}`);
  console.log("");

  const ok = [
    reportLine("Initial JS", initialJs, BUDGETS.initialJs),
    reportLine("Initial CSS", initialCss, BUDGETS.initialCss),
    reportLine("Initial JS+CSS", initialTotal, BUDGETS.initialTotal),
    reportLine("Largest JS chunk", largestJsChunk.size, BUDGETS.largestJsChunk),
  ].every(Boolean);

  if (!ok) {
    fail("One or more bundle budgets exceeded.");
  } else {
    console.log("\nBUNDLE BUDGET PASS");
  }
} catch (err) {
  fail(err instanceof Error ? err.message : String(err));
}
