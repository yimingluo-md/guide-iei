import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/workbench-connection.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { monitorWorkbenchConnection } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const healthy = { service: "GUIDE-IEI", instance_id: "one", desktop_app: true, quitting: false };
const pause = (ms = 1) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate) {
  const end = Date.now() + 2000;
  while (!predicate()) { assert(Date.now() < end, "Monitor did not reach expected state"); await pause(); }
}
function fixture(t, options = {}) {
  const requests = [], states = [];
  const monitor = monitorWorkbenchConnection((id, signal) => new Promise((resolve, reject) => {
    requests.push({ id, signal, resolve, reject });
    if (options.rejectOnAbort) signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }), (state) => states.push(state), { retryMs: 5, timeoutMs: 500, ...options });
  t.after(() => monitor.dispose());
  return { monitor, states, requests, async next(index) { await until(() => requests.length > index); return requests[index]; } };
}

test("temporary disconnection never means Quit and clears on reconnect without reload", async (t) => {
  const f = fixture(t);
  assert.equal(f.requests[0].id, "");
  f.requests[0].resolve(healthy);
  const watch = await f.next(1);
  assert.equal(watch.id, "one");
  watch.reject(new Error("connection reset"));
  const reconnect = await f.next(2);
  assert.equal(f.states.at(-1).phase, "reconnecting");
  assert.equal(reconnect.id, "", "Reconnection should not wait for a long-poll heartbeat");
  reconnect.resolve(healthy);
  await until(() => f.states.at(-1).phase === "running");
  assert(!f.states.some((state) => state.phase === "stopped"));
});

test("confirmed external Quit becomes stopped, then a new instance reconnects", async (t) => {
  const f = fixture(t);
  f.requests[0].resolve(healthy);
  (await f.next(1)).resolve({ ...healthy, quitting: true });
  const closing = await f.next(2);
  assert.equal(f.states.at(-1).phase, "quitting");
  closing.reject(new Error("closed"));
  const reopened = await f.next(3);
  assert.equal(f.states.at(-1).phase, "stopped");
  reopened.resolve({ ...healthy, instance_id: "two" });
  await until(() => f.states.at(-1).phase === "running");
  assert.equal(f.states.at(-1).instanceId, "two");
});

test("an accepted browser Quit supersedes stale in-flight healthy responses", async (t) => {
  const f = fixture(t);
  f.requests[0].resolve(healthy);
  const stale = await f.next(1);
  f.monitor.acknowledgeQuit("one");
  assert(stale.signal.aborted);
  stale.resolve(healthy); // Deliberately simulate a transport ignoring abort.
  const closing = await f.next(2);
  assert.equal(f.states.at(-1).phase, "quitting");
  closing.resolve(healthy); // A cached/late response also cannot revoke Quit.
  (await f.next(3)).reject(new Error("closed"));
  await until(() => f.states.at(-1).phase === "stopped");
});

test("initial failure or an unrelated listener cannot falsely confirm Quit", async (t) => {
  const f = fixture(t);
  f.requests[0].reject(new Error("offline"));
  (await f.next(1)).resolve({ service: "another app", instance_id: "one", quitting: true });
  (await f.next(2)).resolve({ ...healthy, instance_id: null, quitting: true });
  await f.next(3);
  assert(f.states.every((state) => state.phase === "reconnecting"));
});

test("a service restart is reconnecting, not a confirmed Quit", async (t) => {
  const f = fixture(t);
  f.requests[0].resolve(healthy);
  (await f.next(1)).resolve({ ...healthy, restarting: true });
  (await f.next(2)).reject(new Error("restart gap"));
  (await f.next(3)).resolve({ ...healthy, instance_id: "restarted" });
  await until(() => f.states.at(-1).phase === "running");
  assert(!f.states.some((state) => ["quitting", "stopped"].includes(state.phase)));
});

test("hung requests time out and disposal cancels requests and retry timers", async (t) => {
  const f = fixture(t, { timeoutMs: 10, rejectOnAbort: true });
  await f.next(1);
  assert(f.requests[0].signal.aborted);
  assert.equal(f.states.at(-1).phase, "reconnecting");
  f.monitor.dispose();
  assert(f.requests[1].signal.aborted);
  const count = f.states.length;
  await pause(30);
  assert.equal(f.requests.length, 2);
  assert.equal(f.states.length, count);
});

test("legacy status-only backends use delayed polling, not a tight loop", async (t) => {
  const f = fixture(t, { retryMs: 50 });
  f.requests[0].resolve({ ...healthy, watch_supported: false });
  await until(() => f.states.at(-1)?.phase === "running");
  assert.equal(f.requests.length, 1);
  await f.next(1);
});

test("shutdown notice keeps the review mounted and contains no browser-close or persistence commands", async () => {
  const ui = await readFile(new URL("../app/VariantWorkbench.tsx", import.meta.url), "utf8");
  const notice = await readFile(new URL("../app/WorkbenchConnectionNotice.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(ui, /if \(quitMessage\) return|setQuitMessage/);
  assert.match(ui, /<WorkbenchConnectionNotice connection=\{connection\} \/>/);
  assert.match(notice, /role="status" aria-live="polite"/);
  assert.match(notice, /Your loaded review is preserved/);
  assert.doesNotMatch(source + notice, /localStorage|sessionStorage|location\.reload|window\.close/);
});
