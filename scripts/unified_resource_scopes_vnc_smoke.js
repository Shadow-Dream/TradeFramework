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

async function waitBrowser(page, id) {
  await page.waitForSelector(`#${id} .trade-resource-browser-shell`, { timeout: 30000 });
}

async function openTreeFolder(page, id, name) {
  await page.evaluate((containerId, folderName) => {
    const row = [...document.querySelectorAll(`#${containerId} .sb-folders-list-item`)]
      .find((candidate) => candidate.querySelector('.sb-folder-name')?.textContent.trim() === folderName);
    if (!row) throw new Error(`Folder '${folderName}' is missing from ${containerId}`);
    row.click();
  }, id, name);
  await page.waitForFunction((containerId, folderName) => (
    [...document.querySelectorAll(`#${containerId} .sb-folders-list-item.active-list-item .sb-folder-name`)]
      .some((node) => node.textContent.trim() === folderName)
  ), {}, id, name);
}

async function main() {
  const session = createSession();
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--disable-gpu'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1720, height: 1000 });
    await page.setCookie(
      { name: 'trade_session', value: session.token, url: BASE, secure: true, httpOnly: true, sameSite: 'Strict' },
      { name: 'trade_csrf', value: session.csrf, url: BASE, secure: true, sameSite: 'Strict' },
    );
    const pageErrors = [];
    const failedResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    await page.goto(`${BASE}/modules`, { waitUntil: 'networkidle2', timeout: 30000 });
    await waitBrowser(page, 'moduleRepositoryBrowser');
    const moduleSurface = await page.evaluate(() => {
      const catalog = window.__tradeState.repositoryCatalogs.modules;
      const fixedFolders = catalog.folders
        .filter((folder) => folder.fixed && folder.path.split('/').filter(Boolean).length === 1)
        .map((folder) => folder.path)
        .sort();
      const kinds = [...new Set(catalog.items.map((item) => item.kind))].sort();
      return {
        fixedFolders,
        kinds,
        environmentFolder: catalog.folders.some((folder) => folder.path === '/Environment'),
        environmentItems: catalog.items.filter((item) => /Environment|FeeModel|FillModel/.test(item.kind)).length,
        browserCount: document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-browser-shell').length,
      };
    });
    const moduleKinds = ['Constraint', 'Signal', 'Target', 'Universe'];
    assert(moduleSurface.fixedFolders.join('|') === moduleKinds.map((kind) => `/${kind}`).sort().join('|')
      && moduleSurface.kinds.join('|') === moduleKinds.sort().join('|')
      && !moduleSurface.environmentFolder
      && moduleSurface.environmentItems === 0
      && moduleSurface.browserCount === 1,
    'Module filesystem still contains Backtest Environment resources', moduleSurface);
    const moduleSearch = await page.evaluate(() => ({
      oldInput: Boolean(document.getElementById('moduleFilter')),
      comboboxes: document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-search-control').length,
      sidebarSearches: document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-browser-sidebar > .trade-resource-search-row').length,
      mainSearches: document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-browser-main .trade-resource-search-row').length,
    }));
    assert(!moduleSearch.oldInput && moduleSearch.comboboxes === 1
      && moduleSearch.sidebarSearches === 1 && moduleSearch.mainSearches === 0,
      'Module repository search is not using the hierarchical combobox', moduleSearch);
    await page.click('#moduleRepositoryBrowser .trade-resource-search-toggle');
    await page.waitForSelector('#moduleRepositoryBrowser .trade-resource-search-menu');
    moduleSearch.folders = await page.$$eval(
      '#moduleRepositoryBrowser .trade-resource-search-folder summary',
      (nodes) => nodes.map((node) => node.textContent.trim()),
    );
    assert(moduleKinds.every((kind) => moduleSearch.folders.includes(kind)) && moduleSearch.folders.includes('BuiltIn'),
      'Module search combobox does not mirror the folder hierarchy', moduleSearch);
    const moduleSearchTarget = await page.evaluate(() => {
      const items = window.__tradeState.repositoryCatalogs.modules.items;
      return items.find((item) => /atr/i.test(`${item.label} ${item.moduleId}`)) || items[0];
    });
    const moduleFuzzyQuery = String(moduleSearchTarget.label || moduleSearchTarget.moduleId)
      .toLowerCase().replace(/[^a-z0-9]/g, '').replace(/[aeiou]/g, '').slice(0, 6);
    const moduleSearchInput = '#moduleRepositoryBrowser .trade-resource-search-control input';
    await page.click(moduleSearchInput, { clickCount: 3 });
    await page.type(moduleSearchInput, moduleFuzzyQuery);
    await page.waitForFunction((targetId) => (
      [...document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-search-item')]
        .some((item) => item.textContent.includes(targetId))
    ), {}, moduleSearchTarget.label || moduleSearchTarget.moduleId);
    moduleSearch.fuzzyQuery = moduleFuzzyQuery;
    moduleSearch.target = moduleSearchTarget.label || moduleSearchTarget.moduleId;
    await page.evaluate((targetId) => {
      const item = [...document.querySelectorAll('#moduleRepositoryBrowser .trade-resource-search-item')]
        .find((candidate) => candidate.textContent.includes(targetId));
      item?.click();
    }, moduleSearch.target);
    await page.waitForFunction((targetId) => {
      const input = document.querySelector('#moduleRepositoryBrowser .trade-resource-search-control input');
      const cards = [...document.querySelectorAll('#moduleRepositoryBrowser .file-item-container')];
      return input?.value === targetId && cards.some((card) => card.title === targetId);
    }, {}, moduleSearch.target);
    moduleSearch.selectionRevealed = true;
    await page.screenshot({ path: '/tmp/trade-unified-modules-vnc.png', fullPage: true });

    await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
    await waitBrowser(page, 'pipelineRepositoryBrowser');
    const pipelineSearchTarget = await page.evaluate(() => window.__tradeState.repositoryCatalogs.pipelines.items[0]);
    const pipelineSearch = await page.evaluate(() => ({
      comboboxes: document.querySelectorAll('#pipelineRepositoryBrowser .trade-resource-search-control').length,
      sidebarSearches: document.querySelectorAll('#pipelineRepositoryBrowser .trade-resource-browser-sidebar > .trade-resource-search-row').length,
      mainSearches: document.querySelectorAll('#pipelineRepositoryBrowser .trade-resource-browser-main .trade-resource-search-row').length,
    }));
    assert(pipelineSearchTarget && pipelineSearch.comboboxes === 1
      && pipelineSearch.sidebarSearches === 1 && pipelineSearch.mainSearches === 0,
    'Pipeline filesystem is missing the sidebar search combobox', pipelineSearch);
    const pipelineFuzzyQuery = String(pipelineSearchTarget.label || pipelineSearchTarget.itemId)
      .toLowerCase().replace(/[^a-z0-9]/g, '').replace(/[aeiou]/g, '').slice(0, 6);
    await page.type('#pipelineRepositoryBrowser .trade-resource-search-control input', pipelineFuzzyQuery);
    await page.waitForFunction((targetId) => (
      [...document.querySelectorAll('#pipelineRepositoryBrowser .trade-resource-search-item')]
        .some((item) => item.textContent.includes(targetId))
    ), {}, pipelineSearchTarget.label || pipelineSearchTarget.itemId);
    pipelineSearch.fuzzyQuery = pipelineFuzzyQuery;
    pipelineSearch.target = pipelineSearchTarget.label || pipelineSearchTarget.itemId;

    await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
    await waitBrowser(page, 'dataRepositoryBrowser');
    const dataSurface = await page.evaluate(() => {
      const catalog = window.__tradeState.repositoryCatalogs.data;
      const filterTypes = [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-type-filter button')]
        .map((button) => button.dataset.resourceTypeFilter);
      return {
        browserCount: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-browser-shell').length,
        duplicateBrowsers: ['datasetRepositoryBrowser', 'samplerRepositoryBrowser', 'scriptRepositoryBrowser', 'workspaceRepositoryBrowser']
          .filter((id) => document.getElementById(id)).length,
        resourceTypes: [...new Set(catalog.items.map((item) => item.resourceType))].sort(),
        filterTypes,
        typeIcons: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-type-icon').length,
        typeBadges: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-type-badge').length,
        sampleCardHtml: document.querySelector('#dataRepositoryBrowser .file-item-container[data-resource-type]')?.innerHTML || '',
        forbiddenTypeFolders: catalog.folders.filter((folder) => ['/Dataset', '/Sampler', '/Script', '/Workspace'].includes(folder.path)),
      };
    });
    assert(dataSurface.browserCount === 1
      && dataSurface.duplicateBrowsers === 0
      && dataSurface.resourceTypes.includes('Dataset')
      && dataSurface.resourceTypes.includes('Sampler')
      && dataSurface.filterTypes.includes('all')
      && dataSurface.filterTypes.includes('dataset')
      && dataSurface.filterTypes.includes('sampler')
      && dataSurface.filterTypes.includes('script')
      && dataSurface.filterTypes.includes('workspace')
      && dataSurface.typeIcons > 0
      && dataSurface.typeBadges > 0
      && dataSurface.forbiddenTypeFolders.length === 0,
    'Data resources are not presented in one type-filtered filesystem', dataSurface);
    const dataSearch = await page.evaluate(() => ({
      oldInput: Boolean(document.getElementById('dataSearch')),
      comboboxes: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-search-control').length,
      sidebarSearches: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-browser-sidebar > .trade-resource-search-row').length,
      mainSearches: document.querySelectorAll('#dataRepositoryBrowser .trade-resource-browser-main .trade-resource-search-row').length,
    }));
    assert(!dataSearch.oldInput && dataSearch.comboboxes === 1
      && dataSearch.sidebarSearches === 1 && dataSearch.mainSearches === 0,
      'Data filesystem search is not using the fuzzy combobox', dataSearch);
    const dataSearchTarget = await page.evaluate(() => window.__tradeState.repositoryCatalogs.data.items[0]);
    const dataFuzzyQuery = String(dataSearchTarget.label || dataSearchTarget.itemId)
      .toLowerCase().replace(/[^a-z0-9]/g, '').replace(/[aeiou]/g, '').slice(0, 6);
    const dataSearchInput = '#dataRepositoryBrowser .trade-resource-search-control input';
    await page.type(dataSearchInput, dataFuzzyQuery);
    await page.waitForFunction((targetId) => (
      [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-search-item')]
        .some((item) => item.textContent.includes(targetId))
    ), {}, dataSearchTarget.label || dataSearchTarget.itemId);
    dataSearch.fuzzyQuery = dataFuzzyQuery;
    dataSearch.target = dataSearchTarget.label || dataSearchTarget.itemId;
    await page.evaluate((targetId) => {
      const item = [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-search-item')]
        .find((candidate) => candidate.textContent.includes(targetId));
      item?.click();
    }, dataSearch.target);
    await page.waitForFunction((targetId) => {
      const input = document.querySelector('#dataRepositoryBrowser .trade-resource-search-control input');
      const cards = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container')];
      return input?.value === targetId && cards.some((card) => card.title === targetId);
    }, {}, dataSearch.target);
    dataSearch.selectionRevealed = true;
    await page.click('#dataRepositoryBrowser .trade-resource-search-clear');
    const downloadSurface = await page.evaluate(() => ({
      topButton: Boolean(document.getElementById('downloadSelectedDatasetsBtn')),
      topToolbar: Boolean(document.querySelector('.dataset-download-toolbar')),
      inspectorButton: [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-inspector button')]
        .some((button) => button.textContent.trim() === 'Download'),
    }));
    assert(!downloadSurface.topButton && !downloadSurface.topToolbar && !downloadSurface.inspectorButton,
      'Dataset download still has a non-context-menu entry point', downloadSurface);
    await page.click('#dataRepositoryBrowser [data-resource-type-filter="dataset"]');
    await page.waitForFunction(() => {
      const cards = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container[data-resource-type]')];
      return cards.length > 0 && cards.every((card) => card.dataset.resourceType === 'dataset');
    });
    await page.click('#dataRepositoryBrowser .file-item-container[data-resource-type="dataset"]', { button: 'right' });
    await page.waitForSelector('#dataRepositoryBrowser .trade-resource-context-menu button');
    const contextDownloadLabel = await page.$$eval(
      '#dataRepositoryBrowser .trade-resource-context-menu button',
      (buttons) => buttons.map((button) => button.textContent.trim()).find((label) => label === 'Download Dataset') || '',
    );
    assert(contextDownloadLabel === 'Download Dataset',
      'Dataset right-click menu does not expose the download action', { contextDownloadLabel });
    await page.evaluate(() => {
      window.__datasetContextDownloadHref = '';
      const originalClick = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function captureDatasetDownload() {
        if (this.href.includes('/api/data/datasets/download?')) {
          window.__datasetContextDownloadHref = this.href;
          return;
        }
        return originalClick.call(this);
      };
    });
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-context-menu button')]
        .find((candidate) => candidate.textContent.trim() === 'Download Dataset');
      button?.click();
    });
    await page.waitForFunction(() => window.__datasetContextDownloadHref.includes('datasetId='));
    const contextDownload = {
      label: contextDownloadLabel,
      url: await page.evaluate(() => window.__datasetContextDownloadHref),
    };
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('#dataRepositoryBrowser .trade-resource-type-filter button')]
        .find((candidate) => candidate.textContent.includes('Sampler'));
      button?.click();
    });
    await page.waitForFunction(() => {
      const cards = [...document.querySelectorAll('#dataRepositoryBrowser .file-item-container[data-resource-type]')];
      return cards.length > 0 && cards.every((card) => card.dataset.resourceType === 'sampler');
    });
    await page.screenshot({ path: '/tmp/trade-unified-data-vnc.png', fullPage: true });

    await page.goto(`${BASE}/backtests`, { waitUntil: 'networkidle2', timeout: 30000 });
    await waitBrowser(page, 'backtestResourceBrowser');
    const backtestSurface = await page.evaluate(() => {
      const catalog = window.__tradeState.repositoryCatalogs.backtest;
      return {
        browserCount: document.querySelectorAll('#backtestResourceBrowser .trade-resource-browser-shell').length,
        resourceTypes: [...new Set(catalog.items.map((item) => item.resourceType))].sort(),
        filterTypes: [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-type-filter button')]
          .map((button) => button.dataset.resourceTypeFilter),
        duplicateBacktestCards: catalog.items.filter((item) => item.resourceType === 'Backtest').length,
      };
    });
    assert(backtestSurface.browserCount === 1
      && backtestSurface.resourceTypes.join('|') === 'Result'
      && backtestSurface.filterTypes.includes('result')
      && backtestSurface.duplicateBacktestCards === 0,
    'Backtest filesystem must contain Results only; Environment is an independent resource', backtestSurface);
    const backtestSearchTarget = await page.evaluate(() => (
      window.__tradeState.repositoryCatalogs.backtest.items.find((item) => item.resourceType === 'Result')
    ));
    const backtestSearch = await page.evaluate(() => ({
      comboboxes: document.querySelectorAll('#backtestResourceBrowser .trade-resource-search-control').length,
      sidebarSearches: document.querySelectorAll('#backtestResourceBrowser .trade-resource-browser-sidebar > .trade-resource-search-row').length,
      mainSearches: document.querySelectorAll('#backtestResourceBrowser .trade-resource-browser-main .trade-resource-search-row').length,
    }));
    assert(backtestSearchTarget && backtestSearch.comboboxes === 1
      && backtestSearch.sidebarSearches === 1 && backtestSearch.mainSearches === 0,
    'Backtest filesystem is missing the sidebar search combobox', backtestSearch);
    const backtestFuzzyQuery = String(backtestSearchTarget.label || backtestSearchTarget.itemId)
      .toLowerCase().replace(/[^a-z0-9]/g, '').replace(/[aeiou]/g, '').slice(0, 6);
    await page.type('#backtestResourceBrowser .trade-resource-search-control input', backtestFuzzyQuery);
    await page.waitForFunction((targetId) => (
      [...document.querySelectorAll('#backtestResourceBrowser .trade-resource-search-item')]
        .some((item) => item.textContent.includes(targetId))
    ), {}, backtestSearchTarget.label || backtestSearchTarget.itemId);
    backtestSearch.fuzzyQuery = backtestFuzzyQuery;
    backtestSearch.target = backtestSearchTarget.label || backtestSearchTarget.itemId;
    await page.click('#backtestResourceBrowser .trade-resource-search-clear');
    const environmentSurface = await page.evaluate(() => ({
      folders: [...document.querySelectorAll('#backtestResourceBrowser .sb-folder-name')].map((node) => node.textContent.trim()),
      environmentCards: [...document.querySelectorAll('#backtestResourceBrowser .file-item-container[data-resource-type="environment"]')].length,
    }));
    assert(environmentSurface.environmentCards > 0
      && backtestSurface.componentKinds.every((kind) => environmentSurface.folders.includes(kind)),
    'Environment resources or component-type directories are missing from Backtest', environmentSurface);
    await page.screenshot({ path: '/tmp/trade-unified-backtest-vnc.png', fullPage: true });

    assert(!pageErrors.length, 'Browser runtime errors occurred', pageErrors);
    assert(!failedResponses.length, 'Server returned 5xx responses', failedResponses);
    console.log(JSON.stringify({
      moduleSurface,
      moduleSearch,
      pipelineSearch,
      dataSurface,
      dataSearch,
      downloadSurface,
      contextDownload,
      backtestSurface,
      backtestSearch,
      environmentSurface,
      screenshots: [
        '/tmp/trade-unified-modules-vnc.png',
        '/tmp/trade-unified-data-vnc.png',
        '/tmp/trade-unified-backtest-vnc.png',
      ],
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    deleteSession(session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exitCode = 1;
});
