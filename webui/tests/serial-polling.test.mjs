import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/serial-polling.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { startSerialPolling } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const pause = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate) {
  const end = Date.now() + 2000;
  while (!predicate()) { assert(Date.now() < end); await pause(1); }
}

test("slow refresh never overlaps; only first batch is initial", async (t) => {
  const calls = [], releases = [];
  const stop = startSerialPolling((initial) => {
    calls.push(initial);
    return new Promise((resolve) => releases.push(resolve));
  }, assert.fail, 5);
  t.after(stop);
  await pause();
  assert.deepEqual(calls, [true]);
  releases.shift()();
  await until(() => calls.length === 2);
  assert.deepEqual(calls, [true, false]);
  stop(); releases.shift()();
  await pause();
  assert.equal(calls.length, 2);
});

test("early failure waits for slow sibling request before retrying", async (t) => {
  let resolveSlow, calls = 0;
  const errors = [];
  const stop = startSerialPolling(async () => {
    calls++;
    const results = await Promise.allSettled([
      Promise.reject(new Error("jobs unavailable")),
      new Promise((resolve) => { resolveSlow = resolve; }),
    ]);
    if (results.some((r) => r.status === "rejected")) throw new Error("batch failed");
  }, (e) => errors.push(e.message), 5);
  t.after(stop);
  await pause();
  assert.equal(calls, 1); assert.deepEqual(errors, []);
  resolveSlow();
  await until(() => calls === 2);
  assert.deepEqual(errors, ["batch failed"]);
  stop(); resolveSlow();
  await pause();
  assert.equal(calls, 2); assert.equal(errors.length, 1);
});

test("annotation panel uses serial polling and waits for every status request", async () => {
  const component = await readFile(new URL("../app/VariantWorkbench.tsx", import.meta.url), "utf8");
  const panel = component.slice(component.indexOf("function AnnotationPanel("), component.indexOf("function chooseFiles(", component.indexOf("function AnnotationPanel(")));
  assert.match(panel, /startSerialPolling\(refresh/);
  assert.match(panel, /Promise\.allSettled\(/);
  assert.doesNotMatch(panel, /setInterval/);
  const poll = panel.slice(panel.indexOf("async function refresh"), panel.indexOf("function chooseFiles"));
  assert.match(poll, /setConnectionError\(""\)/);
  assert.doesNotMatch(poll, /setServiceError\(/, "polling must not overwrite an action error");
});

test("setup UI exposes essential jobs and disables unavailable WGS status", async () => {
  const source = await readFile(new URL("../app/VariantWorkbench.tsx", import.meta.url), "utf8");
  assert.match(source, /latestResourceJobs\.get\("essential_setup"\)/);
  assert.match(source, /Essential setup log/);
  assert.match(source, /essentialJob\.log/);
  assert.match(source, /disabled=\{!wgsSetupKnown \|\| resourceSetupBusy/);
  assert.match(source, /Cannot check datasets — resolve the configuration error above/);
  assert.match(source, /wgsSetupKnown = !capabilities\?\.annotation_profile.error/);
  const service = await readFile(new URL("../app/local-service.ts", import.meta.url), "utf8");
  const ids = service.slice(service.indexOf("export type ResourceDownloadJob"), service.indexOf("operation?:", service.indexOf("export type ResourceDownloadJob")));
  assert.match(ids, /"screen_context"/);
});
