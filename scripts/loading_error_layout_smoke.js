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
    const delays = { summary: true, pipeline: false, composition: false };
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      const url = request.url();
      const delayed = (delays.summary && url.includes('/api/summary'))
        || (delays.pipeline && url.includes('/api/pipelines'))
        || (delays.composition && url.includes('/api/backtest-submissions/prepare'));
      if (delayed) setTimeout(() => request.continue(), 900);
      else request.continue();
    });

    await page.goto(`${BASE}/overview`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForFunction(() => (
      document.querySelector('#overview')?.classList.contains('active')
      && !document.querySelector('#overview > .view-loading')?.hidden
    ), { timeout: 30000 });
    const overviewLoading = await page.evaluate(() => ({
      value: document.querySelector('#serviceStatusValue')?.textContent.trim(),
      loadingIndicator: document.querySelector('#serviceStatusIndicator')?.classList.contains('loading'),
      downIndicator: document.querySelector('#serviceStatusIndicator')?.classList.contains('down'),
      loaderText: document.querySelector('#overview > .view-loading')?.textContent.trim(),
    }));
    assert(overviewLoading.value === 'Loading' && overviewLoading.loadingIndicator
      && !overviewLoading.downIndicator && overviewLoading.loaderText === 'Loading Overview…',
    'Overview presents loading as an outage', overviewLoading);
    delays.summary = false;
    await page.waitForFunction(() => document.querySelector('#serviceStatusValue')?.textContent.trim() === 'Active',
      { timeout: 30000 });

    delays.pipeline = true;
    await page.click('.nav-btn[data-view="pipeline"]');
    await page.waitForFunction(() => (
      location.pathname === '/pipeline'
      && !document.querySelector('#pipeline > .view-loading')?.hidden
    ), { timeout: 30000 });
    const pipelineLoading = await page.evaluate(() => ({
      loaderText: document.querySelector('#pipeline > .view-loading')?.textContent.trim(),
      loadErrorHidden: document.querySelector('#pipelineLoadError')?.hidden,
      loadError: document.querySelector('#pipelineLoadError')?.textContent.trim(),
      saveErrorHidden: document.querySelector('#pipelineSaveError')?.hidden,
      saveError: document.querySelector('#pipelineSaveError')?.textContent.trim(),
    }));
    assert(pipelineLoading.loaderText === 'Loading Pipeline…'
      && pipelineLoading.loadErrorHidden && !pipelineLoading.loadError
      && pipelineLoading.saveErrorHidden && !pipelineLoading.saveError,
    'Pipeline renders loading constraints as red errors', pipelineLoading);
    delays.pipeline = false;
    await page.waitForFunction(() => document.querySelector('#pipeline > .view-loading')?.hidden,
      { timeout: 30000 });

    delays.composition = true;
    await page.click('.nav-btn[data-view="backtests"]');
    await page.waitForFunction(() => (
      location.pathname === '/backtests'
      && document.querySelector('#backtestCompositionStatus')?.dataset.state === 'pending'
    ), { timeout: 30000 });
    const pendingComposition = await page.evaluate(() => ({
      inSubmitPanel: document.querySelector('#backtestCompositionStatus')?.closest('#backtestSubmitPanel')?.id === 'backtestSubmitPanel',
      pending: document.querySelector('#backtestCompositionStatus')?.dataset.state,
      errorStyled: document.querySelector('#backtestCompositionStatus')?.classList.contains('dialog-error'),
      panelState: document.querySelector('#backtestSubmitPanel')?.dataset.state,
      submitDisabled: document.querySelector('#runBacktestBtn')?.disabled,
      submitText: document.querySelector('#runBacktestBtn')?.textContent.trim(),
      submitLoading: document.querySelector('#runBacktestBtn')?.classList.contains('button-loading'),
      extraErrorHidden: document.querySelector('#backtestEntryError')?.hidden,
    }));
    assert(pendingComposition.inSubmitPanel && pendingComposition.pending === 'pending'
      && pendingComposition.panelState === 'pending' && !pendingComposition.errorStyled
      && pendingComposition.submitDisabled && pendingComposition.submitText === 'Checking…'
      && pendingComposition.submitLoading
      && pendingComposition.extraErrorHidden,
    'Backtest pending validation is not an explicit locked submission state', pendingComposition);
    delays.composition = false;
    await page.waitForFunction(() => document.querySelector('#backtestCompositionStatus')?.dataset.state !== 'pending',
      { timeout: 30000 });
    const finalComposition = await page.evaluate(() => {
      const status = document.querySelector('#backtestCompositionStatus');
      const text = status?.textContent.trim() || '';
      const visibleMatches = [...document.querySelectorAll('body *')].filter((node) => (
        node.children.length === 0 && !node.hidden && getComputedStyle(node).display !== 'none'
        && node.textContent.trim() === text
      )).length;
      const bar = document.querySelector('.backtest-submit-bar').getBoundingClientRect();
      const button = document.querySelector('#runBacktestBtn').getBoundingClientRect();
      return {
        state: status?.dataset.state,
        text,
        visibleMatches,
        statusInSubmitPanel: status?.closest('#backtestSubmitPanel')?.id === 'backtestSubmitPanel',
        extraErrorHidden: document.querySelector('#backtestEntryError')?.hidden,
        buttonRightAligned: button.right > bar.left + (bar.width * 0.75),
        barHeight: bar.height,
      };
    });
    assert(finalComposition.visibleMatches === 1 && finalComposition.statusInSubmitPanel
      && finalComposition.extraErrorHidden && finalComposition.buttonRightAligned
      && finalComposition.barHeight >= 70 && finalComposition.barHeight < 130,
    'Backtest validation and primary action are not unified in the submission panel', finalComposition);
    assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
    await page.screenshot({ path: '/tmp/trade-backtest-validation-layout.png', fullPage: true });
    console.log(JSON.stringify({ overviewLoading, pipelineLoading, pendingComposition, finalComposition, pageErrors }, null, 2));
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
