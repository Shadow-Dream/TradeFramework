const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'http://127.0.0.1:30808';
const SESSION = process.env.TRADE_TEST_SESSION || '';
const CSRF = process.env.TRADE_TEST_CSRF || '';

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

async function revealResource(page, item) {
  const input = '#dataRepositoryBrowser .trade-resource-search input';
  await page.click(input);
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.keyboard.type(item.label || item.samplerId);
  await page.waitForFunction((itemId) => (
    [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-search-item')]
      .some((node) => node.dataset.tradeItemId === itemId)
  ), {}, item.itemId);
  await page.evaluate((itemId) => {
    [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-search-item')]
      .find((node) => node.dataset.tradeItemId === itemId)?.click();
  }, item.itemId);
  await page.waitForFunction((itemId) => (
    [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
      .some((node) => node.dataset.tradeItemId === itemId)
  ), {}, item.itemId);
}

async function openContextMenu(page, itemId) {
  await page.evaluate((targetId) => {
    const card = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
      .find((node) => node.dataset.tradeItemId === targetId);
    card.dispatchEvent(new MouseEvent('contextmenu', {
      bubbles: true, cancelable: true, clientX: 900, clientY: 500,
    }));
  }, itemId);
}

async function main() {
  assert(SESSION && CSRF, 'TRADE_TEST_SESSION and TRADE_TEST_CSRF are required');
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  page.setDefaultTimeout(90000);
  await page.setExtraHTTPHeaders({ 'X-Forwarded-Proto': 'https' });
  await page.setCookie(
    { name: 'trade_session', value: SESSION, url: BASE, httpOnly: true },
    { name: 'trade_csrf', value: CSRF, url: BASE },
  );
  let samplerEditorRequests = 0;
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    if (/\/api\/data\/samplers\/[^/]+\/versions\/[^/]+\/jupyter$/.test(request.url())) {
      samplerEditorRequests += 1;
      request.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ accepted: true, url: 'about:blank' }),
      });
      return;
    }
    request.continue();
  });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 90000 });
  await page.waitForFunction(() => (
    window.__tradeState?.environments?.length > 0
    && document.querySelector('#backtestEnvironmentSelect')?.options.length > 0
  ));
  const selected = await page.$eval('#backtestEnvironmentSelect', (node) => node.value);
  assert(selected === '', 'Backtest preselected an Environment instead of requiring explicit composition', { selected });
  const environmentKey = await page.$eval('#backtestEnvironmentSelect', (node) => (
    [...node.options].find((option) => option.value.startsWith('standard-paper-environment::'))?.value || ''
  ));
  assert(environmentKey, 'Standard Paper Environment option is missing');
  await page.select('#backtestEnvironmentSelect', environmentKey);
  await page.click('#chainEnvironmentDetails');
  await page.waitForFunction(() => (
    location.pathname === '/environment-blueprint'
    && document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length > 0
  ));
  const details = await page.evaluate(() => ({
    environment: new URLSearchParams(location.search).get('environment'),
    graphNodes: document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length || 0,
    availableModules: Object.keys(window.__tradeState?.environmentModules || {}).length,
  }));
  assert(details.environment === environmentKey
    && details.graphNodes > 1 && details.availableModules > 0,
  'Environment Details is empty or opened the neutral version', details);

  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 90000 });
  await page.waitForFunction(() => window.__tradeState?.repositoryCatalogs?.data?.items?.length > 0);
  const samplers = await page.evaluate(() => {
    const items = window.__tradeState.repositoryCatalogs.data.items
      .filter((item) => item.sourceRepository === 'samplers');
    return {
      builtin: items.find((item) => item.builtin),
      editable: items.find((item) => !item.builtin && item.type === 'row-map'),
    };
  });
  assert(samplers.builtin && samplers.editable, 'Sampler catalog lacks both built-in and editable versions', samplers);

  await revealResource(page, samplers.editable);
  await openContextMenu(page, samplers.editable.itemId);
  const editableAction = await page.$eval(
    '#dataRepositoryBrowser [data-sampler-context-action="edit"]',
    (button) => ({ disabled: button.disabled, title: button.title }),
  );
  assert(!editableAction.disabled && editableAction.title.includes('Jupyter'),
    'Custom Sampler has no Jupyter editor action', editableAction);
  await page.click('#dataRepositoryBrowser [data-sampler-context-action="edit"]');
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert(samplerEditorRequests === 1, 'Sampler Open did not request the editor', { samplerEditorRequests });
  await page.evaluate((targetId) => {
    const card = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
      .find((node) => node.dataset.tradeItemId === targetId);
    card.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  }, samplers.editable.itemId);
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert(samplerEditorRequests === 2, 'Sampler double-click did not request the editor', { samplerEditorRequests });
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);

  await browser.close();
  console.log(JSON.stringify({ selected, details, builtinPresent: !!samplers.builtin, editableAction, samplerEditorRequests }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
