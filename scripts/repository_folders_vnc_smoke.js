const { execFileSync } = require('node:child_process');
const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'https://trade.duckduckrun.com';

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

function createSmokeSession() {
  const source = [
    'import json, secrets, time',
    'from engine.control import api as control; from engine.control import auth as trade_auth',
    'config = control.load_config(".runtime/strategy-control.json")',
    'trade_auth.ensure_default_user(config)',
    'token = secrets.token_urlsafe(32)',
    'csrf = secrets.token_urlsafe(32)',
    'now = int(time.time())',
    'with trade_auth.connect(config) as connection:',
    '    user = connection.execute("SELECT user_id FROM users WHERE status = ? ORDER BY created_at LIMIT 1", ("active",)).fetchone()',
    '    connection.execute("INSERT INTO sessions (token_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)", (trade_auth.opaque_token_hash(token), user["user_id"], trade_auth.opaque_token_hash(csrf), now, now + 3600, now))',
    '    connection.commit()',
    'print(json.dumps({"token": token, "csrf": csrf}))',
  ].join('\n');
  return JSON.parse(execFileSync('python3', ['-c', source], { encoding: 'utf8' }));
}

function deleteSmokeSession(token) {
  const source = [
    'from engine.control import api as control; from engine.control import auth as trade_auth; import sys',
    'config = control.load_config(".runtime/strategy-control.json")',
    'with trade_auth.connect(config) as connection:',
    '    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (trade_auth.opaque_token_hash(sys.argv[1]),))',
    '    connection.commit()',
  ].join('\n');
  execFileSync('python3', ['-c', source, token]);
}

async function postFolderAction(page, payload) {
  return page.evaluate(async (body) => {
    const response = await fetch('/api/repository-folders', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': window.__tradeAuth.csrfToken,
      },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `folder action returned ${response.status}`);
    return result;
  }, payload);
}

async function clickFolder(page, containerId, name) {
  await page.evaluate((rootId, expected) => {
    const button = [...document.querySelectorAll(`#${rootId} [data-embedded-folder]`)]
      .find((candidate) => candidate.querySelector('span:nth-child(2)')?.textContent.trim() === expected);
    if (!button) throw new Error(`Folder button '${expected}' was not found`);
    button.click();
  }, containerId, name);
}

async function createFolderThroughUi(page, repository, containerId, name) {
  await page.click(`#${containerId} [data-embedded-new-folder]`);
  await page.waitForSelector('#repositoryFolderDialog[open]');
  await page.evaluate((value) => {
    const input = document.querySelector('#repositoryFolderName');
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, name);
  await page.click('#confirmRepositoryFolderBtn');
  await page.waitForFunction(() => !document.querySelector('#repositoryFolderDialog')?.open);
  return page.evaluate((repo, rootId) => ({
    folderId: window.__tradeState.repositoryFolderSelections[repo],
    path: document.querySelector(`#${rootId} .embedded-repository-toolbar strong`)?.textContent,
  }), repository, containerId);
}

async function moveThroughUi(page, repository, containerId, itemId, folderId) {
  await page.evaluate((repo, rootId, id, targetFolder) => {
    const root = document.querySelector(`#${rootId}`);
    const button = [...root.querySelectorAll('[data-embedded-folder]')]
      .find((candidate) => candidate.dataset.embeddedFolder === '*');
    button?.click();
    const currentRoot = document.querySelector(`#${rootId}`);
    const card = [...currentRoot.querySelectorAll('.repository-card')]
      .find((candidate) => candidate.querySelector('[data-repository-move]')?.dataset.repositoryMove === id);
    if (!card) throw new Error(`Repository card '${id}' was not found`);
    const select = card.querySelector('[data-repository-move-select]');
    select.value = targetFolder;
    card.querySelector('[data-repository-move]').click();
  }, repository, containerId, itemId, folderId);
  await page.waitForFunction((repo, id, expectedFolder) => (
    window.__tradeState.repositoryCatalogs[repo]
      ?.items?.find((item) => item.itemId === id)?.folderId === expectedFolder
  ), {}, repository, itemId, folderId);
}

async function main() {
  const session = createSmokeSession();
  let browser;
  let page;
  const cleanup = [];
  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--disable-gpu'],
    });
    page = await browser.newPage();
    await page.setViewport({ width: 1720, height: 1000 });
    await page.setCookie(
      { name: 'trade_session', value: session.token, url: BASE, secure: true, httpOnly: true, sameSite: 'Strict' },
      { name: 'trade_csrf', value: session.csrf, url: BASE, secure: true, httpOnly: false, sameSite: 'Strict' },
    );
    const pageErrors = [];
    const failedResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    await page.goto(`${BASE}/modules`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => window.__tradeState?.repositoryCatalogs?.modules?.items?.length > 0);
    assert(!(await page.$('.nav-btn[data-view="repositories"]')), 'A standalone Repositories page still exists');
    const moduleAudit = await page.evaluate(() => {
      const catalog = window.__tradeState.repositoryCatalogs.modules;
      return {
        fixed: catalog.folders.filter((folder) => folder.fixed).map((folder) => folder.name),
        environmentModules: catalog.items.filter((item) => item.kind === 'Environment').map((item) => ({
          itemId: item.itemId,
          runtimeClass: item.runtimeClass,
          folderPath: item.folderPath,
        })),
      };
    });
    assert(moduleAudit.fixed.includes('Environment') && moduleAudit.fixed.includes('Signal'),
      'Fixed Module type folders are incomplete', moduleAudit);
    assert(moduleAudit.environmentModules.length === 12
      && moduleAudit.environmentModules.every((item) => item.runtimeClass && item.folderPath === '/Environment'),
    'Environment runtime modules are missing or are presentation-only definitions', moduleAudit.environmentModules);

    await clickFolder(page, 'moduleRepositoryBrowser', 'Environment');
    await page.waitForFunction(() => document.querySelectorAll('#moduleRepositoryBrowser .repository-card').length === 12);
    const fixedActions = await page.evaluate(() => ({
      renameDisabled: document.querySelector('#moduleRepositoryBrowser [data-embedded-rename-folder]').disabled,
      deleteDisabled: document.querySelector('#moduleRepositoryBrowser [data-embedded-delete-folder]').disabled,
    }));
    assert(fixedActions.renameDisabled && fixedActions.deleteDisabled, 'Fixed Module folders can be mutated', fixedActions);

    await clickFolder(page, 'moduleRepositoryBrowser', 'Signal');
    const signalFolder = await createFolderThroughUi(page, 'modules', 'moduleRepositoryBrowser', 'VNC Smoke');
    cleanup.push({ repository: 'modules', folderId: signalFolder.folderId });
    assert(signalFolder.path === '/Signal/VNC Smoke', 'Nested Module folder path is incorrect', signalFolder);
    const signalItem = await page.evaluate(() => {
      const item = window.__tradeState.repositoryCatalogs.modules.items.find((candidate) => candidate.kind === 'Signal');
      return { itemId: item.itemId, folderId: item.folderId };
    });
    cleanup.push({ repository: 'modules', itemId: signalItem.itemId, folderId: signalItem.folderId });
    await moveThroughUi(page, 'modules', 'moduleRepositoryBrowser', signalItem.itemId, signalFolder.folderId);

    await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]');
    await page.$eval('#pipelineRepositoryBrowser .file-item-container[data-trade-item-id]', (node) => {
      node.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    });
    await page.waitForFunction(() => location.pathname === '/pipeline/builder', { timeout: 30000 });
    await page.waitForSelector('[data-load-stage="signal"]');
    const signalGrouping = await page.evaluate((itemId) => {
      const select = document.querySelector('[data-load-stage="signal"]');
      const option = [...select.options].find((candidate) => candidate.value === itemId);
      const trigger = select.nextElementSibling?.querySelector('.hierarchical-select-trigger');
      trigger?.click();
      const menu = select.__hierarchicalMenu;
      return {
        group: option?.parentElement?.label || '',
        value: option?.value || '',
        folders: [...menu.querySelectorAll('summary')].map((node) => node.textContent.trim()),
        visibleNativeSelect: getComputedStyle(select).opacity !== '0',
      };
    }, signalItem.itemId);
    assert(signalGrouping.group === '/Signal/VNC Smoke'
      && signalGrouping.folders.includes('Signal') && signalGrouping.folders.includes('VNC Smoke')
      && !signalGrouping.visibleNativeSelect,
    'Pipeline Module selector is not a real hierarchical folder menu', signalGrouping);
    await page.screenshot({ path: '/tmp/trade-pipeline-module-folder-menu-vnc.png', fullPage: true });

    await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => window.__tradeState.repositoryCatalogs.datasets
      && document.querySelector('#datasetRepositoryBrowser .repository-card'));
    const dataSurface = await page.evaluate(() => ({
      datasetCards: document.querySelectorAll('#datasetRepositoryBrowser .repository-card').length,
      samplerCards: document.querySelectorAll('#samplerRepositoryBrowser .repository-card').length,
      workspaceMultiMenu: Boolean(document.querySelector('#datasetWorkspaceSources + .hierarchical-select')),
      workspaceMultiple: document.querySelector('#datasetWorkspaceSources')?.multiple,
      workspaceNext: document.querySelector('#datasetWorkspaceSources')?.nextElementSibling?.className || '',
    }));
    assert(dataSurface.datasetCards > 0 && dataSurface.samplerCards > 0 && dataSurface.workspaceMultiMenu,
      'Data page retained a flat Dataset/Sampler list or flat multi-select', dataSurface);
    const datasetParent = await createFolderThroughUi(page, 'datasets', 'datasetRepositoryBrowser', 'VNC Smoke');
    cleanup.push({ repository: 'datasets', folderId: datasetParent.folderId });
    const datasetChild = await createFolderThroughUi(page, 'datasets', 'datasetRepositoryBrowser', 'Nested');
    cleanup.unshift({ repository: 'datasets', folderId: datasetChild.folderId });
    assert(datasetChild.path === '/VNC Smoke/Nested', 'Arbitrary Dataset folder depth is incorrect', datasetChild);
    const datasetItem = await page.evaluate(() => {
      const item = window.__tradeState.repositoryCatalogs.datasets.items.find((candidate) => candidate.status === 'active');
      return { itemId: item.itemId, folderId: item.folderId };
    });
    assert(datasetItem.itemId, 'No Dataset was available for the folder smoke test');
    cleanup.unshift({ repository: 'datasets', itemId: datasetItem.itemId, folderId: datasetItem.folderId });
    await moveThroughUi(page, 'datasets', 'datasetRepositoryBrowser', datasetItem.itemId, datasetChild.folderId);

    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#backtestDataset option');
    const datasetGrouping = await page.evaluate((datasetId) => {
      const select = document.querySelector('#backtestDataset');
      const option = [...select.options]
        .find((candidate) => candidate.value === datasetId);
      select.nextElementSibling.querySelector('.hierarchical-select-trigger').click();
      return {
        group: option?.parentElement?.label || '',
        value: option?.value || '',
        folders: [...select.__hierarchicalMenu.querySelectorAll('summary')].map((node) => node.textContent.trim()),
      };
    }, datasetItem.itemId);
    assert(datasetGrouping.group === '/VNC Smoke/Nested'
      && datasetGrouping.folders.includes('VNC Smoke') && datasetGrouping.folders.includes('Nested'),
    'Backtest Dataset selector is not a real hierarchical folder menu', datasetGrouping);
    const backtestSurface = await page.evaluate(() => ({
      resultCards: document.querySelectorAll('#backtestRepositoryBrowser .repository-card').length,
      hierarchicalInputs: ['backtestDataset', 'backtestSampler', 'backtestEnvironmentSelect', 'backtestPipelineSelect']
        .map((id) => Boolean(document.querySelector(`#${id} + .hierarchical-select`))),
    }));
    assert(backtestSurface.resultCards > 0 && backtestSurface.hierarchicalInputs.every(Boolean),
      'Backtest retained a flat Result list or flat repository selectors', backtestSurface);

    await page.goto(`${BASE}/environment`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => (
      window.__tradeState?.repositoryCatalogs?.environments?.items?.length > 0
      && document.querySelector('#environmentRepositoryBrowser .trade-resource-browser-shell')
    ));
    const environmentSurface = await page.evaluate(() => ({
      resources: window.__tradeState.repositoryCatalogs.environments.items.length,
      browser: Boolean(document.querySelector('#environmentRepositoryBrowser .trade-resource-browser-shell')),
      blueprintHidden: document.querySelector('#environmentBlueprintSection')?.hidden,
      blueprintMounted: Boolean(document.querySelector('#environmentGraphBuilder')?.__liteGraphGraph),
    }));
    assert(environmentSurface.resources > 0 && environmentSurface.browser
      && environmentSurface.blueprintHidden && !environmentSurface.blueprintMounted,
    'Environment first-level page did not remain a Browser-only resource surface', environmentSurface);

    const resultBacktestId = await page.evaluate(() => (
      window.__tradeState.repositoryCatalogs.backtest.items.find((item) => item.visualizable && item.status !== 'archived')?.backtestId || ''
    ));
    assert(resultBacktestId, 'No visualizable Result is available for the dedicated Result route');
    await page.goto(`${BASE}/result?backtestId=${encodeURIComponent(resultBacktestId)}`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction((expected) => window.__tradeState?.selectedBacktest?.backtestId === expected,
      { timeout: 30000 }, resultBacktestId);
    assert(!(await page.$('#resultBacktest')), 'Result retained the obsolete cross-Backtest selector');

    await page.goto(`${BASE}/modules`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForFunction(() => window.__tradeState?.repositoryCatalogs?.modules?.items?.length > 0);
    await clickFolder(page, 'moduleRepositoryBrowser', 'Environment');
    await page.waitForFunction(() => document.querySelectorAll('#moduleRepositoryBrowser .repository-card').length === 12);
    await page.screenshot({ path: '/tmp/trade-repository-folders-vnc.png', fullPage: true });
    assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);
    assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);

    console.log(JSON.stringify({ moduleAudit, signalGrouping, dataSurface, datasetGrouping, backtestSurface, environmentSurface, pageErrors, failedResponses }, null, 2));
  } finally {
    if (page) {
      for (const entry of cleanup.filter((item) => item.itemId)) {
        try {
          await postFolderAction(page, { action: 'moveItem', ...entry });
        } catch {}
      }
      for (const entry of cleanup.filter((item) => !item.itemId)) {
        try {
          await postFolderAction(page, { action: 'delete', ...entry });
        } catch {}
      }
    }
    if (browser) await browser.close();
    deleteSmokeSession(session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
