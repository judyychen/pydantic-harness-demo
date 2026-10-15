import assert from "node:assert/strict";

const base = process.env.NEXT_SPIKE_URL || "http://127.0.0.1:3000";
const health = await fetch(`${base}/api/health`);
assert.equal(health.status, 200);
const status = await health.json();
assert.equal(status.purpose, "pydantic-next-spike");
assert.equal(status.ready, true);
assert.match(status.node, /^v22\./);

const page = await fetch(base);
assert.equal(page.status, 200);
const html = await page.text();
for (const id of ["page-title", "draft", "draft-value", "count", "increment", "revision"]) {
  assert.ok(html.includes(`id="${id}"`), `Missing rendered element: ${id}`);
}
console.log("Next.js health and rendered page checks passed.");
