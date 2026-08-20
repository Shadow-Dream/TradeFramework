const { execFileSync } = require('node:child_process');
const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'http://127.0.0.1:30808';
const DATASET_ID = process.env.TRADE_TEST_DATASET_ID || '';

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
  const browser = await puppeteer.launch({
    headless: process.env.TRADE_TEST_HEADFUL === '1' ? false : true,
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

  await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction((datasetId) => (
    (window.__tradeState?.backtests || []).some((row) => (
      row.visualizable && (!datasetId || row.datasetId === datasetId)
    ))
  ), { timeout: 30000 }, DATASET_ID);
  const backtestId = await page.evaluate((datasetId) => (
    (window.__tradeState?.backtests || []).find((row) => (
      row.visualizable && (!datasetId || row.datasetId === datasetId)
    ))?.backtestId || ''
  ), DATASET_ID);
  assert(backtestId, 'No visualizable backtest is available');
  await page.goto(`${BASE}/result?backtestId=${encodeURIComponent(backtestId)}`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction((expected) => window.__tradeState?.selectedBacktest?.backtestId === expected,
    { timeout: 30000 }, backtestId);
  const catalogAudit = await page.evaluate(() => {
    const catalog = window.TradeChartCore.visualizerCatalog(
      window.__tradeState.selectedBacktest,
      window.__tradeState.selectedBacktest.visualization,
    );
    const candle = catalog.find((item) => item.id === 'ohlc.candles');
    const line = catalog.find((item) => item.id === 'series.line');
    const candlePaths = (candle?.optionMap?.dataKey || []).map((item) => item.value);
    return {
      definitionCount: catalog.length,
      ohlcPath: candlePaths[0] || '',
      candlePaths,
      linePaths: (line?.optionMap?.dataKey || []).map((item) => item.value),
    };
  });

  assert(catalogAudit.definitionCount === 6, 'Visualizer definitions were not loaded from the service', catalogAudit);
  assert(catalogAudit.ohlcPath, 'Selected result has no structured OHLC DataKey', catalogAudit);
  assert(catalogAudit.candlePaths.includes(catalogAudit.ohlcPath),
    'Candles rejected a compatible structured object DataKey', catalogAudit);
  assert(catalogAudit.linePaths.includes(`${catalogAudit.ohlcPath}.close`)
    && !catalogAudit.linePaths.includes(catalogAudit.ohlcPath),
  'Line did not distinguish a numeric leaf from its object parent', catalogAudit);
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
  assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);

  await page.screenshot({ path: '/tmp/trade-result-schema-labels-vnc.png', fullPage: true });
  await browser.close();
  deleteSession(session.token);
  console.log(JSON.stringify({ backtestId, catalogAudit, pageErrors, failedResponses }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
