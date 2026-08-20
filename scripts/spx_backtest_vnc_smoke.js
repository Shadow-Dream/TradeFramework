const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'https://trade.duckduckrun.com';
const DATASET_ID = 'spx-cfd-spreadex-1h-20260628';
const PIPELINE_ID = process.env.TRADE_TEST_PIPELINE || 'nested-json-smoke';
const auth = process.env.TRADE_TEST_AUTH_FILE
  ? JSON.parse(require('fs').readFileSync(process.env.TRADE_TEST_AUTH_FILE, 'utf8'))
  : {};
const SESSION = process.env.TRADE_TEST_SESSION || auth.session || '';
const CSRF = process.env.TRADE_TEST_CSRF || auth.csrf || '';

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

async function main() {
  const browser = await puppeteer.launch({
    headless: false,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  if (SESSION && CSRF) {
    await page.setCookie(
      { name: 'trade_session', value: SESSION, url: BASE, httpOnly: true, secure: BASE.startsWith('https://') },
      { name: 'trade_csrf', value: CSRF, url: BASE, secure: BASE.startsWith('https://') },
    );
  }
  const pageErrors = [];
  const failedResponses = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => {
    if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
  });

  await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction((pipelineId) => (
    [...document.querySelectorAll('#backtestPipelineSelect option')]
      .some((option) => option.value.startsWith(`${pipelineId}::`))
  ), {}, PIPELINE_ID);
  const pipelineKey = await page.$eval('#backtestPipelineSelect', (node, pipelineId) => (
    [...node.options].find((option) => option.value.startsWith(`${pipelineId}::`))?.value || ''
  ), PIPELINE_ID);
  await page.select('#backtestPipelineSelect', pipelineKey);
  await page.waitForSelector(`#backtestDataset option[value="${DATASET_ID}"]`, { timeout: 30000 });
  await page.select('#backtestDataset', DATASET_ID);
  const datasetCard = await page.evaluate(() => ({
    versionControlAbsent: !document.querySelector('#backtestDatasetVersion'),
    meta: document.querySelector('#chainDatasetMeta')?.textContent.trim() || '',
  }));
  assert(datasetCard.versionControlAbsent && /evidence locked automatically/i.test(datasetCard.meta)
    && !/sha256|version/i.test(datasetCard.meta),
  'Dataset card exposes internal version evidence', datasetCard);
  await page.select('#backtestSampler', 'nested-row::1');
  const compositionKeys = await page.evaluate(() => ({
    environment: [...document.querySelectorAll('#backtestEnvironmentSelect option')]
      .find((option) => option.value.startsWith('standard-paper-environment::'))?.value || '',
    analysis: [...document.querySelectorAll('#backtestAnalysisSelect option')]
      .find((option) => option.value.startsWith('standard-performance-analysis::'))?.value || '',
  }));
  assert(compositionKeys.environment && compositionKeys.analysis,
    'Backtest standard graph versions are missing', compositionKeys);
  await page.select('#backtestEnvironmentSelect', compositionKeys.environment);
  await page.select('#backtestAnalysisSelect', compositionKeys.analysis);
  await page.click('#configureBacktestSampler');
  await page.waitForSelector('#backtestSamplerConfigDialog[open]');
  const samplerInspector = await page.evaluate(() => ({
    fieldCount: document.querySelectorAll('#backtestSamplerConfigFields [data-schema-field]').length,
    mappingRows: document.querySelectorAll('#backtestSamplerConfigFields .key-value-row').length,
    hasRawJson: !!document.querySelector('#backtestSamplerParameters')
      || !!document.querySelector('#backtestSamplerConfigFields textarea[data-schema-type="object"]'),
    datasetInsideNode: !!document.querySelector('[data-backtest-node="dataset"] #backtestDataset'),
    samplerInsideNode: !!document.querySelector('[data-backtest-node="sampler"] #backtestSampler'),
  }));
  assert(samplerInspector.fieldCount >= 4 && samplerInspector.mappingRows === 7
    && !samplerInspector.hasRawJson && samplerInspector.datasetInsideNode && samplerInspector.samplerInsideNode,
  'Sampler Inspector is not schema-driven inside the Backtest graph', samplerInspector);
  await page.screenshot({ path: '/tmp/trade-sampler-config-vnc.png', fullPage: true });
  await page.click('#applyBacktestSamplerConfigBtn');
  await page.waitForFunction(() => {
    const button = document.querySelector('#runBacktestBtn');
    return !button?.disabled && button?.textContent.trim() === 'Build';
  });

  const form = await page.evaluate(() => ({
    dataset: document.querySelector('#backtestDataset')?.value,
    sampler: document.querySelector('#backtestSampler')?.value,
    parameters: window.__tradeBacktestEntryState?.samplerParameters,
    hasSimulationInputs: !!document.querySelector('#backtestSimulationInputs'),
    pipeline: document.querySelector('#backtestPipelineSelect')?.value,
  }));
  assert(form.dataset === DATASET_ID && form.sampler === 'nested-row::1', 'SPX Backtest form selection failed', form);
  assert(!form.hasSimulationInputs, 'Unexpected Backtest simulation controls are present', form);

  await page.click('#runBacktestBtn');
  const immediateBuild = await page.evaluate(() => ({
    disabled: document.querySelector('#runBacktestBtn')?.disabled,
    busy: document.querySelector('#runBacktestBtn')?.getAttribute('aria-busy'),
    label: document.querySelector('#runBacktestBtn')?.textContent.trim(),
    panelState: document.querySelector('#backtestSubmitPanel')?.dataset.state,
  }));
  assert(immediateBuild.disabled && immediateBuild.busy === 'true'
    && immediateBuild.label === 'Checking…' && immediateBuild.panelState === 'pending',
  'Backtest Build has no immediate checking feedback', immediateBuild);
  await page.waitForFunction(() => {
    const button = document.querySelector('#runBacktestBtn');
    return !button?.disabled && button?.textContent.trim() === 'Run Backtest';
  }, { timeout: 30000 });

  await page.click('#runBacktestBtn');
  const immediateSubmission = await page.evaluate(() => ({
    disabled: document.querySelector('#runBacktestBtn')?.disabled,
    busy: document.querySelector('#runBacktestBtn')?.getAttribute('aria-busy'),
    label: document.querySelector('#runBacktestBtn')?.textContent.trim(),
    panelState: document.querySelector('#backtestSubmitPanel')?.dataset.state,
  }));
  assert(immediateSubmission.disabled && immediateSubmission.busy === 'true'
    && immediateSubmission.label === 'Submitting…' && immediateSubmission.panelState === 'submitting',
  'Backtest submission has no immediate locked feedback', immediateSubmission);
  await page.waitForFunction(() => Boolean(window.__tradeState?.backtestJobs?.[0]?.jobId),
    { timeout: 30000 });
  const submittedJobId = await page.evaluate(() => window.__tradeState?.backtestJobs?.[0]?.jobId || '');
  assert(submittedJobId, 'Submitted Backtest job was not registered');
  await page.waitForFunction((jobId) => (
    window.__tradeState?.backtestJobs?.find((job) => job.jobId === jobId)?.status === 'completed'
  ), { timeout: 360000 }, submittedJobId);
  const completedBacktestId = await page.evaluate((jobId) => (
    window.__tradeState.backtestJobs.find((job) => job.jobId === jobId)?.backtestId || ''
  ), submittedJobId);
  assert(completedBacktestId, 'Completed Backtest job has no Result identity');
  await page.goto(`${BASE}/result?backtestId=${encodeURIComponent(completedBacktestId)}`,
    { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction((datasetId) => {
    const selected = window.__tradeState?.selectedBacktest;
    return selected?.datasetId === datasetId && selected?.metrics?.cycleCount === 20674;
  }, { timeout: 30000 }, DATASET_ID);
  await page.waitForFunction(() => document.querySelector('#metricStrip')?.textContent.includes('Cycles20674'),
    { timeout: 30000 });

  const result = await page.evaluate(() => {
    const selected = window.__tradeState.selectedBacktest;
    return {
      backtestId: selected.backtestId,
      datasetId: selected.datasetId,
      metrics: selected.metrics,
      dataKeys: Object.keys(selected.dataKeys || {}),
      metricText: document.querySelector('#metricStrip')?.textContent || '',
      routeBacktestId: new URLSearchParams(location.search).get('backtestId'),
    };
  });
  assert(result.routeBacktestId === result.backtestId, 'Dedicated Result route did not open the SPX run', result);
  assert(result.dataKeys.includes('policy.signal_action'), 'SPX Result does not expose the Pipeline-declared output', result);
  assert(result.dataKeys.includes('backtest.analysis') && !result.dataKeys.includes('backtest.transition'),
    'SPX Result does not expose the direct DataKey flow', result);
  assert(result.metrics.cycleCount > 0, 'Backtest did not record any Result cycles', result);
  assert(!/Return|Max DD|End Value|Trades/.test(result.metricText), 'Built-in financial metrics reappeared', result);
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
  assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);

  await page.screenshot({ path: '/tmp/trade-spx-backtest-vnc.png', fullPage: true });
  await browser.close();
  console.log(JSON.stringify({ form, samplerInspector, result, pageErrors, failedResponses }, null, 2));
}

main().catch((error) => {
  console.error(error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
