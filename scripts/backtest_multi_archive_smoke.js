const { execFileSync } = require('node:child_process');
let puppeteer;
try {
  puppeteer = require('puppeteer-core');
} catch {
  puppeteer = require('puppeteer');
}

const BASE = process.env.TRADE_WEB_BASE || 'http://10.130.130.66:30809';
const CONFIG = process.env.TRADE_CONFIG || 'deploy/user/strategy-control-preview.json';

function assert(condition, message, payload) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

function createSession() {
  const code = [
    'import json, secrets, time',
    'from engine.service import control_api as control; from engine.control import auth as trade_auth',
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
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
  const secure = BASE.startsWith('https://');
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1720, height: 1050 });
  await page.setCookie(
    { name: 'trade_session', value: session.token, url: BASE, secure, httpOnly: true, sameSite: 'Strict' },
    { name: 'trade_csrf', value: session.csrf, url: BASE, secure, sameSite: 'Strict' },
  );
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  try {
    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    try {
      await page.waitForSelector('#backtestResourceBrowser .trade-resource-browser-shell');
    } catch (error) {
      error.payload = {
        url: page.url(),
        cookies: (await page.cookies(BASE)).map((cookie) => cookie.name),
        body: (await page.content()).slice(0, 1000),
        pageErrors,
      };
      throw error;
    }
    await page.waitForFunction(() => window.__tradeState.repositoryCatalogs.backtest?.items?.length > 0);
    const first = await page.evaluate(() => window.__tradeState.repositoryCatalogs.backtest.items
      .find((item) => item.status !== 'archived'));
    assert(first, 'No active Backtest is available for the multi-selection smoke test');

    const search = '#backtestResourceBrowser .trade-resource-search input';
    await page.type(search, first.label);
    await page.waitForFunction((itemId) => [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-search-item')]
      .some((button) => button.dataset.tradeItemId === itemId), {}, first.itemId);
    await page.evaluate((itemId) => [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-search-item')]
      .find((button) => button.dataset.tradeItemId === itemId).click(), first.itemId);
    await page.evaluate((selector) => {
      const field = document.querySelector(selector);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, '');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }, search);
    await page.waitForFunction(() => document.querySelectorAll('#backtestResourceBrowser .file-item-container[data-trade-item-id]').length >= 2);
    const points = await page.$$eval('#backtestResourceBrowser .file-item-container[data-trade-item-id]', (cards) => cards.slice(0, 2).map((card) => {
      const rect = card.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, itemId: card.dataset.tradeItemId };
    }));
    await page.mouse.click(points[0].x, points[0].y);
    await page.keyboard.down('Control');
    await page.mouse.click(points[1].x, points[1].y);
    await page.keyboard.up('Control');
    await new Promise((resolve) => setTimeout(resolve, 500));
    const selectionAfterClicks = await page.evaluate(() => ({
      selected: window.__tradeState.uiRepositorySelections.backtest?.map((item) => item.backtestId) || [],
      cards: [...document.querySelectorAll('#backtestResourceBrowser .file-item-container.file-selected')]
        .map((card) => card.dataset.tradeItemId),
      inspectorActions: [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-inspector-actions button')]
        .map((button) => button.textContent.trim()),
    }));
    assert(selectionAfterClicks.selected.length === 2, 'Backtest Browser did not retain a two-item selection', selectionAfterClicks);
    assert(selectionAfterClicks.inspectorActions.includes('Archive 2 Backtests'), 'Multi-selection Inspector has no batch Archive action', selectionAfterClicks);
    await page.mouse.click(points[1].x, points[1].y, { button: 'right' });
    await page.waitForSelector('#backtestResourceBrowser .trade-resource-context-menu');
    const result = await page.evaluate(() => ({
      selected: window.__tradeState.uiRepositorySelections.backtest?.map((item) => item.backtestId) || [],
      actions: [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-context-menu button')]
        .map((button) => button.textContent.trim()),
    }));
    assert(result.selected.length === 2, 'Right-click collapsed the Backtest multi-selection', result);
    assert(result.actions.includes('Archive 2 Backtests'), 'Backtest context menu has no batch Archive action', result);
    assert(!pageErrors.length, 'Browser emitted page errors', pageErrors);
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
