const { execFileSync } = require('node:child_process');
let puppeteer;
try {
  puppeteer = require('puppeteer-core');
} catch {
  puppeteer = require('puppeteer');
}

const BASE = process.env.TRADE_WEB_BASE || 'http://10.130.130.66:30809';
const CONFIG = process.env.TRADE_CONFIG || 'deploy/user/strategy-control-preview.json';
const SCREENSHOT = process.env.TRADE_CONFIG_SCREENSHOT || '/tmp/trade-config-editor-environment.png';

const ACCEPTANCE = {
  datasetId: 'tlm01d02-eval-spx-rth-20240823-20260820',
  datasetContentHash: 'sha256:614fbf3236db19a0971d5bc1a210efd369374f479b979b4e92eea890b604c9b9',
  sampler: 'causal-multi-timeframe-market::4',
  environment: 'tlm1d02-market-environment::3',
  pipeline: 'tlm1d02::3',
  analysis: 'tlm1d02-d13-performance::3',
};

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

function sessionCommand(action, token = '') {
  const create = action === 'create';
  const code = create ? [
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
  ] : [
    'from engine.service import control_api as control; from engine.control import auth as trade_auth; import sys',
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
    'with trade_auth.connect(config) as connection:',
    '    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (trade_auth.opaque_token_hash(sys.argv[1]),))',
    '    connection.commit()',
  ];
  const output = execFileSync('python3', ['-c', code.join('\n'), ...(create ? [] : [token])], {
    encoding: 'utf8',
  });
  return create ? JSON.parse(output) : null;
}

async function waitForConfigEditor(page) {
  await page.waitForFunction(() => {
    const dialog = document.querySelector('#backtestSamplerConfigDialog');
    const loading = document.querySelector('#backtestConfigLoading');
    const workbench = document.querySelector('#backtestConfigWorkbench');
    return dialog?.open && loading?.hidden && !workbench?.hidden;
  }, { timeout: 30000 });
}

async function openConfig(page, resource) {
  const names = {
    sampler: 'Sampler',
    environment: 'Environment',
    pipeline: 'Pipeline',
    analysis: 'Analysis',
  };
  await page.click(`#configureBacktest${names[resource]}`);
  try {
    await waitForConfigEditor(page);
  } catch (error) {
    error.payload = await page.evaluate((name) => ({
      resource: name,
      dialogOpen: Boolean(document.querySelector('#backtestSamplerConfigDialog')?.open),
      loadingHidden: Boolean(document.querySelector('#backtestConfigLoading')?.hidden),
      workbenchHidden: Boolean(document.querySelector('#backtestConfigWorkbench')?.hidden),
      error: document.querySelector('#backtestSamplerConfigError')?.textContent?.trim() || '',
      selected: {
        sampler: document.querySelector('#backtestSampler')?.value || '',
        environment: document.querySelector('#backtestEnvironmentSelect')?.value || '',
        pipeline: document.querySelector('#backtestPipelineSelect')?.value || '',
        analysis: document.querySelector('#backtestAnalysisSelect')?.value || '',
      },
    }), resource);
    throw error;
  }
}

async function selectConfigInstance(page, instanceId) {
  await page.evaluate((id) => {
    const button = document.querySelector(`[data-backtest-config-instance="${CSS.escape(id)}"]`);
    if (!button) throw new Error(`Missing Config instance '${id}'`);
    button.click();
  }, instanceId);
}

async function setSchemaField(page, name, value) {
  await page.$eval(`[data-schema-field="${name}"]`, (input, next) => {
    input.value = String(next);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
}

async function main() {
  const session = sessionCommand('create');
  let browser;
  const pageErrors = [];
  const failedResponses = [];
  let preparedRequest = null;
  try {
    browser = await puppeteer.launch({
      headless: true,
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1720, height: 1080 });
    await page.setCookie(
      { name: 'trade_session', value: session.token, url: BASE, httpOnly: true, sameSite: 'Strict' },
      { name: 'trade_csrf', value: session.csrf, url: BASE, sameSite: 'Strict' },
    );
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    page.on('request', (request) => {
      if (request.method() !== 'POST' || !request.url().endsWith('/api/backtest-submissions/prepare')) return;
      preparedRequest = JSON.parse(request.postData() || '{}');
    });

    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      window.__tradeState?.datasets?.length
      && window.__tradeState?.samplers?.length
      && window.__tradeState?.environments?.length
      && window.__tradeState?.pipelineVersions?.length
      && window.__tradeState?.analyses?.length
    ), { timeout: 30000 });
    try {
      await page.waitForFunction((expected) => {
        const dataset = window.__tradeState.datasets.find((row) => (
          row.datasetId === expected.datasetId
          && String(row.latestVersionId || '').endsWith(`@${expected.datasetContentHash}`)
        ));
        const values = (selector) => [...document.querySelectorAll(`${selector} option`)].map((option) => option.value);
        return dataset
          && values('#backtestDataset').includes(dataset.datasetId)
          && values('#backtestSampler').includes(expected.sampler)
          && values('#backtestEnvironmentSelect').includes(expected.environment)
          && values('#backtestPipelineSelect').includes(expected.pipeline)
          && values('#backtestAnalysisSelect').includes(expected.analysis);
      }, { timeout: 30000 }, ACCEPTANCE);
    } catch (error) {
      error.payload = await page.evaluate(() => ({
        url: location.href,
        health: document.querySelector('#health')?.textContent?.trim() || '',
        entryError: document.querySelector('#backtestEntryError')?.textContent?.trim() || '',
        controls: ['backtestDataset', 'backtestSampler', 'backtestEnvironmentSelect', 'backtestPipelineSelect', 'backtestAnalysisSelect']
          .map((id) => ({ id, exists: Boolean(document.getElementById(id)), options: document.getElementById(id)?.options?.length || 0 })),
        datasets: window.__tradeState?.datasets || [],
      }));
      throw error;
    }
    const selection = await page.evaluate((expected) => {
      const dataset = window.__tradeState.datasets.find((row) => (
        row.datasetId === expected.datasetId
        && String(row.latestVersionId || '').endsWith(`@${expected.datasetContentHash}`)
      ));
      return {
        datasetId: dataset?.datasetId || '',
        available: {
          sampler: [...document.querySelectorAll('#backtestSampler option')].map((option) => option.value),
          environment: [...document.querySelectorAll('#backtestEnvironmentSelect option')].map((option) => option.value),
          pipeline: [...document.querySelectorAll('#backtestPipelineSelect option')].map((option) => option.value),
          analysis: [...document.querySelectorAll('#backtestAnalysisSelect option')].map((option) => option.value),
        },
      };
    }, ACCEPTANCE);
    assert(selection.datasetId, 'Acceptance Dataset is unavailable', selection);
    for (const resource of ['sampler', 'environment', 'pipeline', 'analysis']) {
      assert(selection.available[resource].includes(ACCEPTANCE[resource]), `Acceptance ${resource} is unavailable`, selection);
    }
    await page.select('#backtestDataset', selection.datasetId);
    await page.select('#backtestSampler', ACCEPTANCE.sampler);
    await page.select('#backtestEnvironmentSelect', ACCEPTANCE.environment);
    await page.select('#backtestPipelineSelect', ACCEPTANCE.pipeline);
    await page.select('#backtestAnalysisSelect', ACCEPTANCE.analysis);

    await openConfig(page, 'environment');
    const environmentLayout = await page.evaluate(() => {
      const rect = (selector) => {
        const box = document.querySelector(selector).getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
      };
      return {
        count: document.querySelectorAll('[data-backtest-config-instance]').length,
        mode: document.querySelector('#backtestConfigModeBadge').textContent.trim(),
        instances: rect('.backtest-config-instances'),
        fields: rect('.backtest-config-fields-pane'),
        json: rect('.backtest-config-json-pane'),
      };
    });
    assert(environmentLayout.count === 2, 'Environment does not expose both internal Module instances', environmentLayout);
    assert(environmentLayout.mode === 'Sparse override', 'Environment is not editing a sparse Backtest override', environmentLayout);
    assert(environmentLayout.instances.right <= environmentLayout.fields.left
      && environmentLayout.fields.right <= environmentLayout.json.left,
    'Config workbench panes overlap', environmentLayout);

    await selectConfigInstance(page, 'causal-execution');
    await page.waitForSelector('[data-schema-field="initialCash"]');
    const initialCashDefault = await page.$eval('[data-schema-field="initialCash"]', (input) => Number(input.value));
    assert(initialCashDefault === 1000, 'Environment version default was not rendered', { initialCashDefault });
    await setSchemaField(page, 'initialCash', 1500);
    let environmentDraft = await page.$eval('#backtestConfigJson', (input) => JSON.parse(input.value));
    assert(environmentDraft['causal-execution']?.initialCash === 1500,
      'Schema edit did not produce the sparse Environment override', environmentDraft);

    await page.$eval('#backtestConfigJson', (input) => {
      input.value = JSON.stringify({ 'causal-execution': { initialCash: 1600 } }, null, 2);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.click('#syncBacktestConfigJsonBtn');
    const jsonRoundTrip = await page.$eval('[data-schema-field="initialCash"]', (input) => Number(input.value));
    assert(jsonRoundTrip === 1600, 'JSON source did not update the schema form', { jsonRoundTrip });
    await page.click('#resetBacktestConfigInstanceBtn');
    environmentDraft = await page.$eval('#backtestConfigJson', (input) => JSON.parse(input.value));
    const resetValue = await page.$eval('[data-schema-field="initialCash"]', (input) => Number(input.value));
    assert(Object.keys(environmentDraft).length === 0 && resetValue === 1000,
      'Reset instance did not restore the Environment version default', { environmentDraft, resetValue });
    await setSchemaField(page, 'initialCash', 1500);
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    await page.click('#applyBacktestSamplerConfigBtn');
    await page.waitForFunction(() => !document.querySelector('#backtestSamplerConfigDialog').open);
    const savedEnvironmentOverride = await page.evaluate(() => window.__tradeBacktestEntryState.environmentConfigOverride);
    assert(JSON.stringify(savedEnvironmentOverride) === JSON.stringify({ 'causal-execution': { initialCash: 1500 } }),
      'Apply did not retain the sparse Environment override', savedEnvironmentOverride);

    await openConfig(page, 'pipeline');
    const pipelineAudit = await page.evaluate((key) => ({
      renderedCount: document.querySelectorAll('[data-backtest-config-instance]').length,
      definitionCount: Object.keys(window.__tradeState.backtestPipelineDefinitions[key]?.instances || {}).length,
    }), ACCEPTANCE.pipeline);
    assert(pipelineAudit.renderedCount > 0 && pipelineAudit.renderedCount === pipelineAudit.definitionCount,
      'Pipeline does not expose every internal Module instance', pipelineAudit);
    await selectConfigInstance(page, 'tlm.context');
    await page.waitForSelector('[data-schema-field="atrLen"]');
    const atrDefault = await page.$eval('[data-schema-field="atrLen"]', (input) => Number(input.value));
    assert(atrDefault === 14, 'Pipeline Module version default was not rendered', { atrDefault });
    await page.click('#cancelBacktestSamplerConfigBtn');

    await openConfig(page, 'analysis');
    const analysisCount = await page.$$eval('[data-backtest-config-instance]', (nodes) => nodes.length);
    assert(analysisCount === 1, 'Analysis does not expose its internal Module instance', { analysisCount });
    await page.waitForSelector('[data-schema-field="riskFreeRate"]');
    const riskFreeDefault = await page.$eval('[data-schema-field="riskFreeRate"]', (input) => Number(input.value));
    assert(riskFreeDefault === 0, 'Analysis Module version default was not rendered', { riskFreeDefault });
    await page.click('#cancelBacktestSamplerConfigBtn');

    await openConfig(page, 'sampler');
    const samplerAudit = await page.evaluate(() => ({
      count: document.querySelectorAll('[data-backtest-config-instance]').length,
      mode: document.querySelector('#backtestConfigModeBadge').textContent.trim(),
      hasEditor: Boolean(document.querySelector('#backtestConfigFields [data-schema-field], #backtestConfigFields [data-config-json-editor]')),
      hasSourceInstrumentField: Boolean(document.querySelector('#backtestConfigFields [data-schema-field="sourceInstrumentId"]')),
      parameters: JSON.parse(document.querySelector('#backtestConfigJson').value),
    }));
    assert(samplerAudit.count === 1
      && samplerAudit.mode === 'Complete parameters'
      && samplerAudit.hasEditor
      && samplerAudit.hasSourceInstrumentField
      && samplerAudit.parameters
      && typeof samplerAudit.parameters === 'object'
      && !Array.isArray(samplerAudit.parameters),
    'Sampler complete parameters are not available', samplerAudit);
    await page.click('#cancelBacktestSamplerConfigBtn');

    await page.click('#runBacktestBtn');
    await page.waitForFunction(() => (
      ['valid', 'invalid'].includes(window.__tradeBacktestEntryState.compositionValidation)
    ), { timeout: 30000 });
    const buildAudit = await page.evaluate(() => ({
      validation: window.__tradeBacktestEntryState.compositionValidation,
      message: window.__tradeBacktestEntryState.compositionMessage,
      button: document.querySelector('#runBacktestBtn').textContent.trim(),
    }));
    assert(buildAudit.validation === 'valid' && buildAudit.button.startsWith('Run'), 'Authoritative Build failed', buildAudit);
    assert(preparedRequest?.environment?.configOverride?.['causal-execution']?.initialCash === 1500,
      'Build request did not contain the Environment override', preparedRequest);
    assert(preparedRequest?.pipeline?.configOverride
      && preparedRequest?.analysis?.configOverride
      && preparedRequest?.sampler
      && Object.prototype.hasOwnProperty.call(preparedRequest.sampler, 'parameters'),
    'Build request omitted a top-level configuration surface', preparedRequest);
    assert(!pageErrors.length, 'Browser emitted page errors', pageErrors);
    assert(!failedResponses.length, 'Server returned 5xx responses', failedResponses);

    console.log(JSON.stringify({
      selection: { ...ACCEPTANCE, datasetId: selection.datasetId },
      environmentLayout,
      savedEnvironmentOverride,
      pipelineCount: pipelineAudit.renderedCount,
      analysisCount,
      samplerMode: samplerAudit.mode,
      build: buildAudit,
      screenshot: SCREENSHOT,
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    sessionCommand('delete', session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
