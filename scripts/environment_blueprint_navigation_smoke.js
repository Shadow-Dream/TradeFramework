const { execFileSync } = require('node:child_process');
const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'http://127.0.0.1:30808';

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

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

async function main() {
  const session = createSession();
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--disable-gpu'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000 });
    await page.setExtraHTTPHeaders({ 'X-Forwarded-Proto': 'https' });
    await page.setCookie(
      { name: 'trade_session', value: session.token, url: BASE, httpOnly: true },
      { name: 'trade_csrf', value: session.csrf, url: BASE },
    );
    const pageErrors = [];
    const failedResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    await page.goto(`${BASE}/environment`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      window.__tradeState?.repositoryCatalogs?.environments?.items?.length > 0
      && document.querySelector('#environmentRepositoryBrowser .file-item-container[data-trade-item-id]')
    ), { timeout: 30000 });
    const browserAudit = await page.evaluate(() => ({
      path: location.pathname,
      resourceCount: window.__tradeState.repositoryCatalogs.environments.items.length,
      repositoryVisible: !document.querySelector('#environmentRepositorySection').hidden,
      blueprintHidden: document.querySelector('#environmentBlueprintSection').hidden,
      graphMounted: Boolean(document.querySelector('#environmentGraphBuilder').__liteGraphGraph),
    }));
    assert(browserAudit.path === '/environment'
      && browserAudit.resourceCount > 0
      && browserAudit.repositoryVisible
      && browserAudit.blueprintHidden
      && !browserAudit.graphMounted,
    'Environment first-level route is not Browser-only', browserAudit);
    await page.screenshot({ path: '/tmp/trade-environment-browser.png', fullPage: true });

    const resourceSelector = '#environmentRepositoryBrowser .file-item-container[data-trade-item-id]';
    const openedItemId = await page.$eval(resourceSelector, (node) => node.dataset.tradeItemId);
    await page.click(resourceSelector);
    await page.waitForSelector('#environmentRepositoryBrowser [data-resource-open-action="open"]');
    const openButtonAudit = await page.$eval('#environmentRepositoryBrowser [data-resource-open-action="open"]', (button) => ({
      disabled: button.disabled,
      text: button.textContent.trim(),
    }));
    await page.click('#environmentRepositoryBrowser [data-resource-open-action="open"]');
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const postClickAudit = await page.evaluate(() => ({
      path: location.pathname,
      search: location.search,
      health: document.querySelector('#health')?.textContent?.trim(),
      browserError: document.querySelector('#environmentRepositoryBrowser .trade-resource-error')?.textContent?.trim() || '',
      blueprintHidden: document.querySelector('#environmentBlueprintSection').hidden,
    }));
    assert(postClickAudit.path === '/environment-blueprint',
      'Environment Browser Open did not navigate to the Blueprint route', { openedItemId, openButtonAudit, postClickAudit, pageErrors });
    await page.waitForFunction(() => (
      location.pathname === '/environment-blueprint'
      && document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph
    ), { timeout: 30000 });
    const openAudit = await page.evaluate(() => ({
      path: location.pathname,
      selected: new URLSearchParams(location.search).get('environment'),
      nodes: document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length || 0,
      browserHidden: document.querySelector('#environmentRepositorySection').hidden,
      backLabel: document.querySelector('#environmentGraphBuilder [data-graph-back]')?.textContent?.trim(),
      routeBlueprint: document.body.classList.contains('route-blueprint'),
      toolbarRows: document.querySelectorAll('#environmentGraphBuilder .alpha-litegraph-toolbar-row').length,
      actionGroups: document.querySelectorAll('#environmentGraphBuilder .alpha-litegraph-action-group, #environmentGraphBuilder .alpha-litegraph-toolbar-group').length,
      resourceFields: [...document.querySelectorAll('#environmentGraphBuilder [data-graph-resource-field]')]
        .map((field) => field.dataset.graphResourceField),
      externalToolbar: Boolean(document.querySelector('#environmentBlueprintSection > .pipeline-context-bar')),
      versionOptions: document.querySelectorAll('#environmentGraphBuilder [data-graph-version] option').length,
    }));
    assert(openAudit.selected === openedItemId
      && openAudit.browserHidden
      && openAudit.backLabel === 'Back to Environments'
      && openAudit.routeBlueprint
      && openAudit.toolbarRows === 2
      && openAudit.actionGroups === 4
      && openAudit.resourceFields.join('|') === 'environmentId|name'
      && !openAudit.externalToolbar
      && openAudit.versionOptions > 0,
    'Environment Browser Open did not enter the matching Blueprint', { openedItemId, openAudit });
    await page.screenshot({ path: '/tmp/trade-environment-blueprint.png', fullPage: true });

    await page.click('#environmentGraphBuilder [data-graph-back]');
    await page.waitForFunction(() => (
      location.pathname === '/environment'
      && !document.querySelector('#environmentRepositorySection').hidden
      && document.querySelector('#environmentBlueprintSection').hidden
      && !document.querySelector('#environmentGraphBuilder').__liteGraphGraph
    ), { timeout: 30000 });

    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => document.querySelector('#backtestEnvironmentSelect')?.value, { timeout: 30000 });
    const backtestEnvironment = await page.$eval('#backtestEnvironmentSelect', (node) => node.value);
    await page.click('#chainEnvironmentDetails');
    await page.waitForFunction(() => (
      location.pathname === '/environment-blueprint'
      && document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length > 0
    ), { timeout: 30000 });
    const detailsAudit = await page.evaluate(() => ({
      selected: new URLSearchParams(location.search).get('environment'),
      backLabel: document.querySelector('#environmentGraphBuilder [data-graph-back]')?.textContent?.trim(),
      loadedVersion: document.querySelector('#environmentGraphBuilder [data-graph-version]')?.value,
      versions: [...document.querySelectorAll('#environmentGraphBuilder [data-graph-version] option')]
        .map((option) => option.value),
      resourceFieldCount: document.querySelectorAll('#environmentGraphBuilder [data-graph-resource-field]').length,
    }));
    assert(detailsAudit.selected === backtestEnvironment
      && detailsAudit.backLabel === 'Back to Backtest Entry'
      && detailsAudit.loadedVersion === backtestEnvironment.split('::')[1]
      && detailsAudit.resourceFieldCount === 2,
    'Backtest Details did not open the selected Environment Blueprint', { backtestEnvironment, detailsAudit });
    await page.screenshot({ path: '/tmp/trade-environment-blueprint-populated.png', fullPage: true });
    await page.click('#environmentGraphBuilder [data-graph-back]');
    await page.waitForFunction(() => location.pathname === '/backtests', { timeout: 30000 });

    await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]');
    await page.$eval('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]', (node) => {
      node.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    });
    await page.waitForFunction(() => location.pathname === '/pipeline/builder', { timeout: 30000 });
    await page.waitForSelector('#pipelineStageGrid [data-open-alpha-details="signal"]');
    await page.click('#pipelineStageGrid [data-open-alpha-details="signal"]');
    await page.waitForFunction(() => (
      location.pathname === '/signal-blueprint'
      && document.querySelector('#alphaGraphBuilder')?.__liteGraphGraph
    ), { timeout: 30000 });
    const signalAudit = await page.evaluate(() => ({
      path: location.pathname,
      routeBlueprint: document.body.classList.contains('route-blueprint'),
      graphVisible: document.querySelector('#alphaGraphBuilder')?.getBoundingClientRect().height > 0,
      backLabel: document.querySelector('#alphaGraphBuilder [data-graph-back]')?.textContent?.trim(),
    }));
    assert(signalAudit.path === '/signal-blueprint'
      && signalAudit.routeBlueprint
      && signalAudit.graphVisible
      && signalAudit.backLabel === 'Back',
    'Shared Blueprint route layout broke the Signal Details page', signalAudit);
    await page.click('#alphaGraphBuilder [data-graph-back]');
    await page.waitForFunction(() => location.pathname === '/pipeline/builder', { timeout: 30000 });

    assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
    assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);
    console.log(JSON.stringify({
      browserAudit,
      openedItemId,
      openAudit,
      backtestEnvironment,
      detailsAudit,
      signalAudit,
      pageErrors,
      failedResponses,
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    deleteSession(session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
