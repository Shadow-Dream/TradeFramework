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

    await page.goto(`${BASE}/overview`, { waitUntil: 'networkidle2', timeout: 30000 });
    const navigation = await page.evaluate(() => [...document.querySelectorAll('.side-nav-group')].map((group) => ({
      heading: group.querySelector('.side-nav-heading')?.textContent.trim(),
      items: [...group.querySelectorAll('.nav-btn')].map((button) => button.textContent.trim()),
    })));
    assert(JSON.stringify(navigation) === JSON.stringify([
      { heading: 'Overview', items: ['Overview'] },
      { heading: 'Pipeline', items: ['Pipeline', 'Modules'] },
      { heading: 'Backtest', items: ['Dataset', 'Environment', 'Analysis', 'Backtest'] },
    ]), 'Sidebar ownership hierarchy is incorrect', navigation);
    assert(!(await page.$('.nav-btn[data-view="results"]')), 'Result is still exposed as global navigation');
    await page.screenshot({ path: '/tmp/trade-sidebar-ownership.png', fullPage: true });

    await page.goto(`${BASE}/modules`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#moduleRepositoryBrowser .trade-resource-browser-shell');
    const moduleOwnership = await page.evaluate(() => ({
      title: document.querySelector('#modules h2')?.textContent.trim(),
      analysisRepository: Boolean(document.querySelector('#modules #analysisModuleRepositoryBrowser')),
      environmentRepository: Boolean(document.querySelector('#modules #environmentModuleRepositoryBrowser')),
    }));
    assert(moduleOwnership.title === 'Pipeline Module Repository'
      && !moduleOwnership.analysisRepository && !moduleOwnership.environmentRepository,
    'Modules page still owns non-Pipeline Module repositories', moduleOwnership);

    await page.goto(`${BASE}/environment`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      document.querySelector('#environmentRepositoryBrowser .trade-resource-browser-shell')
      && document.querySelector('#environmentModuleRepositoryBrowser .trade-resource-browser-shell')
    ), { timeout: 30000 });
    const environmentOwnership = await page.evaluate(() => ({
      resourceRepository: Boolean(document.querySelector('#environment #environmentRepositoryBrowser')),
      moduleRepository: Boolean(document.querySelector('#environment #environmentModuleRepositoryBrowser')),
      blueprintHidden: document.querySelector('#environmentBlueprintSection').hidden,
      graphMounted: Boolean(document.querySelector('#environmentGraphBuilder').__liteGraphGraph),
    }));
    assert(environmentOwnership.resourceRepository && environmentOwnership.moduleRepository
      && environmentOwnership.blueprintHidden && !environmentOwnership.graphMounted,
    'Environment page ownership is incorrect', environmentOwnership);

    await page.goto(`${BASE}/analysis`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      document.querySelector('#analysisRepositoryBrowser .trade-resource-browser-shell')
      && document.querySelector('#analysisModuleRepositoryBrowser .trade-resource-browser-shell')
      && document.querySelector('#analysisRepositoryBrowser .file-item-container[data-trade-item-id]')
    ), { timeout: 30000 });
    const analysisOwnership = await page.evaluate(() => ({
      resourceRepository: Boolean(document.querySelector('#analysis #analysisRepositoryBrowser')),
      moduleRepository: Boolean(document.querySelector('#analysis #analysisModuleRepositoryBrowser')),
      blueprintHidden: document.querySelector('#analysisBlueprintSection').hidden,
      graphMounted: Boolean(document.querySelector('#analysisGraphBuilder').__liteGraphGraph),
    }));
    assert(analysisOwnership.resourceRepository && analysisOwnership.moduleRepository
      && analysisOwnership.blueprintHidden && !analysisOwnership.graphMounted,
    'Analysis first-level page is not Browser-only', analysisOwnership);
    await page.screenshot({ path: '/tmp/trade-analysis-browser.png', fullPage: true });

    const analysisItemId = await page.$eval(
      '#analysisRepositoryBrowser .file-item-container[data-trade-item-id]',
      (node) => node.dataset.tradeItemId,
    );
    await page.click('#analysisRepositoryBrowser .file-item-container[data-trade-item-id]');
    await page.waitForSelector('#analysisRepositoryBrowser [data-resource-open-action="open"]');
    await page.click('#analysisRepositoryBrowser [data-resource-open-action="open"]');
    await page.waitForFunction(() => (
      location.pathname === '/analysis-blueprint'
      && document.querySelector('#analysisGraphBuilder')?.__liteGraphGraph
    ), { timeout: 30000 });
    const analysisBlueprint = await page.evaluate(() => ({
      selected: new URLSearchParams(location.search).get('analysis'),
      browserHidden: document.querySelector('#analysisRepositorySection').hidden,
      routeBlueprint: document.body.classList.contains('route-blueprint'),
      backLabel: document.querySelector('#analysisGraphBuilder [data-graph-back]')?.textContent.trim(),
      resourceFields: [...document.querySelectorAll('#analysisGraphBuilder [data-graph-resource-field]')]
        .map((field) => field.dataset.graphResourceField),
    }));
    assert(analysisBlueprint.selected === analysisItemId
      && analysisBlueprint.browserHidden
      && analysisBlueprint.routeBlueprint
      && analysisBlueprint.backLabel === 'Back to Analyses'
      && analysisBlueprint.resourceFields.join('|') === 'analysisId|name',
    'Analysis Open did not enter the independent Blueprint route', { analysisItemId, analysisBlueprint });
    await page.click('#analysisGraphBuilder [data-graph-back]');
    await page.waitForFunction(() => location.pathname === '/analysis');

    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      window.__tradeState?.repositoryCatalogs?.backtest?.items?.length > 0
      && document.querySelector('#backtestAnalysisSelect')?.value
    ),
      { timeout: 30000 });
    const backtestAnalysisKey = await page.$eval('#backtestAnalysisSelect', (select) => select.value);
    await page.click('#chainAnalysisDetails');
    await page.waitForFunction(() => (
      location.pathname === '/analysis-blueprint'
      && document.querySelector('#analysisGraphBuilder')?.__liteGraphGraph
    ), { timeout: 30000 });
    const backtestAnalysisBlueprint = await page.evaluate(() => ({
      selected: new URLSearchParams(location.search).get('analysis'),
      backLabel: document.querySelector('#analysisGraphBuilder [data-graph-back]')?.textContent.trim(),
    }));
    assert(backtestAnalysisBlueprint.selected === backtestAnalysisKey
      && backtestAnalysisBlueprint.backLabel === 'Back to Backtest Entry',
    'Backtest Analysis Details did not open the selected Analysis Blueprint', { backtestAnalysisKey, backtestAnalysisBlueprint });
    await page.click('#analysisGraphBuilder [data-graph-back]');
    await page.waitForFunction(() => location.pathname === '/backtests');
    const resultItem = await page.evaluate(() => (
      window.__tradeState.repositoryCatalogs.backtest.items.find((item) => item.visualizable && item.status !== 'archived') || null
    ));
    assert(resultItem?.backtestId, 'No visualizable Backtest Result is available');
    await page.evaluate((itemId) => {
      [...document.querySelectorAll('#backtestResourceBrowser .file-item-container[data-trade-item-id]')]
        .find((node) => node.dataset.tradeItemId === itemId)?.click();
    }, resultItem.itemId);
    await page.waitForSelector('#backtestResourceBrowser [data-resource-open-action="open"]');
    await page.click('#backtestResourceBrowser [data-resource-open-action="open"]');
    await page.waitForFunction((backtestId) => (
      location.pathname === '/result'
      && new URLSearchParams(location.search).get('backtestId') === backtestId
      && window.__tradeState?.selectedBacktest?.backtestId === backtestId
    ), { timeout: 30000 }, resultItem.backtestId);
    const resultRoute = await page.evaluate(() => ({
      sideHidden: getComputedStyle(document.querySelector('.side')).display === 'none',
      selectorPresent: Boolean(document.querySelector('#resultBacktest')),
      backButton: document.querySelector('#backFromResultBtn')?.textContent.trim(),
    }));
    assert(resultRoute.sideHidden && !resultRoute.selectorPresent && resultRoute.backButton === 'Back to Backtest',
      'Result is not a Backtest-owned dedicated page', resultRoute);

    assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
    assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);
    await page.screenshot({ path: '/tmp/trade-navigation-ownership.png', fullPage: true });
    console.log(JSON.stringify({ navigation, moduleOwnership, environmentOwnership, analysisOwnership, analysisBlueprint, backtestAnalysisBlueprint, resultRoute }, null, 2));
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
