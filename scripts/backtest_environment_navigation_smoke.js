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
  if (process.env.TRADE_TEST_FORWARDED_HTTPS === '1') {
    await page.setExtraHTTPHeaders({ 'X-Forwarded-Proto': 'https' });
  }
  await page.setCookie(
    { name: 'trade_session', value: SESSION, url: BASE, httpOnly: true, secure: BASE.startsWith('https://') },
    { name: 'trade_csrf', value: CSRF, url: BASE, secure: BASE.startsWith('https://') },
  );
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 90000 });
  await page.waitForFunction(() => (
    document.querySelector('#backtestPipelineSelect')?.options.length > 0
    && document.querySelector('#backtestEnvironmentSelect')?.options.length > 0
    && document.querySelector('#backtestAnalysisSelect')?.options.length > 0
  ));
  const composition = await page.evaluate(() => ({
    path: location.pathname,
    pipeline: document.querySelector('#backtestPipelineSelect')?.value,
    environmentOptions: [...document.querySelectorAll('#backtestEnvironmentSelect option')].map((node) => node.value),
    analysisOptions: [...document.querySelectorAll('#backtestAnalysisSelect option')].map((node) => node.value),
    environmentNode: !!document.querySelector('[data-backtest-node="environment"]'),
    pipelineNode: !!document.querySelector('[data-backtest-node="pipeline"]'),
    analysisNode: !!document.querySelector('[data-backtest-node="analyzer"]'),
  }));
  assert(composition.path === '/backtests'
    && composition.pipeline === ''
    && composition.environmentOptions.includes('standard-paper-environment::2')
    && composition.analysisOptions.includes('standard-performance-analysis::1')
    && composition.environmentNode && composition.pipelineNode && composition.analysisNode,
  'Backtest composition does not expose independent Pipeline, Environment and Analysis versions', composition);

  const selections = await page.evaluate(() => ({
    environment: [...document.querySelectorAll('#backtestEnvironmentSelect option')]
      .find((node) => node.value.startsWith('standard-paper-environment::'))?.value || '',
    analysis: [...document.querySelectorAll('#backtestAnalysisSelect option')]
      .find((node) => node.value.startsWith('standard-performance-analysis::'))?.value || '',
  }));
  assert(selections.environment && selections.analysis, 'Standard graph presets are missing', selections);
  await page.select('#backtestEnvironmentSelect', selections.environment);
  await page.select('#backtestAnalysisSelect', selections.analysis);

  await page.click('#chainEnvironmentDetails');
  await page.waitForFunction(() => (
    location.pathname === '/environment-blueprint'
    && document.querySelector('#environment')?.classList.contains('active')
    && document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length > 0
  ));
  const environment = await page.evaluate(() => ({
    path: location.pathname,
    selected: new URLSearchParams(location.search).get('environment'),
    nodes: document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph?._nodes?.length || 0,
  }));
  assert(environment.nodes > 1, 'Independent Environment graph did not render', environment);

  await page.click('#environmentGraphBuilder [data-graph-back]');
  await page.waitForFunction(() => location.pathname === '/backtests');
  await page.click('#chainAnalysisDetails');
  await page.waitForFunction(() => (
    location.pathname === '/analysis-blueprint'
    && document.querySelector('#analysis')?.classList.contains('active')
    && document.querySelector('#analysisGraphBuilder')?.__liteGraphGraph?._nodes?.length > 0
  ));
  const analysis = await page.evaluate(() => ({
    path: location.pathname,
    selected: new URLSearchParams(location.search).get('analysis'),
    nodes: document.querySelector('#analysisGraphBuilder')?.__liteGraphGraph?._nodes?.length || 0,
  }));
  assert(analysis.nodes > 1, 'Independent Analysis graph did not render', analysis);

  await page.click('.nav-btn[data-view="pipeline"]');
  await page.waitForFunction(() => location.pathname === '/pipeline');
  await page.waitForSelector('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]');
  await page.$eval('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]', (node) => {
    node.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  });
  await page.waitForFunction(() => location.pathname === '/pipeline/builder');
  const pipeline = await page.evaluate(() => ({
    hasEmbeddedAnalysisSection: !!document.querySelector('#pipelineAnalysisSection'),
    hasEmbeddedAnalysisCard: [...document.querySelectorAll('#pipelineStageGrid .component-group h3')]
      .some((node) => node.textContent.trim() === 'Analysis'),
    manifestHasAnalysis: (() => {
      const manifest = document.querySelector('#manifestJson')?.textContent;
      return /analysisGraph|analysisModules/.test(manifest || '');
    })(),
  }));
  assert(!pipeline.hasEmbeddedAnalysisSection && !pipeline.hasEmbeddedAnalysisCard && !pipeline.manifestHasAnalysis,
    'Pipeline still owns an Analysis shell', pipeline);
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);

  await browser.close();
  console.log(JSON.stringify({ composition, environment, analysis, pipeline, pageErrors }, null, 2));
}

main().catch((error) => {
  console.error(error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
