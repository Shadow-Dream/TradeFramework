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

async function selectSearchResult(page, item, containerId = 'dataRepositoryBrowser') {
  const input = `#${containerId} .trade-resource-search input`;
  await page.evaluate((selector) => {
    const field = document.querySelector(selector);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, '');
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.focus();
  }, input);
  await page.type(input, item.label);
  await page.waitForFunction((id, itemId) => [...document.querySelectorAll(`#${id} .trade-resource-search-item`)]
    .some((button) => button.dataset.tradeItemId === itemId), {}, containerId, item.itemId);
  await page.evaluate((id, itemId) => [...document.querySelectorAll(`#${id} .trade-resource-search-item`)]
    .find((button) => button.dataset.tradeItemId === itemId).click(), containerId, item.itemId);
  await page.waitForFunction((id, label) => [...document.querySelectorAll(`#${id} .file-item-container`)]
    .some((card) => card.getAttribute('title') === label), {}, containerId, item.label);
  return page.evaluate((id, label) => {
    const card = [...document.querySelectorAll(`#${id} .file-item-container`)]
      .find((candidate) => candidate.getAttribute('title') === label);
    const rect = card.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, containerId, item.label);
}

async function main() {
  const session = createSession();
  const browser = await puppeteer.launch({
    headless: false,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1720, height: 1050 });
  await page.setCookie(
    { name: 'trade_session', value: session.token, url: BASE, secure: true, httpOnly: true, sameSite: 'Strict' },
    { name: 'trade_csrf', value: session.csrf, url: BASE, secure: true, sameSite: 'Strict' },
  );
  const pageErrors = [];
  const serverErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => { if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`); });

  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
  try {
    await page.waitForSelector('#dataRepositoryBrowser .trade-resource-browser-shell');
  } catch (error) {
    console.error(JSON.stringify({ url: page.url(), pageErrors, serverErrors, body: (await page.content()).slice(0, 2000) }, null, 2));
    throw error;
  }
  const surface = await page.evaluate(() => ({
    oldCards: ['Dataset Operations', 'Dataset Workspaces', 'Submit Dataset Script']
      .filter((title) => [...document.querySelectorAll('#data .panel h2')].some((node) => node.textContent.trim() === title)),
    typeFilters: [...document.querySelectorAll('#dataRepositoryBrowser [data-resource-type-filter]')].map((node) => node.textContent.trim()),
    activeDataset: window.__tradeState.repositoryCatalogs.data.items.find((item) => item.sourceRepository === 'datasets' && item.status === 'active'),
    activeScript: window.__tradeState.repositoryCatalogs.data.items.find((item) => item.sourceRepository === 'scripts' && item.status !== 'archived'),
    activeWorkspace: window.__tradeState.repositoryCatalogs.data.items.find((item) => item.sourceRepository === 'workspaces' && item.status === 'draft'),
  }));
  assert(surface.oldCards.length === 0, 'Legacy Data operation cards are still visible', surface);
  assert(await page.$eval('#datasetProcessArguments', (node) => node.tagName === 'TEXTAREA')
    && await page.$('#datasetProcessParameterFields') === null,
  'Process must expose call-time argv without an Engine-owned parameter form');

  await page.evaluate(() => document.querySelector('#dataRepositoryBrowser .trade-resource-browser-main')
    .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 700, clientY: 420 })));
  await page.waitForSelector('#dataRepositoryBrowser .trade-resource-context-menu');
  const blankMenu = await page.$$eval('#dataRepositoryBrowser .trade-resource-context-menu button', (buttons) => buttons.map((button) => button.textContent.trim()));
  assert(blankMenu.includes('Add Dataset…') && blankMenu.includes('Add Script…'), 'Blank-area Data menu is incomplete', blankMenu);
  await page.evaluate(() => [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
    .find((button) => button.textContent.trim() === 'Add Dataset…').click());
  await page.waitForSelector('#datasetAddDialog[open]');
  assert(await page.$eval('#datasetAddMethodStep', (node) => !node.hidden), 'Add Dataset did not open on the method-selection step');
  assert(await page.$eval('#datasetAddDetailStep', (node) => node.hidden), 'Add Dataset details were shown before choosing a method');
  await page.click('[data-dataset-add-method="upload"]');
  assert(await page.$eval('#datasetAddUploadFields', (node) => !node.hidden), 'Local ZIP upload choice did not reveal upload fields');
  assert(await page.$('#uploadDatasetName') !== null && await page.$('#uploadDatasetId') === null,
    'Dataset upload must ask for Name without exposing Engine-owned Dataset ID');
  await page.click('#backDatasetAddBtn');
  await page.click('[data-dataset-add-method="online"]');
  const dateLayout = await page.$$eval('#datasetAddOnlineFields .dataset-date-grid label', (labels) => labels.map((label) => {
    const rect = label.getBoundingClientRect();
    return { width: rect.width, top: rect.top };
  }));
  assert(dateLayout.length === 3 && dateLayout.every((item) => item.width >= 175)
    && Math.max(...dateLayout.map((item) => item.top)) - Math.min(...dateLayout.map((item) => item.top)) < 4,
  'Start, End and Interval are still cramped or wrapping', dateLayout);
  await page.screenshot({ path: '/tmp/trade-add-dataset-online-vnc.png', fullPage: true });
  await page.click('#cancelDatasetAddBtn');
  await page.evaluate(() => document.querySelector('#dataRepositoryBrowser .trade-resource-browser-main')
    .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 700, clientY: 420 })));
  await page.evaluate(() => [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
    .find((button) => button.textContent.trim() === 'Add Script…').click());
  await page.waitForSelector('#datasetScriptDialog[open]');
  assert(await page.$('#datasetScriptId') === null && await page.$('#datasetScriptVersion') === null,
    'Add Script still exposes Engine-owned Script or Version identity');
  assert(await page.$('#datasetScriptParameterSchema') === null,
    'Add Script still imposes an Engine-owned parameter schema on free-form Python');
  assert(await page.$eval('#datasetScriptMethodStep', (node) => !node.hidden), 'Add Script did not open on source selection');
  assert(await page.$eval('#datasetScriptDetailStep', (node) => node.hidden), 'Add Script details were shown before choosing a source');
  const scriptMethods = await page.$$eval('[data-dataset-script-method]', (buttons) => buttons.map((button) => button.dataset.datasetScriptMethod));
  assert(scriptMethods.length === 2 && scriptMethods.includes('upload') && scriptMethods.includes('workspace'), 'Add Script must expose exactly Local Upload and Workspace sources', scriptMethods);
  await page.screenshot({ path: '/tmp/trade-add-script-method-vnc.png', fullPage: true });
  await page.click('[data-dataset-script-method="workspace"]');
  assert(await page.$eval('#datasetScriptWorkspaceFields', (node) => !node.hidden), 'Workspace choice did not reveal Workspace Script fields');
  assert(await page.$eval('#datasetScriptFileField', (node) => node.hidden), 'Workspace choice still shows the local file input');
  await page.waitForFunction(() => document.querySelector('#datasetScriptWorkspacePath')?.options.length > 0
    && !document.querySelector('#datasetScriptWorkspacePath').options[0].textContent.startsWith('Loading'));
  const scriptSourceWidths = await page.$$eval('#datasetScriptWorkspaceFields label', (labels) => labels.map((label) => label.getBoundingClientRect().width));
  assert(scriptSourceWidths.length === 2 && scriptSourceWidths.every((width) => width >= 300), 'Workspace and Python script selectors are still cramped', scriptSourceWidths);
  await page.screenshot({ path: '/tmp/trade-add-script-workspace-vnc.png', fullPage: true });
  await page.click('#backDatasetScriptBtn');
  await page.click('[data-dataset-script-method="upload"]');
  assert(await page.$eval('#datasetScriptFileField', (node) => !node.hidden), 'Local upload choice did not reveal the Python file input');
  assert(await page.$eval('#datasetScriptWorkspaceFields', (node) => node.hidden), 'Local upload choice still shows Workspace selectors');
  await page.click('#cancelDatasetScriptBtn');

  if (surface.activeWorkspace) {
    const workspacePoint = await selectSearchResult(page, surface.activeWorkspace);
    await page.mouse.click(workspacePoint.x, workspacePoint.y);
    const staleDatasetPoint = await page.evaluate((label) => {
      const card = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
        .find((candidate) => candidate.getAttribute('title') === label);
      if (!card) return null;
      const rect = card.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }, surface.activeDataset?.label || '');
    if (staleDatasetPoint) {
      await page.keyboard.down('Control');
      await page.mouse.click(staleDatasetPoint.x, staleDatasetPoint.y);
      await page.keyboard.up('Control');
    }
    const contextWorkspacePoint = await page.evaluate((label) => {
      const card = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
        .find((candidate) => candidate.getAttribute('title') === label);
      const rect = card.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }, surface.activeWorkspace.label);
    await page.mouse.click(contextWorkspacePoint.x, contextWorkspacePoint.y, { button: 'right' });
    await page.waitForSelector('#dataRepositoryBrowser .trade-resource-context-menu');
    assert(await page.evaluate((itemId) => window.__tradeState.lastDataContextItemId === itemId, surface.activeWorkspace.itemId), 'Data browser did not retain the right-clicked Workspace as the action target');
    await page.evaluate(() => [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
      .find((button) => button.textContent.trim() === 'Add Script…').click());
    await page.waitForSelector('#datasetScriptDialog[open]');
    assert(await page.$eval('#datasetScriptMethodStep', (node) => node.hidden), 'Workspace Add Script shortcut did not skip source selection');
    assert(await page.$eval('#datasetScriptWorkspaceFields', (node) => !node.hidden), 'Workspace Add Script shortcut did not open Workspace fields');
    assert(await page.$eval('#datasetScriptMode', (node) => node.value === 'workspace'), 'Workspace Add Script shortcut selected the wrong source mode');
    assert(await page.$('#datasetScriptEntrypoint') === null, 'Add Script still exposes the removed Entrypoint contract');
    assert(await page.$eval('#datasetScriptWorkspace', (node, workspaceId) => node.value === workspaceId, surface.activeWorkspace.workspaceId), 'Workspace Add Script shortcut did not preselect the clicked Workspace', surface.activeWorkspace);
    await page.waitForFunction(() => document.querySelector('#datasetScriptWorkspacePath')?.options.length > 0
      && !document.querySelector('#datasetScriptWorkspacePath').options[0].textContent.startsWith('Loading'));
    await page.screenshot({ path: '/tmp/trade-add-script-workspace-shortcut-vnc.png', fullPage: true });
    await page.click('#cancelDatasetScriptBtn');
  }

  assert(surface.activeDataset, 'No active Dataset is available for Dataset action checks', surface);
  const datasetPoint = await selectSearchResult(page, surface.activeDataset);
  await page.mouse.click(datasetPoint.x, datasetPoint.y, { button: 'right' });
  await page.waitForSelector('#dataRepositoryBrowser .trade-resource-context-menu');
  const datasetMenu = await page.$$eval('#dataRepositoryBrowser .trade-resource-context-menu button', (buttons) => buttons.map((button) => button.textContent.trim()));
  for (const expected of ['Add Workspace…', 'Download Dataset', 'Replace…', 'Rename…', 'Archive']) {
    assert(datasetMenu.includes(expected), `Dataset menu is missing ${expected}`, datasetMenu);
  }
  await page.evaluate(() => [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
    .find((button) => button.textContent.trim() === 'Rename…').click());
  await page.waitForSelector('#repositoryResourceRenameDialog[open]');
  assert(await page.$eval('#repositoryResourceRenameName', (input) => Boolean(input.value)), 'Rename dialog was not prefilled');
  await page.click('#cancelRepositoryResourceRenameBtn');
  await page.mouse.click(datasetPoint.x, datasetPoint.y, { button: 'right' });
  await page.evaluate(() => [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
    .find((button) => button.textContent.trim() === 'Replace…').click());
  await page.waitForSelector('#datasetReplaceDialog[open]');
  assert(await page.$eval('#datasetReplaceTarget', (node) => Boolean(node.textContent.trim())), 'Replace dialog has no Dataset target');
  await page.click('#cancelDatasetReplaceBtn');
  const datasetOpenPoint = await page.evaluate((label) => {
    const card = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
      .find((candidate) => candidate.getAttribute('title') === label);
    if (!card) throw new Error('Dataset card disappeared before double-click');
    const rect = card.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, surface.activeDataset.label);
  await page.evaluate((label) => [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
    .find((candidate) => candidate.getAttribute('title') === label)
    .dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true })), surface.activeDataset.label);
  try {
    await page.waitForSelector('#datasetWorkspaceDialog[open]');
  } catch (error) {
    console.error(await page.evaluate(() => JSON.stringify({
      browserError: document.querySelector('#dataRepositoryBrowser .trade-resource-error')?.textContent || '',
      openDialogs: [...document.querySelectorAll('dialog[open]')].map((dialog) => dialog.id),
      selected: [...document.querySelectorAll('#dataRepositoryBrowser .file-selected')].map((node) => node.getAttribute('title')),
      scripts: [...document.scripts].map((script) => script.src).filter(Boolean),
    }, null, 2)));
    throw error;
  }
  assert(await page.$('#datasetWorkspaceName') !== null && await page.$('#datasetWorkspaceId') === null,
    'Add Workspace must ask for Name without exposing Engine-owned Workspace ID');
  const workspacePrefill = await page.$$eval('#datasetWorkspaceSelected [data-workspace-dataset-remove]', (buttons) => buttons.map((button) => button.dataset.workspaceDatasetRemove));
  assert(workspacePrefill.includes(surface.activeDataset.datasetId), 'Dataset double-click did not prefill Add Workspace', workspacePrefill);
  const workspaceDialogHeight = await page.$eval('#datasetWorkspaceDialog', (dialog) => dialog.getBoundingClientRect().height);
  assert(workspaceDialogHeight >= 640, 'Add Workspace dialog is still too short', workspaceDialogHeight);
  const anotherDataset = await page.evaluate((excluded) => window.__tradeState.datasets.find((item) => item.status === 'active' && item.datasetId !== excluded), surface.activeDataset.datasetId);
  if (anotherDataset) {
    const fuzzyQuery = (anotherDataset.name || anotherDataset.datasetId).replace(/\s+/g, '').slice(0, 5);
    await page.type('#datasetWorkspaceSearch', fuzzyQuery);
    await page.waitForSelector('#datasetWorkspaceCandidates [data-workspace-dataset-add]');
    await page.evaluate((datasetId) => document.querySelector(`#datasetWorkspaceCandidates [data-workspace-dataset-add="${CSS.escape(datasetId)}"]`)?.click(), anotherDataset.datasetId);
    const added = await page.$$eval('#datasetWorkspaceSelected [data-workspace-dataset-remove]', (buttons) => buttons.map((button) => button.dataset.workspaceDatasetRemove));
    assert(added.includes(anotherDataset.datasetId), 'Clicking a fuzzy-search candidate did not add it to the collection', added);
    await page.click(`#datasetWorkspaceSelected [data-workspace-dataset-remove="${anotherDataset.datasetId}"]`);
    const removed = await page.$$eval('#datasetWorkspaceSelected [data-workspace-dataset-remove]', (buttons) => buttons.map((button) => button.dataset.workspaceDatasetRemove));
    assert(!removed.includes(anotherDataset.datasetId), 'Clicking a selected Dataset did not remove it', removed);
  }
  await page.screenshot({ path: '/tmp/trade-add-workspace-picker-vnc.png', fullPage: true });
  await page.click('#cancelDatasetWorkspaceBtn');

  if (surface.activeScript) {
    const scriptPoint = await selectSearchResult(page, surface.activeScript);
    await page.evaluate((label) => [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')]
      .find((candidate) => candidate.getAttribute('title') === label)
      .dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true })), surface.activeScript.label);
    await page.waitForSelector('#datasetProcessDialog[open]');
    assert(await page.$('#datasetProcessOutputId') === null,
      'Process still exposes Engine-owned output Dataset ID');
    const process = await page.evaluate(() => ({
      script: document.querySelector('#datasetProcessScript').value,
      sourceCount: document.querySelector('#datasetProcessSources').options.length,
      argumentInput: document.querySelector('#datasetProcessArguments')?.tagName,
      parameterFields: document.querySelectorAll('#datasetProcessParameterFields [data-schema-field]').length,
    }));
    assert(process.script === `${surface.activeScript.recipeId}::${surface.activeScript.version}` && process.sourceCount > 0,
      'Script double-click did not open a usable Process dialog', process);
    assert(process.argumentInput === 'TEXTAREA' && process.parameterFields === 0,
      'Process must accept call-time argv without an Engine parameter form', process);
    await page.click('#cancelDatasetProcessBtn');
  }

  await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForSelector('#pipelineRepositoryBrowser .trade-resource-browser-shell');
  const pipeline = await page.evaluate(() => window.__tradeState.repositoryCatalogs.pipelines.items.find((item) => item.status !== 'archived'));
  if (pipeline) {
    const point = await selectSearchResult(page, pipeline, 'pipelineRepositoryBrowser');
    await page.mouse.click(point.x, point.y, { button: 'right' });
    await page.waitForSelector('#pipelineRepositoryBrowser .trade-resource-context-menu');
    const menu = await page.$$eval('#pipelineRepositoryBrowser .trade-resource-context-menu button', (buttons) => buttons.map((button) => button.textContent.trim()));
    assert(menu.includes('Rename…'), 'Pipeline context menu has no Rename action', menu);
    await page.evaluate(() => [...document.querySelectorAll('#pipelineRepositoryBrowser .trade-resource-context-menu button')]
      .find((button) => button.textContent.trim() === 'Rename…').click());
    await page.waitForSelector('#repositoryResourceRenameDialog[open]');
    await page.click('#cancelRepositoryResourceRenameBtn');
  }

  await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForSelector('#backtestResourceBrowser .trade-resource-browser-shell');
  const resultItem = await page.evaluate(() => window.__tradeState.repositoryCatalogs.backtest.items.find((item) => item.sourceRepository === 'results'));
  if (resultItem) {
    const point = await selectSearchResult(page, resultItem, 'backtestResourceBrowser');
    await page.evaluate((label) => [...document.querySelectorAll('#backtestResourceBrowser .file-item-container')]
      .find((candidate) => candidate.getAttribute('title') === label)
      .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 900, clientY: 500 })), resultItem.label);
    await page.waitForSelector('#backtestResourceBrowser .trade-resource-context-menu');
    const menu = await page.$$eval('#backtestResourceBrowser .trade-resource-context-menu button', (buttons) => buttons.map((button) => button.textContent.trim()));
    assert(menu.includes('Rename…'), 'Backtest Result context menu has no Rename action', menu);
    await page.evaluate(() => [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-context-menu button')]
      .find((button) => button.textContent.trim() === 'Rename…').click());
    await page.waitForSelector('#repositoryResourceRenameDialog[open]');
    await page.click('#cancelRepositoryResourceRenameBtn');
  }

  const resultBacktestId = await page.evaluate(() => (
    window.__tradeState.repositoryCatalogs.backtest.items.find((item) => item.visualizable && item.status !== 'archived')?.backtestId || ''
  ));
  assert(resultBacktestId, 'No visualizable Result is available');
  await page.goto(`${BASE}/result?backtestId=${encodeURIComponent(resultBacktestId)}`, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(() => Boolean(window.__tradeState?.selectedBacktest?.backtestId), { timeout: 30000 });
  await page.waitForSelector('[data-temp-instance="0"]');
  const templateOption = await page.$eval('[data-temp-module-select="0"]', (select) => (
    [...select.options].find((option) => option.value)?.value || ''
  ));
  let resultInstance = null;
  if (templateOption) {
    await page.select('[data-temp-module-select="0"]', templateOption);
    await page.waitForFunction(() => Boolean(document.querySelector('[data-temp-instance="0"]')?.value));
    resultInstance = await page.evaluate(() => ({
      type: document.querySelector('[data-temp-instance="0"]')?.type,
      value: document.querySelector('[data-temp-instance="0"]')?.value,
      outputs: [...document.querySelectorAll('[data-temp-outputs-fields="0"] input')].map((input) => input.value),
    }));
    assert(resultInstance.type === 'hidden' && /^tmp_[a-z0-9]+$/i.test(resultInstance.value),
      'Results temporary Module identity is still user-editable or name-derived', resultInstance);
    assert(resultInstance.outputs.length > 0 && resultInstance.outputs.every((value) => value && !value.includes('mod_')),
      'Default Data Keys still leak opaque Module identity', resultInstance);
  }

  await page.screenshot({ path: '/tmp/trade-data-filesystem-actions-vnc.png', fullPage: true });
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
  assert(serverErrors.length === 0, 'Browser received server errors', serverErrors);
  await browser.close();
  console.log(JSON.stringify({ surface, blankMenu, datasetMenu, workspacePrefill, resultInstance, pageErrors, serverErrors }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
