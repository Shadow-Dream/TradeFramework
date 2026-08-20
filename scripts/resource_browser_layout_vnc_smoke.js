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

function deleteSession(token) {
  const code = [
    'from engine.control import api as control; from engine.control import auth as trade_auth; import sys',
    'config = control.load_config(".runtime/strategy-control.json")',
    'with trade_auth.connect(config) as connection:',
    '    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (trade_auth.opaque_token_hash(sys.argv[1]),))',
    '    connection.commit()',
  ].join('\n');
  execFileSync('python3', ['-c', code, token]);
}

async function chooseLayout(page, containerId, layout) {
  const current = await page.$eval(
    `#${containerId} .trade-resource-browser-shell`,
    (node) => node.dataset.layout,
  );
  if (current === layout) return;
  await page.click(`#${containerId} .trade-resource-layout-toggle`);
}

async function verifyBrowser(page, spec) {
  const { route, containerId, repository } = spec;
  await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell`, { timeout: 30000 });
  await chooseLayout(page, containerId, 'grid');
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell[data-layout="grid"]`, { timeout: 30000 });

  const firstCard = await page.$(`#${containerId} .file-item-container`);
  assert(firstCard, `No resources or folders are visible in ${repository}`);
  const selectedTitle = await firstCard.evaluate((node) => node.title);
  await firstCard.click();
  await page.waitForSelector(`#${containerId} .file-item-container.file-selected`);

  await chooseLayout(page, containerId, 'list');
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell[data-layout="list"] .files.list`);
  const listState = await page.evaluate((id, repo, title) => {
    const root = document.getElementById(id);
    const selected = root.querySelector('.file-item-container.file-selected');
    return {
      layout: root.querySelector('.trade-resource-browser-shell')?.dataset.layout,
      storage: window.localStorage.getItem(`trade.resource-browser.layout.v1:${repo}`),
      selectedTitle: selected?.title || '',
      typeHeader: getComputedStyle(root.querySelector('.files-header .file-name'), '::after').content,
      dateHeader: getComputedStyle(root.querySelector('.files-header .file-date'), '::before').content,
      statusHeader: getComputedStyle(root.querySelector('.files-header .file-size'), '::before').content,
      typeCells: [...root.querySelectorAll('.files.list .file-item-container')]
        .filter((row) => getComputedStyle(row.querySelector('.file-item'), '::after').content !== 'none').length,
      visibleRows: root.querySelectorAll('.files.list .file-item-container').length,
      statusCells: [...root.querySelectorAll('.files.list .file-item-container > .size')]
        .filter((node) => getComputedStyle(node, '::before').content !== 'none').length,
      toggleDecorated: Boolean(root.querySelector('.trade-resource-layout-toggle[data-layout="list"]')),
      expectedSelectedTitle: title,
      rows: [...root.querySelectorAll('.files.list .file-item-container')].slice(0, 12).map((row) => ({
        title: row.title,
        resourceType: row.dataset.resourceType || '',
        html: row.innerHTML.slice(0, 240),
      })),
      activeFolders: [...root.querySelectorAll('.sb-folders-list-item.active-list-item .sb-folder-name')]
        .map((node) => node.textContent.trim()),
    };
  }, containerId, repository, selectedTitle);
  assert(listState.layout === 'list'
    && listState.storage === 'list'
    && listState.selectedTitle === listState.expectedSelectedTitle
    && listState.typeHeader.includes('Type')
    && listState.dateHeader.includes('Modified / Created')
    && listState.statusHeader.includes('Status')
    && listState.typeCells === listState.visibleRows
    && listState.statusCells === listState.visibleRows
    && listState.toggleDecorated,
  `List layout is incomplete in ${repository}`, listState);
  if (repository === 'data') {
    await page.hover('#dataRepositoryBrowser .files.list .files-header');
    await page.screenshot({ path: '/tmp/trade-resource-browser-list-selected-vnc.png', fullPage: true });
  }

  await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell[data-layout="list"] .files.list`, { timeout: 30000 });
  const persisted = await page.evaluate((id, repo) => ({
    layout: document.querySelector(`#${id} .trade-resource-browser-shell`)?.dataset.layout,
    storage: window.localStorage.getItem(`trade.resource-browser.layout.v1:${repo}`),
  }), containerId, repository);
  assert(persisted.layout === 'list' && persisted.storage === 'list',
    `List layout did not persist in ${repository}`, persisted);

  await chooseLayout(page, containerId, 'grid');
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell[data-layout="grid"] .files.grid`);
  const gridState = await page.evaluate((id, repo) => ({
    layout: document.querySelector(`#${id} .trade-resource-browser-shell`)?.dataset.layout,
    storage: window.localStorage.getItem(`trade.resource-browser.layout.v1:${repo}`),
    toggleDecorated: Boolean(document.querySelector(`#${id} .trade-resource-layout-toggle[data-layout="grid"]`)),
  }), containerId, repository);
  assert(gridState.layout === 'grid' && gridState.storage === 'grid' && gridState.toggleDecorated,
    `Grid layout could not be restored in ${repository}`, gridState);
  return { repository, listState, persisted, gridState };
}

async function main() {
  const session = createSession();
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--disable-gpu'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1720, height: 1000 });
    await page.setCookie(
      { name: 'trade_session', value: session.token, url: BASE, secure: true, httpOnly: true, sameSite: 'Strict' },
      { name: 'trade_csrf', value: session.csrf, url: BASE, secure: true, sameSite: 'Strict' },
    );
    const pageErrors = [];
    const failedResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    const results = [];
    for (const spec of [
      { route: 'data', containerId: 'dataRepositoryBrowser', repository: 'data' },
      { route: 'pipeline', containerId: 'pipelineRepositoryBrowser', repository: 'pipelines' },
      { route: 'modules', containerId: 'moduleRepositoryBrowser', repository: 'modules' },
      { route: 'backtests', containerId: 'backtestResourceBrowser', repository: 'backtest' },
    ]) {
      results.push(await verifyBrowser(page, spec));
    }
    await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#dataRepositoryBrowser .trade-resource-browser-shell');
    await chooseLayout(page, 'dataRepositoryBrowser', 'list');
    await page.waitForSelector('#dataRepositoryBrowser .files.list');
    await page.screenshot({ path: '/tmp/trade-resource-browser-list-vnc.png', fullPage: true });

    assert(!pageErrors.length, 'Browser runtime errors occurred', pageErrors);
    assert(!failedResponses.length, 'Server returned 5xx responses', failedResponses);
    console.log(JSON.stringify({ results, screenshot: '/tmp/trade-resource-browser-list-vnc.png' }, null, 2));
  } finally {
    if (browser) await browser.close();
    deleteSession(session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exitCode = 1;
});
