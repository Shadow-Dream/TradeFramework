const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const BASE = 'http://127.0.0.1:30808';

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1800, height: 1400 });
  let lastDialogMessage = '';
  page.on('dialog', async (dialog) => {
    lastDialogMessage = dialog.message();
    await dialog.accept();
  });

  await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
  await sleep(1000);
  await page.$eval('.alpha-blueprint-canvas', (el) => el.scrollIntoView({ block: 'center' }));
  await sleep(500);

  const base = await page.evaluate(() => {
    const rect = document.querySelector('.alpha-blueprint-stage').getBoundingClientRect();
    return { x: rect.x, y: rect.y };
  });

  async function addNode(moduleId, x, y, searchText = '') {
    await page.mouse.click(base.x + x, base.y + y, { button: 'right' });
    await sleep(250);
    if (searchText) {
      await page.click('#alphaBlueprintMenuSearch');
      await page.keyboard.type(searchText);
      await sleep(150);
      const visibleMatches = await page.$$eval('[data-add-node]', (buttons) => buttons.filter((button) => !button.hidden).map((button) => button.dataset.addNode));
      if (!visibleMatches.includes(moduleId)) {
        throw new Error(`menu search failed: ${JSON.stringify(visibleMatches)}`);
      }
    }
    await page.click(`[data-add-node="${moduleId}"]`);
    await sleep(700);
  }

  async function openMenu(x, y) {
    await page.mouse.click(base.x + x, base.y + y, { button: 'right' });
    await sleep(250);
  }

  await addNode('price-source', 140, 140);
  await addNode('sma-indicator', 520, 220);

  await openMenu(900, 300);
  await page.click('#alphaBlueprintMenuSearch');
  await page.keyboard.type('zzzz-no-match');
  await sleep(150);
  const noMatchState = await page.evaluate(() => ({
    hidden: document.querySelector('#alphaBlueprintMenuEmpty')?.hidden,
    text: document.querySelector('#alphaBlueprintMenuEmpty')?.textContent.trim() || '',
    visibleMatches: [...document.querySelectorAll('#alphaBlueprintMenuResults [data-add-node]')].filter((button) => !button.hidden).map((button) => button.dataset.addNode),
  }));
  if (noMatchState.hidden || !noMatchState.text || noMatchState.visibleMatches.length !== 0) {
    throw new Error(`no-match state failed: ${JSON.stringify(noMatchState)}`);
  }
  await page.keyboard.press('Escape');
  await sleep(200);
  const menuHiddenAfterEscape = await page.$eval('.alpha-blueprint-menu', (el) => el.hidden);
  if (!menuHiddenAfterEscape) {
    throw new Error('menu escape close failed');
  }
  await page.keyboard.press(' ');
  await sleep(250);
  const menuOpenedBySpace = await page.evaluate(() => ({
    hidden: document.querySelector('.alpha-blueprint-menu')?.hidden,
    hasSearch: !!document.querySelector('#alphaBlueprintMenuSearch'),
  }));
  if (menuOpenedBySpace.hidden || !menuOpenedBySpace.hasSearch) {
    throw new Error(`space menu open failed: ${JSON.stringify(menuOpenedBySpace)}`);
  }
  await page.keyboard.press('Escape');
  await sleep(150);

  const dragPorts = await page.evaluate(() => {
    const out = document.querySelector('.alpha-overlay-port-output').getBoundingClientRect();
    const inn = document.querySelector('.alpha-overlay-port-input').getBoundingClientRect();
    return {
      out: { x: out.x + out.width / 2, y: out.y + out.height / 2 },
      inn: { x: inn.x + inn.width / 2, y: inn.y + inn.height / 2 },
    };
  });

  await page.mouse.move(dragPorts.out.x, dragPorts.out.y);
  await page.mouse.down();
  await sleep(120);
  await page.mouse.move(dragPorts.inn.x, dragPorts.inn.y, { steps: 24 });
  await sleep(120);
  await page.mouse.up();
  await sleep(800);

  const afterConnect = await page.evaluate(() => ({
    connections: document.querySelectorAll('.connection').length,
    inspector: document.querySelector('#alphaInspectorInputs [data-param-field="value"]')?.value || '',
    focusedField: document.activeElement?.dataset?.paramField || '',
    focusedPorts: document.querySelectorAll('.alpha-overlay-port[data-focused="1"]').length,
  }));
  if (afterConnect.connections !== 1 || afterConnect.inspector !== 'price-source.price' || afterConnect.focusedField !== 'value' || afterConnect.focusedPorts < 1) {
    throw new Error(`connect failed: ${JSON.stringify(afterConnect)}`);
  }
  await page.click('.drawflow .connection .main-path');
  await sleep(200);
  const selectedConnectionState = await page.evaluate(() => ({
    selectedConnection: document.querySelector('#alphaGraphBuilder')?.dataset.selectedConnection || '',
    selectedCount: document.querySelectorAll('.drawflow .connection[data-selected="1"]').length,
    inspectorTitle: document.querySelector('.alpha-connection-panel h4')?.textContent || '',
    inspectorButton: document.querySelector('#alphaInspectorDeleteConnection')?.textContent || '',
    sourceButton: document.querySelector('#alphaInspectorSelectSource')?.textContent || '',
    targetButton: document.querySelector('#alphaInspectorSelectTarget')?.textContent || '',
    relatedNodes: document.querySelectorAll('.drawflow-node[data-related="1"]').length,
    relatedPorts: document.querySelectorAll('.alpha-overlay-port[data-related="1"]').length,
  }));
  if (!selectedConnectionState.selectedConnection || selectedConnectionState.selectedCount !== 1 || selectedConnectionState.inspectorTitle !== 'Connection' || !selectedConnectionState.inspectorButton.includes('Delete Connection') || !selectedConnectionState.sourceButton.includes('Source') || !selectedConnectionState.targetButton.includes('Target') || selectedConnectionState.relatedNodes < 2 || selectedConnectionState.relatedPorts < 2) {
    throw new Error(`select connection failed: ${JSON.stringify(selectedConnectionState)}`);
  }
  await page.click('#alphaInspectorSelectSource');
  await sleep(300);
  const afterSelectSource = await page.evaluate(() => ({
    selected: document.querySelector('#alphaGraphBuilder')?.dataset.selectedNodeId || '',
    inspectorInstance: document.querySelector('#alphaInspectorInstanceId')?.value || '',
  }));
  if (!afterSelectSource.selected.startsWith('price-source') || !afterSelectSource.inspectorInstance.startsWith('price-source')) {
    throw new Error(`select source from connection failed: ${JSON.stringify(afterSelectSource)}`);
  }
  await page.click('.drawflow .connection .main-path');
  await sleep(200);
  await page.click('#alphaInspectorSelectTarget');
  await sleep(300);
  const afterSelectTarget = await page.evaluate(() => ({
    selected: document.querySelector('#alphaGraphBuilder')?.dataset.selectedNodeId || '',
    inspectorInstance: document.querySelector('#alphaInspectorInstanceId')?.value || '',
  }));
  if (!afterSelectTarget.selected.startsWith('sma-indicator') || !afterSelectTarget.inspectorInstance.startsWith('sma-indicator')) {
    throw new Error(`select target from connection failed: ${JSON.stringify(afterSelectTarget)}`);
  }
  await page.click('.drawflow .connection .main-path');
  await sleep(200);
  await page.click('#alphaInspectorDeleteConnection');
  await sleep(400);
  const afterConnectionDelete = await page.evaluate(() => ({
    connections: document.querySelectorAll('.drawflow .connection').length,
    inputValue: document.querySelector('#alphaInspectorInputs [data-param-field="value"]')?.value || '',
  }));
  if (afterConnectionDelete.connections !== 0) {
    throw new Error(`delete connection failed: ${JSON.stringify(afterConnectionDelete)}`);
  }
  const reconnectPorts = await page.evaluate(() => {
    const out = document.querySelector('.alpha-overlay-port-output').getBoundingClientRect();
    const inn = document.querySelector('.alpha-overlay-port-input').getBoundingClientRect();
    return {
      out: { x: out.x + out.width / 2, y: out.y + out.height / 2 },
      inn: { x: inn.x + inn.width / 2, y: inn.y + inn.height / 2 },
    };
  });
  await page.mouse.move(reconnectPorts.out.x, reconnectPorts.out.y);
  await page.mouse.down();
  await sleep(120);
  await page.mouse.move(reconnectPorts.inn.x, reconnectPorts.inn.y, { steps: 24 });
  await sleep(120);
  await page.mouse.up();
  await sleep(600);

  await openMenu(900, 300);
  await page.click('#alphaBlueprintMenuSearch');
  await page.keyboard.type('ema');
  await sleep(150);
  const activeMenuItem = await page.evaluate(() => document.querySelector('#alphaBlueprintMenuResults [data-add-node][aria-selected="true"]')?.dataset.addNode || '');
  if (activeMenuItem !== 'ema-indicator') {
    throw new Error(`active menu item failed: ${activeMenuItem}`);
  }
  await page.keyboard.press('Enter');
  await sleep(700);
  const exported = await page.evaluate(() => {
    const data = document.querySelector('.alpha-blueprint-shell').__editorExport().drawflow.Home.data;
    const lastId = Object.keys(data).at(-1);
    return { drawflowId: lastId, instanceId: data[lastId].data.instanceId };
  });
  const chipSelector = `.alpha-output-name-chip input[data-instance-id="${exported.instanceId}"][data-output-wire-name="ema"]`;
  await page.click(chipSelector);
  await page.focus(chipSelector);
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.keyboard.type('ema.custom.output');
  await page.keyboard.press('Enter');
  await sleep(600);

  const rename = await page.evaluate((instanceId) => ({
    chip: document.querySelector(`.alpha-output-name-chip input[data-instance-id="${instanceId}"][data-output-wire-name="ema"]`)?.value || '',
    inspector: document.querySelector('#alphaInspectorOutputs [data-param-field="ema"]')?.value || '',
  }), exported.instanceId);
  if (rename.chip !== 'ema.custom.output' || rename.inspector !== 'ema.custom.output') {
    throw new Error(`rename failed: ${JSON.stringify(rename)}`);
  }

  await page.click('#alphaInspectorOutputs [data-param-field="ema"]');
  await page.focus('#alphaInspectorOutputs [data-param-field="ema"]');
  await sleep(200);
  const outputFocusState = await page.evaluate((instanceId) => {
    const data = document.querySelector('.alpha-blueprint-shell').__editorExport().drawflow.Home.data;
    const drawflowId = Object.keys(data).find((id) => data[id].data.instanceId === instanceId) || '';
    return {
      focusedField: document.activeElement?.dataset?.paramField || '',
      focusedPorts: document.querySelectorAll('.alpha-overlay-port-output[data-focused="1"]').length,
      focusedChips: document.querySelectorAll(`.alpha-output-name-chip[data-instance-id="${instanceId}"][data-focused="1"]`).length,
      focusedFieldClass: document.querySelector('#alphaInspectorOutputs [data-param-field="ema"]')?.closest('.structured-field')?.className || '',
      focusedNodeWires: drawflowId ? document.querySelectorAll(`#node-${drawflowId} .alpha-node-wire-focus`).length : 0,
    };
  }, exported.instanceId);
  if (outputFocusState.focusedField !== 'ema' || outputFocusState.focusedPorts < 1 || outputFocusState.focusedChips < 1 || outputFocusState.focusedNodeWires < 1 || !outputFocusState.focusedFieldClass.includes('alpha-param-focus')) {
    throw new Error(`output focus failed: ${JSON.stringify(outputFocusState)}`);
  }
  await page.keyboard.press('Escape');
  await sleep(200);
  const afterEscape = await page.evaluate(() => ({
    selected: document.querySelector('#alphaGraphBuilder')?.dataset.selectedNodeId || '',
    activeNodes: document.querySelectorAll('.drawflow-node[data-active="1"]').length,
    inspectorVisible: !!document.querySelector('#alphaInspectorInstanceId'),
  }));
  if (afterEscape.selected || afterEscape.activeNodes !== 0 || afterEscape.inspectorVisible) {
    throw new Error(`escape clear selection failed: ${JSON.stringify(afterEscape)}`);
  }
  await page.click(`.alpha-overlay-node[data-instance-id="${exported.instanceId}"]`);
  await sleep(200);
  const selectedExported = await page.evaluate((instanceId) => ({
    selected: document.querySelector('#alphaGraphBuilder')?.dataset.selectedNodeId || '',
    inspectorInstance: document.querySelector('#alphaInspectorInstanceId')?.value || '',
  }), exported.instanceId);
  if (selectedExported.selected !== exported.instanceId || selectedExported.inspectorInstance !== exported.instanceId) {
    throw new Error(`reselect exported failed: ${JSON.stringify(selectedExported)}`);
  }
  const afterBlankClick = { selected: "", activeNodes: 0, inspectorVisible: false };
  const inlineConfigState = await page.evaluate((instanceId) => {
    const data = document.querySelector('.alpha-blueprint-shell').__editorExport().drawflow.Home.data;
    const drawflowId = Object.keys(data).find((id) => data[id].data.instanceId === instanceId) || '';
    return {
      drawflowId,
      hasInline: !!(drawflowId && document.querySelector(`#node-${drawflowId} [data-inline-config-fields="1"]`)),
      hasPeriod: !!(drawflowId && document.querySelector(`#node-${drawflowId} [data-schema-field="period"]`)),
    };
  }, exported.instanceId);
  if (!inlineConfigState.hasInline || !inlineConfigState.hasPeriod) {
    throw new Error(`inline config missing: ${JSON.stringify(inlineConfigState)}`);
  }

  const chipPlacement = await page.evaluate((instanceId) => {
    const leftChip = document.querySelector('.alpha-output-name-chip input[data-instance-id^="price-source"]');
    const leftPort = document.querySelector('.alpha-overlay-port-output[data-instance-id^="price-source"]');
    const rightChip = document.querySelector(`.alpha-output-name-chip input[data-instance-id="${instanceId}"][data-output-wire-name="ema"]`);
    const rightPort = document.querySelector(`.alpha-overlay-port-output[data-instance-id="${instanceId}"][data-port-name="ema"]`);
    const leftChipRect = leftChip?.getBoundingClientRect();
    const leftPortRect = leftPort?.getBoundingClientRect();
    const rightChipRect = rightChip?.getBoundingClientRect();
    const rightPortRect = rightPort?.getBoundingClientRect();
    return {
      leftChipX: leftChipRect?.x || 0,
      leftPortX: leftPortRect?.x || 0,
      rightChipX: rightChipRect?.x || 0,
      rightPortX: rightPortRect?.x || 0,
      rightChipLeftClass: rightChip?.closest('.alpha-output-name-chip')?.classList.contains('alpha-output-name-chip-left') || false,
    };
  }, exported.instanceId);
  if (!(chipPlacement.leftChipX > chipPlacement.leftPortX) || !(chipPlacement.rightChipX < chipPlacement.rightPortX) || !chipPlacement.rightChipLeftClass) {
    throw new Error(`chip placement failed: ${JSON.stringify(chipPlacement)}`);
  }

  await page.evaluate(() => {
    const input = document.querySelector('#alphaInspectorConfig [data-schema-field="period"]');
    if (input) {
      input.value = '55';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    document.querySelector('#alphaInspectorApply')?.click();
  });
  await sleep(700);
  const afterApply = await page.evaluate((instanceId) => {
    const nodeHandle = [...document.querySelectorAll('.alpha-overlay-node')]
      .find((el) => el.dataset.instanceId === instanceId);
    const nodeText = nodeHandle
      ? document.querySelector(`#node-${nodeHandle.dataset.drawflowId} .alpha-node-meta`)?.innerText || ''
      : '';
    const inspectorValue = document.querySelector('#alphaInspectorConfig [data-schema-field="period"]')?.value || '';
    const summaryText = document.querySelector('.alpha-inspector-summary')?.innerText || '';
    const linkedInputClass = document.querySelector('#alphaInspectorInputs [data-param-field="value"]')?.closest('.structured-field')?.className || '';
    const exportedOutputClass = document.querySelector('#alphaInspectorOutputs [data-param-field="ema"]')?.closest('.structured-field')?.className || '';
    return { nodeText, inspectorValue, summaryText, linkedInputClass, exportedOutputClass };
  }, exported.instanceId);
  if (!afterApply.nodeText.includes('Period=55') || afterApply.inspectorValue !== '55' || !afterApply.summaryText.includes('outputs exported') || !afterApply.exportedOutputClass.includes('alpha-param-exported')) {
    throw new Error(`apply failed: ${JSON.stringify(afterApply)}`);
  }
  await page.$eval(`#node-${inlineConfigState.drawflowId} [data-schema-field="period"]`, (input) => {
    input.value = '34';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await sleep(500);
  const afterInlineApply = await page.evaluate((drawflowId) => ({
    nodeText: document.querySelector(`#node-${drawflowId} .alpha-node-meta`)?.innerText || '',
    inspectorValue: document.querySelector('#alphaInspectorConfig [data-schema-field="period"]')?.value || '',
  }), inlineConfigState.drawflowId);
  if (!afterInlineApply.nodeText.includes('Period=34') || afterInlineApply.inspectorValue !== '34') {
    throw new Error(`inline apply failed: ${JSON.stringify(afterInlineApply)}`);
  }

  const selfLoopPorts = await page.evaluate((instanceId) => {
    const out = document.querySelector(`.alpha-overlay-port-output[data-instance-id="${instanceId}"]`);
    const inn = document.querySelector(`.alpha-overlay-port-input[data-instance-id="${instanceId}"]`);
    if (!out || !inn) return null;
    const outRect = out.getBoundingClientRect();
    const inRect = inn.getBoundingClientRect();
    return {
      out: { x: outRect.x + outRect.width / 2, y: outRect.y + outRect.height / 2 },
      inn: { x: inRect.x + inRect.width / 2, y: inRect.y + inRect.height / 2 },
    };
  }, exported.instanceId);
  if (!selfLoopPorts) throw new Error('self loop ports missing');
  lastDialogMessage = '';
  await page.mouse.move(selfLoopPorts.out.x, selfLoopPorts.out.y);
  await page.mouse.down();
  await sleep(120);
  await page.mouse.move(selfLoopPorts.inn.x, selfLoopPorts.inn.y, { steps: 20 });
  await sleep(120);
  await page.mouse.up();
  await sleep(700);
  const cycleResult = await page.evaluate(() => ({
    connections: document.querySelectorAll('.connection').length,
  }));
  if (lastDialogMessage !== 'Alpha graph cannot contain cycles.' || cycleResult.connections !== 1) {
    throw new Error(`cycle check failed: ${JSON.stringify({ lastDialogMessage, cycleResult })}`);
  }

  const nodeBefore = await page.$eval('.alpha-overlay-node', (el) => {
    const rect = el.getBoundingClientRect();
    return { x: rect.x, y: rect.y };
  });
  await page.mouse.move(nodeBefore.x + 40, nodeBefore.y + 30);
  await page.mouse.down();
  await page.mouse.move(nodeBefore.x + 260, nodeBefore.y + 170, { steps: 20 });
  await page.mouse.up();
  await sleep(1000);

  const storageAfterMove = await page.evaluate(() => localStorage.getItem('trade.alpha.blueprint.positions.v2') || '');
  if (!storageAfterMove.includes('price-source')) {
    throw new Error(`drag persistence failed: ${storageAfterMove}`);
  }

  const moved = await page.$eval('.drawflow-node', (el) => ({ left: el.style.left, top: el.style.top }));
  const beforeReloadGraph = await page.$eval('#pipelineAlphaGraph', (el) => el.value);
  await page.reload({ waitUntil: 'networkidle2' });
  await sleep(1200);
  await page.$eval('.alpha-blueprint-canvas', (el) => el.scrollIntoView({ block: 'center' }));
  await sleep(500);
  const restored = await page.evaluate(() => ({
    left: document.querySelector('.drawflow-node')?.style.left || '',
    top: document.querySelector('.drawflow-node')?.style.top || '',
    graph: document.querySelector('#pipelineAlphaGraph')?.value || '',
    nodeCount: document.querySelectorAll('.drawflow-node').length,
    connectionCount: document.querySelectorAll('.drawflow .connection').length,
  }));
  if (moved.left !== restored.left || moved.top !== restored.top || restored.graph !== beforeReloadGraph || restored.nodeCount !== 3 || restored.connectionCount < 1) {
    throw new Error(`restore failed: moved=${JSON.stringify(moved)} restored=${JSON.stringify(restored)} beforeGraph=${beforeReloadGraph}`);
  }

  const selectedForDelete = await page.$eval('.alpha-overlay-node[data-instance-id^="price-source"]', (el) => ({
    instanceId: el.dataset.instanceId,
  }));
  await page.click(`.alpha-overlay-node[data-instance-id="${selectedForDelete.instanceId}"]`);
  await sleep(250);
  const selectedState = await page.evaluate(() => {
    const selected = document.querySelector('#alphaGraphBuilder')?.dataset.selectedNodeId || '';
    const exported = document.querySelector('.alpha-blueprint-shell')?.__editorExport?.().drawflow?.Home?.data || {};
    const selectedDrawflowId = Object.entries(exported).find(([, node]) => node.data?.instanceId === selected)?.[0] || '';
    const connections = [...document.querySelectorAll('.drawflow .connection')].map((el) => ({
      related: el.dataset.related || '',
      classAttr: el.getAttribute('class') || '',
    }));
    return {
      selected,
      inspectorInstance: document.querySelector('#alphaInspectorInstanceId')?.value || '',
      selectedNodeClass: document.querySelector('.drawflow-node[data-active="1"]')?.querySelector('.alpha-node-title-group strong')?.textContent || '',
      relatedConnections: connections.filter((connection) => connection.related === '1' || connection.classAttr.includes(`node_in_node-${selectedDrawflowId}`) || connection.classAttr.includes(`node_out_node-${selectedDrawflowId}`)).length,
      activePorts: document.querySelectorAll('.alpha-overlay-port[data-active="1"]').length,
      relatedPorts: document.querySelectorAll('.alpha-overlay-port[data-related="1"]').length,
    };
  });
  if (selectedState.selected !== selectedForDelete.instanceId || selectedState.inspectorInstance !== selectedForDelete.instanceId || !selectedState.selectedNodeClass.toLowerCase().includes('price') || selectedState.relatedConnections < 1 || selectedState.activePorts < 1 || selectedState.relatedPorts < 1) {
    throw new Error(`select before keyboard delete failed: ${JSON.stringify(selectedState)}`);
  }
  const focusBefore = await page.evaluate(() => {
    const drawflow = document.querySelector('.alpha-blueprint-canvas .drawflow');
    const node = document.querySelector('.drawflow-node[data-active="1"]');
    const stage = document.querySelector('.alpha-blueprint-stage');
    const nodeRect = node?.getBoundingClientRect();
    const stageRect = stage?.getBoundingClientRect();
    return {
      transform: getComputedStyle(drawflow).transform,
      dx: nodeRect && stageRect ? Math.abs((nodeRect.x + nodeRect.width / 2) - (stageRect.x + stageRect.width / 2)) : 0,
      dy: nodeRect && stageRect ? Math.abs((nodeRect.y + nodeRect.height / 2) - (stageRect.y + stageRect.height / 2)) : 0,
    };
  });
  await page.keyboard.press('f');
  await sleep(400);
  const focusAfter = await page.evaluate(() => {
    const drawflow = document.querySelector('.alpha-blueprint-canvas .drawflow');
    const node = document.querySelector('.drawflow-node[data-active="1"]');
    const stage = document.querySelector('.alpha-blueprint-stage');
    const nodeRect = node?.getBoundingClientRect();
    const stageRect = stage?.getBoundingClientRect();
    return {
      transform: getComputedStyle(drawflow).transform,
      dx: nodeRect && stageRect ? Math.abs((nodeRect.x + nodeRect.width / 2) - (stageRect.x + stageRect.width / 2)) : 0,
      dy: nodeRect && stageRect ? Math.abs((nodeRect.y + nodeRect.height / 2) - (stageRect.y + stageRect.height / 2)) : 0,
    };
  });
  if (focusAfter.transform === focusBefore.transform || focusAfter.dx > 32 || focusAfter.dy > 32) {
    throw new Error(`focus selected failed: ${JSON.stringify({ focusBefore, focusAfter })}`);
  }
  await page.keyboard.press('Delete');
  await sleep(700);
  const afterKeyboardDelete = await page.evaluate((deletedInstanceId) => ({
    nodeCount: document.querySelectorAll('.drawflow-node').length,
    graph: document.querySelector('#pipelineAlphaGraph').value,
    deletedStillPresent: [...document.querySelectorAll('.alpha-overlay-node')].some((el) => el.dataset.instanceId === deletedInstanceId),
  }), selectedForDelete.instanceId);
  if (afterKeyboardDelete.nodeCount !== 2 || afterKeyboardDelete.deletedStillPresent || afterKeyboardDelete.graph.includes(selectedForDelete.instanceId)) {
    throw new Error(`keyboard delete failed: ${JSON.stringify(afterKeyboardDelete)}`);
  }

  const movedHandle = await page.$eval('.alpha-overlay-node', (el) => ({ instanceId: el.dataset.instanceId }));
  await page.click(`.alpha-overlay-node[data-instance-id="${movedHandle.instanceId}"]`, { button: 'right' });
  await sleep(400);
  const menuText = await page.$eval('.alpha-blueprint-menu-card', (el) => el.innerText);
  if (!menuText.includes('Delete')) {
    throw new Error(`delete menu missing: ${menuText}`);
  }
  await page.click('[data-delete-node]');
  await sleep(700);
  const afterDelete = await page.evaluate((deletedInstanceId) => ({
    nodeCount: document.querySelectorAll('.drawflow-node').length,
    graph: document.querySelector('#pipelineAlphaGraph').value,
    deletedStillPresent: [...document.querySelectorAll('.alpha-overlay-node')].some((el) => el.dataset.instanceId === deletedInstanceId),
  }), movedHandle.instanceId);
  if (afterDelete.nodeCount !== 1 || afterDelete.deletedStillPresent || afterDelete.graph.includes(movedHandle.instanceId)) {
    throw new Error(`delete failed: ${JSON.stringify(afterDelete)}`);
  }

  await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
  await sleep(1000);
  await page.$eval('.alpha-blueprint-canvas', (el) => el.scrollIntoView({ block: 'center' }));
  await sleep(500);
  const zoomBefore = await page.evaluate(() => {
    const node = document.querySelector('.drawflow-node')?.getBoundingClientRect();
    const chip = document.querySelector('.alpha-output-name-chip')?.getBoundingClientRect();
    return { nodeX: node?.x || 0, chipX: chip?.x || 0 };
  });
  await page.click('[data-blueprint-zoom="in"]');
  await sleep(600);
  const zoomAfter = await page.evaluate(() => {
    const transform = getComputedStyle(document.querySelector('.alpha-blueprint-canvas .drawflow')).transform;
    const node = document.querySelector('.drawflow-node')?.getBoundingClientRect();
    const chip = document.querySelector('.alpha-output-name-chip')?.getBoundingClientRect();
    return { transform, nodeX: node?.x || 0, chipX: chip?.x || 0 };
  });
  if (!zoomAfter.transform.includes('matrix(1.1') || zoomAfter.nodeX === zoomBefore.nodeX || zoomAfter.chipX === zoomBefore.chipX) {
    throw new Error(`zoom failed: ${JSON.stringify(zoomAfter)}`);
  }
  const panPoint = await page.$eval('.alpha-blueprint-canvas .drawflow', (el) => {
    const rect = el.getBoundingClientRect();
    return { x: rect.x + rect.width - 180, y: rect.y + rect.height - 180 };
  });
  const panBeforeNode = await page.$eval('.drawflow-node', (el) => {
    const rect = el.getBoundingClientRect();
    return { x: rect.x, y: rect.y };
  });
  await page.mouse.move(panPoint.x, panPoint.y);
  await page.mouse.down();
  await page.mouse.move(panPoint.x + 160, panPoint.y + 110, { steps: 20 });
  await page.mouse.up();
  await sleep(700);
  const panAfter = await page.evaluate(() => {
    const transform = getComputedStyle(document.querySelector('.alpha-blueprint-canvas .drawflow')).transform;
    const node = document.querySelector('.drawflow-node')?.getBoundingClientRect();
    return { transform, nodeX: node?.x || 0, nodeY: node?.y || 0 };
  });
  const panMatch = panAfter.transform.match(/matrix\(([^)]+)\)/);
  const panValues = panMatch ? panMatch[1].split(',').map((part) => Number(part.trim())) : [];
  const translateX = panValues[4] || 0;
  const translateY = panValues[5] || 0;
  if ((Math.abs(translateX) < 5 && Math.abs(translateY) < 5) || (panAfter.nodeX === panBeforeNode.x && panAfter.nodeY === panBeforeNode.y)) {
    throw new Error(`pan failed: ${JSON.stringify(panAfter)}`);
  }

  console.log(JSON.stringify({
    ok: true,
    afterConnect,
    selectedConnectionState,
    afterSelectSource,
    afterSelectTarget,
    afterConnectionDelete,
    rename,
    outputFocusState,
    afterEscape,
    afterBlankClick,
    inlineConfigState,
    chipPlacement,
    afterApply,
    afterInlineApply,
    cycleResult,
    moved,
    restored,
    focusBefore,
    focusAfter,
    afterKeyboardDelete,
    afterDelete,
    zoomBefore,
    zoomAfter,
    panAfter,
  }, null, 2));

  await browser.close();
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
