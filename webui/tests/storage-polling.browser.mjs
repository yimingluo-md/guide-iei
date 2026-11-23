// Run with UI_TEST_PORT against the local UI; all service responses are synthetic.
import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';

const origin = `http://127.0.0.1:${Number(process.env.UI_TEST_PORT || 3000)}`;
const browser = await puppeteer.launch({ headless: true, executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(predicate, label) {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await sleep(50); }
  throw new Error(`Timed out waiting for ${label}`);
}
const location = { id: 'data', path: '/new', active_path: '/old', exists: true, writable: true, available: true, free_bytes: 1000, total_bytes: 2000, warning: '', uses_default: false, follows_data_root: false, restart_required: true, used_bytes: 50, included_with_data: false, windows_path: null };
const config = { registry_path: '/registry', locations: [location], annotation_bytes: 0, active_data_root: '/old', active_annotation_root: '/a', active_temporary_root: '/t' };
const stats = { state_dir: '/old', workspace_dir: '/old', locations: {}, total_bytes: 50, datasets: 0, current_datasets: 0, callsets: 0, versions: 0, managed_unique_files: 0, database_page_bytes: 0, database_reclaimable_bytes: 0, annotation_bytes: 0, storage_configuration: config };
const job = status => ({ id: 'job', kind: 'data', source: '/old', destination: '/new', status, progress: status === 'succeeded' ? 100 : 25, bytes_total: 100, bytes_copied: 25, message: status, error: '', restart_required: true });
try {
  const page = await browser.newPage();
  let mode = 'running', polls = 0, health = 0, restarting = false, releasePoll, releaseRestart, releaseHealth;
  await page.setRequestInterception(true);
  page.on('dialog', dialog => dialog.accept());
  page.on('request', request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return url.origin === origin ? request.continue() : request.abort();
    const headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
    const reply = (body, status = 200) => request.respond({ status, headers, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (request.method() === 'OPTIONS') return request.respond({ status: 204, headers });
    if (url.pathname === '/api/storage') return reply(stats);
    if (url.pathname === '/api/storage/locations') {
      polls++;
      if (polls === 1) { releasePoll = () => reply(config); return; }
      if (polls === 2) return reply({ error: 'synthetic refresh failure' }, 503);
      return reply(config);
    }
    if (url.pathname === '/api/storage/migrations') return reply({ jobs: [job(mode)] });
    if (url.pathname === '/api/service/restart') { restarting = true; releaseRestart = () => reply({ restarting: true }); return; }
    if (url.pathname === '/api/health') { if (!restarting) return reply({ ok: true }); health++; releaseHealth = () => reply({ ok: true }); return; }
    return reply({}, 503);
  });
  await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => document.querySelector('.nav-item'));
  await sleep(1500);
  const navigate = label => page.evaluate(label => [...document.querySelectorAll('.nav-item')].find(button => button.textContent.trim() === label).click(), label);
  const storage = () => navigate('Storage');
  const away = () => navigate('Sample library');
  await storage();
  await page.waitForFunction(() => document.querySelector('.storage-migration.running'));
  await page.waitForFunction(() => document.querySelector('.storage-page'));
  await waitFor(() => releasePoll, 'first migration poll');
  await sleep(2200);
  assert.equal(polls, 1, 'slow refresh must not overlap a subsequent poll');
  await releasePoll();
  await page.waitForFunction(() => document.querySelector('.storage-page')?.textContent.includes('synthetic refresh failure'));
  mode = 'succeeded';
  await page.waitForFunction(() => document.querySelector('.storage-migration.succeeded'));
  const completedPolls = polls;
  await sleep(1300);
  assert.equal(polls, completedPolls, 'polling stops after migration completes');
  assert.ok(!(await page.$eval('.storage-page', el => el.textContent)).includes('synthetic refresh failure'));
  await page.$eval('.storage-restart-alert button', button => button.click());
  await waitFor(() => releaseRestart, 'restart request');
  await away();
  await releaseRestart();
  await sleep(1300);
  assert.equal(health, 0, 'a restart response after navigation must not start health polling');
  await storage();
  await page.waitForSelector('.storage-restart-alert button');
  releaseRestart = undefined;
  await page.$eval('.storage-restart-alert button', button => button.click());
  await waitFor(() => releaseRestart, 'restart request');
  // The second restart response is now owned by the remounted panel.
  await releaseRestart();
  await waitFor(() => releaseHealth, 'health request');
  await away();
  await releaseHealth();
  await sleep(1300);
  assert.equal(health, 1, 'an in-flight health response must not continue polling after navigation');
  await storage();
  await page.waitForSelector('.storage-restart-alert button');
  assert.ok(!(await page.$eval('.storage-page', el => el.textContent)).includes('service restarted'));
  releaseRestart = undefined; releaseHealth = undefined;
  await page.$eval('.storage-restart-alert button', button => button.click());
  await waitFor(() => releaseRestart, 'third restart request');
  await releaseRestart();
  await waitFor(() => releaseHealth, 'second health request');
  await releaseHealth();
  await page.waitForFunction(() => document.querySelector('.storage-page')?.textContent.includes('Workbench service restarted'));
  await away();
  mode = 'running'; polls = 0; releasePoll = undefined;
  await storage();
  await waitFor(() => releasePoll, 'obsolete migration poll');
  await away();
  mode = 'succeeded';
  await storage();
  await page.waitForFunction(() => document.querySelector('.storage-migration.succeeded'));
  await releasePoll();
  await sleep(1300);
  assert.ok(await page.$('.storage-migration.succeeded'), 'an obsolete poll cannot overwrite the reopened panel');
  console.log('PASS: serialized polling, error recovery, out-of-order navigation, and restart lifecycle');
} finally { await browser.close(); }
