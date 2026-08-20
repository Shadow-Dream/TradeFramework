const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'https://trade.duckduckrun.com';

function assert(condition, message, payload) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

function createSession() {
  const code = [
    'import json, secrets, time',
    'from engine.control import api as control; from engine.control import auth as trade_auth',
    'config = control.load_config(".runtime/strategy-control.json")',
    'trade_auth.ensure_default_user(config)',
    'token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), int(time.time())',
    'with trade_auth.connect(config) as connection:',
    '    user = connection.execute("SELECT user_id FROM users WHERE status = ? ORDER BY created_at LIMIT 1", ("active",)).fetchone()',
    '    connection.execute("INSERT INTO sessions (token_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)", (trade_auth.opaque_token_hash(token), user["user_id"], trade_auth.opaque_token_hash(csrf), now, now + 3600, now))',
    '    connection.commit()',
    'print(json.dumps({"token": token, "csrf": csrf}))',
  ].join('\n');
  return JSON.parse(execFileSync('python3', ['-c', code], { encoding: 'utf8' }));
}

async function main() {
  const session = createSession();
  const browser = await puppeteer.launch({
    headless: false,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  await page.setCookie(
    { name: 'trade_session', value: session.token, url: BASE, secure: true, httpOnly: true, sameSite: 'Strict' },
    { name: 'trade_csrf', value: session.csrf, url: BASE, secure: true, sameSite: 'Strict' },
  );
  const pageErrors = [];
  const serverErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => {
    if (response.status() >= 500 && !response.url().includes('vnc-forbidden-probe.txt')) {
      serverErrors.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(() => (window.__tradeState?.datasetWorkspaces || []).length > 0);
  const workspace = await page.evaluate(() => window.__tradeState.datasetWorkspaces.find((item) => item.status === 'draft') || window.__tradeState.datasetWorkspaces[0]);
  assert(workspace?.workspaceId && workspace?.workspacePath, 'No Dataset Workspace is available for Jupyter isolation testing', workspace);
  const opened = await page.evaluate(async (workspaceId) => {
    const response = await fetch(`/api/data/workspaces/${encodeURIComponent(workspaceId)}/jupyter`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.__tradeAuth.csrfToken },
      body: '{}',
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }, workspace.workspaceId);
  assert(/\/jupyter\/w\/dataset-[^/]+\/lab\?reset$/.test(opened.url), 'Workspace did not receive an isolated Jupyter prefix', opened);
  assert(!opened.url.includes('/tree/Datasets/'), 'Workspace URL still exposes the Dataset collection parent', opened.url);
  const parsed = new URL(opened.url);
  const marker = parsed.pathname.lastIndexOf('/lab');
  const apiBase = parsed.pathname.slice(0, marker + 1);

  const checks = await page.evaluate(async ({ base, workspaceId }) => {
    const request = async (path, options = {}) => {
      const response = await fetch(base + path, { credentials: 'same-origin', ...options });
      const text = await response.text();
      let payload = null;
      try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
      return { status: response.status, payload };
    };
    const root = await request('api/contents');
    const create = await request('api/contents/vnc-delete-probe.txt', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'file', format: 'text', content: 'created in isolated Workspace' }),
    });
    const remove = await request('api/contents/vnc-delete-probe.txt', { method: 'DELETE' });
    const parent = await request('api/contents/..');
    const sourceWrite = await request('api/contents/dataset1/vnc-forbidden-probe.txt', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'file', format: 'text', content: 'must fail' }),
    });
    return { workspaceId, root, create, remove, parent, sourceWrite };
  }, { base: apiBase, workspaceId: workspace.workspaceId });

  const names = (checks.root.payload?.content || []).map((item) => item.name);
  assert(checks.root.status === 200, 'Unable to list isolated Workspace root', checks.root);
  assert(!names.includes('Datasets') && !names.includes('Modules'), 'Workspace root exposes global collections', names);
  assert(!names.some((name) => name !== workspace.workspaceId && name.endsWith('-workspace')), 'Workspace root exposes a sibling Workspace', names);
  assert([200, 201].includes(checks.create.status), 'Unable to create an owned Workspace file', checks.create);
  assert([200, 204].includes(checks.remove.status), 'Unable to delete an owned Workspace file', checks.remove);
  assert(!Array.isArray(checks.parent.payload?.content), 'Jupyter contents API exposed a parent directory listing', checks.parent);
  assert(checks.sourceWrite.status >= 400, 'Jupyter wrote into a read-only source Dataset', checks.sourceWrite);
  assert(!fs.existsSync(`${workspace.workspacePath}/vnc-delete-probe.txt`), 'Deleted probe remains on disk');

  const lab = await browser.newPage();
  await lab.setViewport({ width: 1600, height: 1000 });
  await lab.goto(opened.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await lab.waitForSelector('.jp-DirListing-content, .jp-FileBrowser', { timeout: 30000 });
  await new Promise((resolve) => setTimeout(resolve, 2500));
  await lab.screenshot({ path: '/tmp/trade-jupyter-workspace-isolation-vnc.png', fullPage: true });
  assert(pageErrors.length === 0, 'Trade page errors occurred', pageErrors);
  assert(serverErrors.length === 0, 'Trade/Jupyter server errors occurred', serverErrors);
  await browser.close();
  console.log(JSON.stringify({ workspace: workspace.workspaceId, openedUrl: opened.url, names, statuses: {
    root: checks.root.status, create: checks.create.status, remove: checks.remove.status,
    parent: checks.parent.status, sourceWrite: checks.sourceWrite.status,
  }, pageErrors, serverErrors }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
