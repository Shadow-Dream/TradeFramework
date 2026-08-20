const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'https://trade.duckduckrun.com';
const FIXTURE = path.resolve('scripts/fixtures/lifecycle_runner.py');

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

async function openBlankModuleContext(page) {
  const main = await page.$('#moduleRepositoryBrowser .trade-resource-browser-main');
  const box = await main.boundingBox();
  await page.mouse.click(box.x + box.width - 28, box.y + box.height - 28, { button: 'right' });
  await page.waitForSelector('.trade-resource-context-menu[data-context-repository="modules"]');
}

async function openModuleContext(page, itemId) {
  await selectModule(page, itemId);
  const selector = `#moduleRepositoryBrowser .file-item-container[data-trade-item-id="${itemId}"]`;
  await page.click(selector, { button: 'right' });
  await page.waitForSelector('.trade-resource-context-menu[data-context-repository="modules"]');
}

async function clickModuleContext(page, action) {
  await page.click(`[data-module-context-action="${action}"]`);
}

async function waitCatalogItem(page, itemId) {
  await page.waitForFunction((key) => (
    window.__tradeState.repositoryCatalogs.modules.items.some((item) => item.itemId === key)
  ), {}, itemId);
}

async function selectModule(page, itemId) {
  const input = '#moduleRepositoryBrowser .trade-resource-search-control input';
  const clear = await page.$('#moduleRepositoryBrowser .trade-resource-search-clear');
  if (clear) await clear.click();
  await page.click(input, { clickCount: 3 });
  await page.type(input, itemId.split('/')[1]);
  await page.waitForSelector(`#moduleRepositoryBrowser .trade-resource-search-item[data-trade-item-id="${itemId}"]`);
  await page.click(`#moduleRepositoryBrowser .trade-resource-search-item[data-trade-item-id="${itemId}"]`);
  await page.waitForSelector(`#moduleRepositoryBrowser .file-item-container[data-trade-item-id="${itemId}"]`);
  await page.click(`#moduleRepositoryBrowser .file-item-container[data-trade-item-id="${itemId}"]`);
  await page.waitForFunction((key) => window.__tradeState.selectedModuleRepositoryItem?.itemId === key, {}, itemId);
}

async function openEditorFrom(page, browser, apiFragment, trigger) {
  const existingTargets = new Set(browser.targets());
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST' && response.url().includes(apiFragment)
  ), { timeout: 30000 });
  const popupPromise = browser.waitForTarget((target) => (
    !existingTargets.has(target) && target.type() === 'page' && Boolean(target.opener())
  ), { timeout: 30000 });
  await trigger();
  const [response, popupTarget] = await Promise.all([responsePromise, popupPromise]);
  const body = await response.json().catch(() => ({}));
  assert(response.status() === 200, 'Module editor API failed', body);
  const popup = await popupTarget.page();
  await popup.waitForFunction(() => location.pathname.includes('/jupyter/'), { timeout: 30000 });
  return { response, body, popup, popupUrl: popup.url() };
}

async function cleanupModules(page, records) {
  await page.evaluate(async (items) => {
    const csrf = decodeURIComponent((document.cookie.match(/(?:^|; )trade_csrf=([^;]+)/) || [])[1] || '');
    for (const item of items) {
      const url = `/api/modules/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.moduleId)}/versions/${encodeURIComponent(item.version)}`;
      await fetch(url, { method: 'DELETE', headers: { 'X-CSRF-Token': csrf } });
    }
  }, records.reverse());
}

async function main() {
  const session = createSession();
  const suffix = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const templateName = `VNC Template ${suffix}`;
  const uploadName = `VNC Upload ${suffix}`;
  const created = [];
  let workspacePath = '';
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
    const errors = [];
    const failures = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`);
    });
    await page.goto(`${BASE}/modules`, { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#moduleRepositoryBrowser .trade-resource-browser-shell');
    assert(await page.$eval('#moduleLoadInstanceId', (input) => input.type === 'hidden'),
      'Pipeline Module instance identity is still directly editable');

    await openBlankModuleContext(page);
    const initialMenu = await page.evaluate(() => ({
      actions: [...document.querySelectorAll('[data-module-context-action]')].map((button) => ({
        action: button.dataset.moduleContextAction,
        disabled: button.disabled,
      })),
    }));
    assert(initialMenu.actions.map((item) => item.action).join('|') === 'add|create'
      && initialMenu.actions.every((item) => !item.disabled),
    'Blank-space Module Repository context menu is incomplete', initialMenu);
    await page.click('#modules .panel-head h2');

    await openBlankModuleContext(page);
    await clickModuleContext(page, 'create');
    await page.select('#createModuleKind', 'Signal');
    assert(await page.$('#createModuleId') === null && await page.$('#createModuleVersion') === null,
      'Create Module still exposes Engine-owned identity fields');
    await page.type('#createModuleName', templateName);
    const createResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/modules/template'));
    await page.click('#confirmCreateModuleBtn');
    const createResponse = await createResponsePromise;
    assert(createResponse.status() === 200, 'Create Module template API failed');
    const createBody = await createResponse.json();
    const templateDefinition = createBody.definition;
    const v1 = createBody.moduleKey;
    assert(/^mod_[0-9A-HJKMNP-TV-Z]{26}$/.test(templateDefinition.moduleId)
      && /^rev_[0-9A-HJKMNP-TV-Z]{26}$/.test(templateDefinition.version),
    'Create Module did not receive Engine-generated identity', templateDefinition);
    created.push({ kind: 'Signal', moduleId: templateDefinition.moduleId, version: templateDefinition.version });
    await waitCatalogItem(page, v1);

    await openBlankModuleContext(page);
    await clickModuleContext(page, 'add');
    await page.select('#moduleUploadKind', 'Signal');
    assert(await page.$('#moduleUploadId') === null && await page.$('#moduleUploadVersion') === null,
      'Add Module still exposes Engine-owned identity fields');
    await page.type('#moduleUploadName', uploadName);
    const upload = await page.$('#moduleUploadFiles');
    await upload.uploadFile(FIXTURE);
    const addResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/modules'));
    await page.click('#confirmModuleUploadBtn');
    const addResponse = await addResponsePromise;
    assert(addResponse.status() === 200, 'Local Module upload API failed');
    const addBody = await addResponse.json();
    const uploadedKey = addBody.moduleKey;
    created.push({ kind: 'Signal', moduleId: addBody.definition.moduleId, version: addBody.definition.version });
    await waitCatalogItem(page, uploadedKey);

    await openModuleContext(page, v1);
    const selectedMenu = await page.evaluate(() => ({
      actions: [...document.querySelectorAll('[data-module-context-action]')].map((button) => button.dataset.moduleContextAction),
      archiveDisabled: document.querySelector('[data-module-context-action="archive"]').disabled,
      replaceDisabled: document.querySelector('[data-module-context-action="replace"]').disabled,
      editDisabled: document.querySelector('[data-module-context-action="edit"]').disabled,
    }));
    assert(selectedMenu.actions.join('|') === 'edit|replace|archive|add|create' && !selectedMenu.archiveDisabled
      && !selectedMenu.replaceDisabled && !selectedMenu.editDisabled,
    'Selected custom Module lifecycle actions are unavailable', selectedMenu);
    await clickModuleContext(page, 'replace');
    await page.$eval('#moduleUploadInputs', (node) => {
      node.value = JSON.stringify({ price: { schema: { type: 'number' } } }, null, 2);
      node.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const replaceUpload = await page.$('#moduleUploadFiles');
    await replaceUpload.uploadFile(FIXTURE);
    const alertMessages = [];
    page.once('dialog', async (dialog) => { alertMessages.push(dialog.message()); await dialog.accept(); });
    const replaceResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/modules/replace'));
    await page.click('#confirmModuleUploadBtn');
    const replaceResponse = await replaceResponsePromise;
    assert(replaceResponse.status() === 200, 'Replace Module API failed', await replaceResponse.text());
    const replaceBody = await replaceResponse.json().catch(() => ({}));
    const v2 = replaceBody.moduleKey;
    const replacementDefinition = replaceBody.definition;
    assert(replacementDefinition.moduleId === templateDefinition.moduleId
      && replacementDefinition.version !== templateDefinition.version
      && /^rev_[0-9A-HJKMNP-TV-Z]{26}$/.test(replacementDefinition.version),
    'Replace did not retain Module identity and generate a Version identity', replacementDefinition);
    created.push({ kind: 'Signal', moduleId: replacementDefinition.moduleId, version: replacementDefinition.version });
    await waitCatalogItem(page, v2);
    assert(replaceBody.impact?.interfaceChanged === true,
      'Replace did not detect the changed Module interface', replaceBody);

    await openModuleContext(page, v1);
    await clickModuleContext(page, 'archive');
    await page.type('#archiveModuleReason', 'VNC lifecycle smoke');
    const archiveResponse = page.waitForResponse((response) => response.url().endsWith('/api/modules/archive'));
    await page.click('#confirmArchiveModuleBtn');
    assert((await archiveResponse).status() === 200, 'Archive Module API failed');
    await page.waitForFunction((key) => (
      window.__tradeState.repositoryCatalogs.modules.items.find((item) => item.itemId === key)?.status === 'archived'
    ), {}, v1);

    await openModuleContext(page, v2);
    const apiFragment = `/api/modules/Signal/${replacementDefinition.moduleId}/versions/${replacementDefinition.version}/jupyter`;
    const contextEdit = await openEditorFrom(page, browser, apiFragment, () => clickModuleContext(page, 'edit'));
    const editBody = contextEdit.body;
    workspacePath = editBody.workspacePath;
    const popupUrl = contextEdit.popupUrl;
    const popupDestination = new URL(popupUrl).searchParams.get('next') || `${new URL(popupUrl).pathname}${new URL(popupUrl).search}`;
    assert(
      /\/jupyter\/w\/module-[^/]+\/lab\?reset$/.test(popupDestination),
      'Jupyter did not open the isolated Module edit Workspace',
      { popupUrl, editBody },
    );
    await contextEdit.popup.close();

    await selectModule(page, v2);
    const inspectorButton = await page.waitForSelector(
      '.trade-resource-inspector-actions [data-resource-open-action="edit"]',
      { timeout: 10000 },
    ).catch(() => null);
    assert(inspectorButton, 'Module inspector does not expose Edit');
    const inspectorEdit = await openEditorFrom(page, browser, apiFragment, () => inspectorButton.click());
    await inspectorEdit.popup.close();

    await selectModule(page, v2);
    const doubleClickEdit = await openEditorFrom(page, browser, apiFragment, () => (
      page.click(`#moduleRepositoryBrowser .file-item-container[data-trade-item-id="${v2}"]`, { clickCount: 2, delay: 80 })
    ));
    await doubleClickEdit.popup.close();

    await openModuleContext(page, v2);
    await page.screenshot({ path: '/tmp/trade-module-lifecycle-vnc.png', fullPage: true });
    assert(!errors.length, 'Browser runtime errors occurred', errors);
    assert(!failures.length, 'Server returned 5xx responses', failures);
    console.log(JSON.stringify({
      initialMenu,
      selectedMenu,
      createdTemplate: v1,
      uploadedModule: uploadedKey,
      replacement: { moduleKey: v2, impact: replaceBody.impact, alerts: alertMessages },
      archived: v1,
      editWorkspace: { workspaceId: editBody.workspaceId, popupUrl, inspectorEdit: true, doubleClickEdit: true },
      screenshot: '/tmp/trade-module-lifecycle-vnc.png',
    }, null, 2));
    await cleanupModules(page, created);
  } finally {
    if (workspacePath) fs.rmSync(workspacePath, { recursive: true, force: true });
    if (browser) await browser.close();
    deleteSession(session.token);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exitCode = 1;
});
