import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BasicWorkflowSubsystemTests(unittest.TestCase):
    def test_daily_axis_includes_full_year_and_hides_library_logo(self):
        core_path = ROOT / "web" / "chart_core.js"
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const source = fs.readFileSync({json.dumps(str(core_path))}, 'utf8');
            let options = null;
            const context = {{ window: {{ LightweightCharts: {{
              createChart(_container, value) {{ options = value; return {{}}; }},
              PriceScaleMode: {{ Normal: 0, Logarithmic: 1 }},
              CrosshairMode: {{ Normal: 0 }},
            }} }} }};
            context.globalThis = context;
            vm.runInNewContext(source, context, {{ filename: {json.dumps(str(core_path))} }});
            context.window.TradeChartCore.createFinancialChart(
              {{ clientWidth: 900, clientHeight: 500 }},
              {{ timeZone: 'UTC', showTime: false }},
            );
            assert.equal(options.layout.attributionLogo, false);
            assert.equal(options.timeScale.tickMarkFormatter(Date.UTC(2026, 0, 2) / 1000), '01/02/2026');
            assert.equal(options.localization.timeFormatter(Date.UTC(2017, 6, 11) / 1000), '07/11/2017');
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        workspace_source = (ROOT / "web" / "basic_workflow_workspace.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('showTime: state.period !== "day" && timeInfo.showTime', workspace_source)

    def test_trade_engine_sidebar_exposes_subsystem_basic(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        engine_source = (ROOT / "engine_service.py").read_text(encoding="utf-8")
        self.assertIn('<h2 class="side-nav-heading">SubSystem</h2>', html)
        self.assertIn('id="subsystemNavLinks"', html)
        self.assertIn('getJson("/api/subsystems")', source)
        self.assertNotIn('<a class="nav-btn" href="/basic-workflow">Basic</a>', html)
        self.assertNotIn("application_protocols.basic_workflow", engine_source)
        self.assertNotIn("/api/subsystems/basic", engine_source)
        self.assertNotIn("/basic-workflow", engine_source)

    def test_basic_home_is_market_only_and_workspace_loads_chart_runtime(self):
        home = (ROOT / "web" / "basic_workflow.html").read_text(encoding="utf-8")
        workspace = (ROOT / "web" / "basic_workflow_workspace.html").read_text(encoding="utf-8")
        home_topbar = home.split("</header>", 1)[0]
        workspace_topbar = workspace.split("</header>", 1)[0]
        self.assertIn("<h1>Trade Engine</h1>", home_topbar)
        self.assertIn("<h1>Trade Engine</h1>", workspace_topbar)
        self.assertIn('href="/overview" aria-label="Back to Trade Engine overview">Back</a>', home_topbar)
        self.assertIn('href="/basic-workflow" aria-label="Back to the Basic stock catalog">Back</a>', workspace_topbar)
        self.assertNotIn('id="bwSyncMarket"', home_topbar)
        self.assertNotIn('id="bwInstrumentTitle"', workspace_topbar)
        self.assertNotIn('id="bwFavoriteInstrument"', workspace_topbar)
        self.assertIn('id="bwSyncMarket" type="button" aria-label="Refresh the complete stock list">Refresh stock list</button>', home)
        self.assertNotIn("Sync snapshot", home)
        self.assertIn("Price data is downloaded when a workspace opens.", home)
        self.assertIn('id="bwStatus" class="bw-plain-status"', home)
        self.assertIn('id="bwStatus" class="bw-plain-status"', workspace)
        self.assertIn('id="bwInstrumentList"', home)
        self.assertIn('id="bwInstrumentSearch"', home)
        self.assertIn('class="bw-market-tabs" aria-label="Stock lists" role="tablist"', home)
        self.assertIn('data-market-tab="watchlist" type="button" role="tab" aria-selected="true"', home)
        self.assertIn('data-market-tab="all" type="button" role="tab" aria-selected="false"', home)
        self.assertIn('id="bwMarketTabPanel" role="tabpanel" aria-labelledby="bwWatchlistTab"', home)
        self.assertIn('id="bwPageRange"', home)
        self.assertIn('id="bwPreviousPage"', home)
        self.assertIn('id="bwNextPage"', home)
        self.assertIn('<span>Data as of</span>', home)
        self.assertIn('<span>Exchange / currency</span>', home)
        self.assertNotIn('<span>Venue</span>', home)
        self.assertIn('<link rel="stylesheet" href="/styles.css" />', home)
        self.assertNotIn('id="bwChart"', home)
        self.assertNotIn('id="bwDrawingTools"', home)
        self.assertNotIn('lightweight-charts', home)
        self.assertNotIn('/chart_core.js', home)
        self.assertIn('id="bwChart"', workspace)
        self.assertIn('id="bwDrawingTools"', workspace)
        self.assertIn('id="bwDrawingStatus" class="bw-drawing-status" role="status"', workspace)
        self.assertIn('id="bwOpenIndicators"', workspace)
        self.assertIn('id="bwIndicatorDialog"', workspace)
        self.assertIn('id="bwIndicatorSearch"', workspace)
        self.assertIn('id="bwIndicatorCatalog"', workspace)
        self.assertIn('id="bwIndicatorForm"', workspace)
        self.assertIn('id="bwActiveIndicators"', workspace)
        self.assertNotIn('SMA 20', workspace)
        self.assertNotIn('EMA 20', workspace)
        self.assertNotIn('BB 20', workspace)
        self.assertIn('id="bwIndicatorStatus" class="bw-indicator-status" role="status"', workspace)
        self.assertIn('id="bwChart" class="bw-chart" tabindex="0"', workspace)
        self.assertIn('aria-keyshortcuts="Delete Backspace"', workspace)
        self.assertIn('/vendor/lightweight-charts/lightweight-charts.standalone.production.js', workspace)
        self.assertIn('<script src="/module_forms.js"></script>', workspace)
        self.assertIn('<script src="/chart_core.js"></script>', workspace)
        self.assertIn('<link rel="stylesheet" href="/styles.css" />', workspace)
        self.assertGreater(workspace.index('id="bwInstrumentTitle"'), workspace.index("<main"))
        self.assertGreater(workspace.index('id="bwFavoriteInstrument"'), workspace.index("<main"))

    def test_basic_home_client_cannot_materialize_or_render_a_chart(self):
        source = (ROOT / "web" / "basic_workflow.js").read_text(encoding="utf-8")
        for forbidden in (
            "/api/subsystems/basic/instruments/open",
            "/api/backtests",
            "/api/backtest-jobs",
            "/api/visualizations",
            "TradeChartCore",
            "LightweightCharts",
            "canvas",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('const WORKSPACE_PATH = "/basic-workflow/workspace";', source)
        self.assertIn("snapshotId: snapshot.snapshotId", source)
        self.assertIn("instrumentId: instrument.instrumentId", source)
        self.assertIn('period: "day"', source)
        self.assertIn("const MARKET_PAGE_SIZE = 50;", source)
        self.assertIn("Snapshot cached · ${formatInstant(barSnapshot.lastTime)}", source)
        self.assertIn("Automatic snapshot queued…", source)
        self.assertNotIn("dataAsOf.textContent = formatInstant(barSnapshot.asOf);", source)
        self.assertIn('window.addEventListener("pageshow"', source)
        self.assertIn("if (!event.persisted || !state.csrfToken) return;", source)
        self.assertIn('if ((state.market.snapshot?.snapshotId || "") !== previousSnapshotId) state.marketPage = 1;', source)
        self.assertGreaterEqual(source.count("state.marketPage = 1;"), 3)
        self.assertIn('postJson(`${MARKET_ENDPOINT}/sync`, { providerId: BASIC_MARKET_PROVIDER_ID })', source)
        self.assertIn('syncButton.textContent = state.syncBusy ? "Refreshing stock list…" : "Refresh stock list";', source)

        workspace_source = (ROOT / "web" / "basic_workflow_workspace.js").read_text(encoding="utf-8")
        self.assertIn('postJson("/api/subsystems/basic/instruments/open"', workspace_source)
        self.assertNotIn('`${MARKET_ENDPOINT}/sync`', workspace_source)
        self.assertNotIn("Refresh stock list", workspace_source)

    def test_market_pagination_renders_only_one_page_and_missing_cutoff_is_blank(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, marketPage, instrumentRow, formatInstant }};',
            );
            const document = {{
              getElementById() {{ return null; }},
              createElement(tag) {{
                const element = {{
                  tag, className: '', dataset: {{}}, textContent: '', title: '', children: [],
                  append(...values) {{ this.children.push(...values); }},
                  setAttribute(name, value) {{ this[name] = value; }},
                }};
                return element;
              }},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            api.state.catalog = {{ modules: [
              {{ kind: 'Signal', moduleId: 'basic-price-close-selector', version: '1', protocolId: 'trade.basic-workflow', status: 'archived', ports: {{ inputs: {{ price: {{}} }}, outputs: {{ close: {{}} }} }} }},
              {{ kind: 'Signal', moduleId: 'sma-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ value: {{}} }}, outputs: {{ sma: {{}} }} }} }},
              {{ kind: 'Signal', moduleId: 'wma-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ value: {{}} }}, outputs: {{ wma: {{}} }} }} }},
              {{ kind: 'Signal', moduleId: 'bollinger-bands-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ price: {{}} }}, outputs: {{ middle: {{}}, upper: {{}}, lower: {{}} }} }} }},
            ] }};
            const records = Array.from({{ length: 121 }}, (_, index) => ({{ instrumentId: `US-${{index}}` }}));
            const first = api.marketPage(records, 1);
            assert.equal(first.records.length, 50);
            assert.deepEqual([first.start, first.end, first.total, first.pageCount], [1, 50, 121, 3]);
            const last = api.marketPage(records, 3);
            assert.equal(last.records.length, 21);
            assert.deepEqual([last.start, last.end], [101, 121]);
            assert.equal(api.marketPage(records, 999).page, 3);
            assert.deepEqual([api.marketPage([], 1).start, api.marketPage([], 1).end], [0, 0]);

            api.state.market = {{ snapshot: {{ snapshotId: 'snapshot-1' }} }};
            const instrument = {{
              instrumentId: 'US-AAPL', symbol: 'AAPL', name: 'Apple',
              exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', availablePeriods: ['day'],
            }};
            const missing = api.instrumentRow(instrument, false, new Map(), new Map());
            assert.equal(missing.children[0].children[3].textContent, '');
            const lastTime = '2026-08-25T20:00:00Z';
            const present = api.instrumentRow(instrument, false, new Map([[
              'US-AAPL', {{ lastTime, asOf: '2099-01-01T00:00:00Z' }},
            ]]), new Map());
            assert.equal(
              present.children[0].children[3].textContent,
              `Snapshot cached · ${{api.formatInstant(lastTime)}}`,
            );
            assert.equal(
              present.children[0].children[3].title,
              `Cached K-line snapshot through ${{lastTime}}`,
            );
            assert.equal(present.children[0].children[3].dataset.snapshotState, 'cached');
            """
        )
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_market_provider_uses_eodhd_demo_us(self):
        source = (ROOT / "web" / "basic_workflow.js").read_text()
        self.assertIn('const BASIC_MARKET_PROVIDER_ID = "eodhd-demo-us";', source)

    def test_market_bar_snapshot_contract_is_exact_and_scoped(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ requireMarketEnvelope }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const instrument = {{
              instrumentId: 'US-AAPL', symbol: 'AAPL', name: 'Apple', exchange: 'NASDAQ',
              currency: 'USD', assetType: 'stock', availablePeriods: ['day'],
            }};
            const snapshot = {{
              schemaVersion: 1, snapshotId: 'snapshot-1', protocolId: 'trade.basic-workflow',
              providerId: 'provider', asOf: '2026-08-26T00:00:00Z',
              contentDigest: `sha256:${{'a'.repeat(64)}}`, instrumentCount: 1,
              instruments: [instrument],
            }};
            const bars = {{
              catalogSnapshotId: 'snapshot-1', providerId: 'provider', instrumentId: 'US-AAPL',
              period: 'day', asOf: '2026-08-26T00:00:00Z',
              firstTime: '2026-08-25T20:00:00Z', lastTime: '2026-08-25T20:00:00Z',
              barCount: 1, contentDigest: `sha256:${{'b'.repeat(64)}}`,
              datasetId: 'dataset-1', datasetVersionId: 'version-1',
            }};
            const envelope = {{ protocolId: 'trade.basic-workflow', snapshot, watchlist: [], barSnapshots: [bars], snapshotJobs: [] }};
            assert.equal(context.__basicTest.requireMarketEnvelope(structuredClone(envelope)).barSnapshots[0].lastTime, bars.lastTime);
            const missing = structuredClone(envelope);
            delete missing.barSnapshots[0].lastTime;
            assert.throws(() => context.__basicTest.requireMarketEnvelope(missing), /must contain exactly/);
            const unexpected = structuredClone(envelope);
            unexpected.barSnapshots[0].fallbackTime = bars.asOf;
            assert.throws(() => context.__basicTest.requireMarketEnvelope(unexpected), /must contain exactly/);
            const olderCatalogLineage = structuredClone(envelope);
            olderCatalogLineage.barSnapshots[0].catalogSnapshotId = 'snapshot-older';
            assert.equal(
              context.__basicTest.requireMarketEnvelope(olderCatalogLineage).barSnapshots[0].catalogSnapshotId,
              'snapshot-older',
            );
            const unavailablePeriod = structuredClone(envelope);
            unavailablePeriod.barSnapshots[0].period = 'minute';
            assert.throws(() => context.__basicTest.requireMarketEnvelope(unavailablePeriod), /period is unavailable/);
            """
        )
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_surfaces_reuse_trade_engine_visual_tokens(self):
        source = (ROOT / "web" / "basic_workflow.css").read_text(encoding="utf-8")
        self.assertNotIn("color-scheme: dark", source)
        self.assertNotIn("radial-gradient", source)
        self.assertNotIn("linear-gradient", source)
        self.assertNotIn("--accent:", source)
        for token in ("var(--bg)", "var(--panel)", "var(--line)", "var(--text)", "var(--muted)", "var(--accent)"):
            self.assertIn(token, source)

        plain_status = source.split(".bw-plain-status {", 1)[1].split("}", 1)[0]
        self.assertIn("border: 0;", plain_status)
        self.assertIn("background: transparent;", plain_status)
        self.assertNotRegex(plain_status, r"(?m)^\s*(?:min-|max-)?height\s*:")
        tabs = source.split(".bw-market-tabs button {", 1)[1].split("}", 1)[0]
        self.assertIn("border-bottom: 2px solid transparent;", tabs)
        self.assertIn("border-radius: 0;", tabs)
        self.assertIn("background: transparent;", tabs)

    def test_basic_home_visible_dom_never_exposes_opaque_identities(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, renderMarket, selectMarketTab, setStatus }};',
            );
            function element(tag = 'div') {{
              const attributes = {{}};
              return {{
                tag, attributes, children: [], textContent: '', title: '', value: '',
                dataset: {{}}, disabled: false, hidden: false, scrollTop: 0,
                classList: {{ toggle() {{}} }},
                append(...values) {{ this.children.push(...values); }},
                replaceChildren(...values) {{ this.children = [...values]; }},
                setAttribute(name, value) {{ attributes[name] = String(value); }},
              }};
            }}
            const ids = Object.fromEntries([
              'bwStatus', 'bwSyncMarket', 'bwInstrumentSearch', 'bwSnapshotTitle',
              'bwSnapshotMeta', 'bwListCount', 'bwPageRange', 'bwPreviousPage',
              'bwNextPage', 'bwInstrumentList', 'bwMarketTabPanel',
            ].map((id) => [id, element()]));
            const tabs = [element('button'), element('button')];
            tabs[0].id = 'bwWatchlistTab';
            tabs[1].id = 'bwAllStocksTab';
            tabs[0].dataset.marketTab = 'watchlist';
            tabs[1].dataset.marketTab = 'all';
            const document = {{
              getElementById(id) {{ return ids[id]; }},
              querySelectorAll(selector) {{ return selector === '[data-market-tab]' ? tabs : []; }},
              createElement: element,
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const digest = `sha256:${{'a'.repeat(64)}}`;
            const catalogId = 'basic-market-catalog-0123456789abcdef01234567';
            const datasetId = 'basic-market-abcdef0123456789abcdef01';
            const datasetVersionId = `${{datasetId}}@${{digest}}`;
            const instrument = {{
              instrumentId: 'US-AAPL', symbol: 'AAPL', name: 'Apple', exchange: 'NASDAQ',
              currency: 'USD', assetType: 'stock', availablePeriods: ['day'],
            }};
            api.state.market = {{
              protocolId: 'trade.basic-workflow',
              snapshot: {{
                schemaVersion: 1, snapshotId: catalogId, protocolId: 'trade.basic-workflow',
                providerId: 'nasdaq-us-snapshot', asOf: '2026-08-26T12:00:00Z',
                contentDigest: digest, instrumentCount: 1, instruments: [instrument],
              }},
              watchlist: [instrument],
              barSnapshots: [{{
                catalogSnapshotId: catalogId, providerId: 'nasdaq-us-snapshot',
                instrumentId: 'US-AAPL', period: 'day', asOf: '2026-08-26T12:00:00Z',
                firstTime: '2026-08-25T20:00:00Z', lastTime: '2026-08-25T20:00:00Z',
                barCount: 1, contentDigest: digest, datasetId, datasetVersionId,
              }}],
              snapshotJobs: [],
            }};
            api.renderMarket();
            assert.equal(tabs[0].attributes['aria-selected'], 'true');
            assert.equal(tabs[0].tabIndex, 0);
            assert.equal(tabs[1].attributes['aria-selected'], 'false');
            assert.equal(tabs[1].tabIndex, -1);
            assert.equal(ids.bwMarketTabPanel.attributes['aria-labelledby'], 'bwWatchlistTab');
            api.selectMarketTab('all');
            assert.equal(tabs[0].attributes['aria-selected'], 'false');
            assert.equal(tabs[0].tabIndex, -1);
            assert.equal(tabs[1].attributes['aria-selected'], 'true');
            assert.equal(tabs[1].tabIndex, 0);
            assert.equal(ids.bwMarketTabPanel.attributes['aria-labelledby'], 'bwAllStocksTab');
            api.setStatus(`catalogSnapshotId=${{catalogId}} hash=${{digest}}`, true);

            const visible = [];
            function collect(node) {{
              if (!node) return;
              if (node.textContent) visible.push(String(node.textContent));
              if (node.title) visible.push(String(node.title));
              if (node.attributes?.['aria-label']) visible.push(node.attributes['aria-label']);
              (node.children || []).forEach(collect);
            }}
            Object.values(ids).forEach(collect);
            const output = visible.join('\\n');
            for (const secret of [catalogId, digest, datasetId, datasetVersionId, 'nasdaq-us-snapshot', 'US-AAPL']) {{
              assert.equal(output.includes(secret), false, `visible home DOM leaked ${{secret}}`);
            }}
            assert.match(output, /AAPL/);
            assert.match(output, /Catalog updated/);
            assert.match(ids.bwStatus.textContent, /market action failed/i);
            """
        )
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_workspace_visible_dom_never_exposes_engine_identities(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, renderInstrumentHeader, renderProvenance, setStatus, setOpenStatus, showChartState }};',
            );
            function element(tag = 'div') {{
              const attributes = {{}};
              return {{
                tag, attributes, children: [], textContent: '', title: '', value: '',
                dataset: {{}}, disabled: false, hidden: false,
                append(...values) {{ this.children.push(...values); }},
                replaceChildren(...values) {{ this.children = [...values]; }},
                setAttribute(name, value) {{ attributes[name] = String(value); }},
              }};
            }}
            const ids = Object.fromEntries([
              'bwStatus', 'bwOpenStatus', 'bwChartState', 'bwFavoriteInstrument',
              'bwInstrumentTitle', 'bwInstrumentMeta', 'bwChartDataset',
              'bwChartPipeline', 'bwChartBacktest', 'bwOpenResult',
            ].map((id) => [id, element()]));
            ids.bwOpenResult.textContent = 'Open full Result';
            const document = {{ getElementById(id) {{ return ids[id]; }}, createElement: element }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const digest = `sha256:${{'b'.repeat(64)}}`;
            const catalogId = 'basic-market-catalog-fedcba9876543210fedcba98';
            const datasetId = 'basic-market-abcdef0123456789abcdef01';
            const pipelineId = 'pipe_01M0Z3942T4CBA6BJQVQ9H10DB';
            const backtestId = 'bt_01M0Z394KX497BFSF6C4DDWDGA';
            const visualizationId = `${{backtestId.toLowerCase()}}-current`;
            const instrument = {{
              instrumentId: 'US-AAPL', symbol: 'AAPL', name: 'Apple', exchange: 'NASDAQ',
              currency: 'USD', assetType: 'stock', availablePeriods: ['day'],
            }};
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.market = {{ snapshot: {{ instruments: [instrument] }}, watchlist: [instrument] }};
            api.renderInstrumentHeader();
            api.renderProvenance({{
              snapshotId: catalogId,
              instrument,
              barSnapshot: {{ period: 'day', barCount: 5, lastTime: '2026-08-25T20:00:00Z', contentDigest: digest }},
              materialization: {{
                dataset: {{ datasetId, datasetVersionId: `${{datasetId}}@${{digest}}` }},
                pipeline: {{ pipelineId, version: '2', contentDigest: digest }},
              }},
              job: {{ backtestId, status: 'queued' }},
            }});
            api.setStatus(`snapshotId=${{catalogId}} hash=${{digest}}`, true);
            api.setOpenStatus(`Visualizer 'market-candles-${{visualizationId}}' failed`, true);
            api.showChartState(`Backtest ${{backtestId}} failed`, `contentDigest=${{digest}}`);

            const visible = [];
            function collect(node) {{
              if (!node) return;
              if (node.textContent) visible.push(String(node.textContent));
              if (node.title) visible.push(String(node.title));
              if (node.attributes?.['aria-label']) visible.push(node.attributes['aria-label']);
              (node.children || []).forEach(collect);
            }}
            Object.values(ids).forEach(collect);
            const output = visible.join('\\n');
            for (const secret of [catalogId, digest, datasetId, pipelineId, backtestId, visualizationId, 'US-AAPL']) {{
              assert.equal(output.includes(secret), false, `visible workspace DOM leaked ${{secret}}`);
            }}
            assert.match(output, /AAPL/);
            assert.match(output, /1D bars/);
            assert.match(output, /Queued/);
            assert.match(ids.bwStatus.textContent, /workspace action failed/i);
            assert.match(ids.bwOpenStatus.textContent, /workspace action failed/i);
            """
        )
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_workspace_query_is_exact_and_fail_closed(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ workspaceSelection }};',
            );
            const location = {{
              pathname: '/basic-workflow/workspace',
              search: '?snapshotId=snapshot-1&instrumentId=US-AAPL&period=day',
              hash: '', replace() {{}},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}}, location,
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            assert.equal(
              JSON.stringify(context.__basicTest.workspaceSelection()),
              JSON.stringify({{ snapshotId: 'snapshot-1', instrumentId: 'US-AAPL', period: 'day' }}),
            );
            location.search = '?snapshotId=snapshot-1&instrumentId=US-AAPL&period=week';
            assert.throws(() => context.__basicTest.workspaceSelection(), /only the day period/);
            location.search = '?snapshotId=snapshot-1&instrumentId=US-AAPL&period=day&extra=x';
            assert.throws(() => context.__basicTest.workspaceSelection(), /Unsupported workspace parameter/);
            location.search = '?snapshotId=snapshot-1&snapshotId=snapshot-2&instrumentId=US-AAPL&period=day';
            assert.throws(() => context.__basicTest.workspaceSelection(), /must occur exactly once/);
            location.search = '?snapshotId=snapshot-1&period=day';
            assert.throws(() => context.__basicTest.workspaceSelection(), /Workspace instrumentId/);
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_client_uses_canonical_market_result_and_visualization_apis(self):
        source = (ROOT / "web" / "basic_workflow_workspace.js").read_text(encoding="utf-8")
        save_revision = source.split("async function saveVisualizationRevision(", 1)[1].split(
            "async function loadCurrentVisualization(", 1,
        )[0]
        load_materialized = source.split("async function loadMaterializedResult(", 1)[1].split(
            "async function openInstrument(", 1,
        )[0]
        open_instrument = source.split("async function openInstrument(", 1)[1].split(
            "function mapValues(", 1,
        )[0]
        for value in (
            '"/api/subsystems/basic/instruments/open"',
            'core.visualizerDependencyPlan',
            'core.drawFinancialPane',
            '"/api/visualizations"',
            'expectedRevision',
            'controller?.listTools?.().tools',
        ):
            self.assertIn(value, source)
        self.assertNotIn("addCandlestickSeries", source)
        self.assertNotIn("targetVisualizerId", source)
        self.assertNotIn('"horizontal-line"', source)
        self.assertNotIn('"trend-line"', source)
        self.assertNotIn('tool.id === "select"', source)
        self.assertIn("button.textContent = label;", source)
        self.assertIn('remove.textContent = "Delete";', source)
        self.assertIn('remove.setAttribute("aria-keyshortcuts", "Delete Backspace");', source)
        self.assertNotIn('`${job.backtestId}-current`', source)
        self.assertNotIn("hasOwn(view.dataKeys", source)
        self.assertNotIn("getJson(", save_revision)
        self.assertIn("result.visualization", save_revision)
        self.assertIn("loadCurrentVisualization(request, seq)", load_materialized)
        self.assertIn("/api/visualizations?backtestId=", source)
        self.assertNotIn("projectionCache.clear()", load_materialized)
        self.assertNotIn("disposeChart({ clearCache: true })", open_instrument)

    def test_render_projection_excludes_cross_protocol_dependency_closure(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, projectBasicVisualizationSpec }};',
            );
            const context = {{
              console,
              structuredClone,
              URLSearchParams,
              AbortController,
              window: {{}},
              document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const basic = 'trade.basic-workflow';
            api.state.catalog = {{
              visualizers: [
                {{ id: 'base', protocolId: basic, inputPorts: {{}}, capabilities: {{ requires: [] }} }},
                {{ id: 'line', protocolId: basic, inputPorts: {{ dataKey: {{}} }}, capabilities: {{ requires: [] }} }},
                {{ id: 'drawing', protocolId: basic, inputPorts: {{}}, capabilities: {{ requires: [{{ bindingParam: 'provider' }}] }} }},
                {{ id: 'outside', protocolId: 'vendor.other', inputPorts: {{}}, capabilities: {{ requires: [] }} }},
              ],
              modules: [
                {{ kind: 'Signal', moduleId: 'basic-temp', version: '1', protocolId: basic }},
              ],
            }};
            const canonical = {{
              schemaVersion: 3,
              datasetId: 'dataset',
              timeZone: 'UTC',
              temporaryModules: [
                {{ instanceId: 'outside-temp', kind: 'Signal', moduleId: 'outside-temp', version: '1', inputs: {{}}, outputs: {{ value: 'temp.outside.close' }}, config: {{}} }},
                {{ instanceId: 'basic-temp', kind: 'Signal', moduleId: 'basic-temp', version: '1', inputs: {{ source: 'temp.outside' }}, outputs: {{ value: 'temp.basic' }}, config: {{}} }},
              ],
              panes: [{{
                id: 'market', title: 'Market', role: 'financial',
                view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
                temporaryModules: [],
                visualizers: [
                  {{ id: 'base-layer', callback: 'base', params: {{}} }},
                  {{ id: 'outside-layer', callback: 'outside', params: {{}} }},
                  {{ id: 'cross-drawing', callback: 'drawing', params: {{ provider: 'outside-layer' }} }},
                  {{ id: 'basic-drawing', callback: 'drawing', params: {{ provider: 'base-layer' }} }},
                  {{ id: 'temp-line', callback: 'line', params: {{ dataKey: 'temp.basic' }} }},
                ],
              }}],
            }};
            const projected = api.projectBasicVisualizationSpec(canonical);
            assert.equal(JSON.stringify(projected.temporaryModules), '[]');
            assert.equal(
              JSON.stringify(projected.panes[0].visualizers.map((item) => item.id)),
              JSON.stringify(['base-layer', 'basic-drawing']),
            );
            assert.equal(
              JSON.stringify(canonical.panes[0].visualizers.map((item) => item.id)),
              JSON.stringify(['base-layer', 'outside-layer', 'cross-drawing', 'basic-drawing', 'temp-line']),
            );
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_indicator_catalog_supports_parameterized_instances_without_mutating_input(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, BASIC_INDICATORS, applyIndicatorMutation, addedIndicatorRecords, indicatorDisplayLabel, projectBasicVisualizationSpec }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const basic = 'trade.basic-workflow';
            const ports = (input, outputs) => ({{
              inputs: {{ [input]: {{ schema: {{}}, required: true }} }},
              outputs: Object.fromEntries(outputs.map((name) => [name, {{ schema: {{}}, required: true }}])),
            }});
            const configSchema = (properties) => ({{ type: 'object', properties, additionalProperties: false }});
            api.state.catalog = {{
              visualizers: [
                {{ id: 'ohlc.candles', protocolId: basic, inputPorts: {{}}, capabilities: {{ requires: [] }} }},
                {{ id: 'series.line', protocolId: basic, inputPorts: {{ dataKey: {{}}, timeKey: {{}} }}, capabilities: {{ requires: [] }} }},
              ],
              modules: [
                {{ kind: 'Signal', moduleId: 'basic-price-close-selector', version: '1', status: 'archived', protocolId: basic, ports: ports('price', ['close']) }},
                {{ kind: 'Signal', moduleId: 'sma-indicator', version: '1', status: 'active', builtin: true, ports: ports('value', ['sma']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'sma-indicator', version: '2', status: 'active', builtin: true, ports: ports('value', ['sma']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'ema-indicator', version: '1', status: 'active', builtin: true, ports: ports('value', ['ema']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'wma-indicator', version: '3', status: 'active', builtin: true, ports: ports('value', ['wma']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'bollinger-bands-indicator', version: '1', status: 'active', builtin: true, ports: ports('price', ['middle', 'upper', 'lower']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }}, k: {{ type: 'number', default: 2 }} }}) }},
                {{ kind: 'Signal', moduleId: 'sma-indicator', version: '99', status: 'active', protocolId: 'vendor.other', ports: ports('value', ['sma']), configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
              ],
            }};
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            const base = {{
              schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', panes: [{{
                id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
                view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
                visualizers: [{{
                  id: 'market-candles', callback: 'ohlc.candles', params: {{
                    dataKey: 'price.day.US-AAPL', timeDomainId: 'market-time', priceScaleId: 'market-price',
                  }},
                }}],
              }}],
            }};
            assert.equal(JSON.stringify(api.BASIC_INDICATORS.map((item) => item.typeId)), JSON.stringify(['sma', 'ema', 'wma', 'vwma', 'bb', 'rsi', 'macd', 'atr', 'stochastic', 'obv', 'roc', 'cci', 'williams-r', 'dmi', 'supertrend', 'mfi', 'parabolic-sar', 'volume', 'anchored-vwap', 'ichimoku']));
            const originalJson = JSON.stringify(base);
            const sma20 = api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }});
            const sma50 = api.applyIndicatorMutation(sma20, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 50 }} }});
            assert.equal(JSON.stringify(base), originalJson, 'indicator composition must be pure');
            assert.equal(JSON.stringify(api.addedIndicatorRecords(sma50).map((item) => item.instanceId)), JSON.stringify(['basic-indicator-sma-1', 'basic-indicator-sma-2']));
            assert.equal(JSON.stringify(sma50.temporaryModules.slice(1).map((item) => item.outputs)), JSON.stringify([
              {{ sma: 'indicator.basic.sma.1.sma' }}, {{ sma: 'indicator.basic.sma.2.sma' }},
            ]));
            assert.equal(sma50.panes[0].visualizers.at(-2).id, 'basic-indicator-sma-1-sma');
            assert.equal(sma50.panes[0].visualizers.at(-1).id, 'basic-indicator-sma-2-sma');
            assert.match(api.indicatorDisplayLabel(api.addedIndicatorRecords(sma50)[0]), /SMA.*20/);
            const withWmaBb = api.applyIndicatorMutation(
              api.applyIndicatorMutation(sma50, {{ type: 'add', indicatorTypeId: 'wma', config: {{ period: 34 }} }}),
              {{ type: 'add', indicatorTypeId: 'bb', config: {{ period: 30, k: 2.5 }} }},
            );
            const records = api.addedIndicatorRecords(withWmaBb);
            assert.equal(records.length, 4);
            assert.equal(JSON.stringify(records[2].config), JSON.stringify({{ period: 34 }}));
            assert.equal(JSON.stringify(records[3].config), JSON.stringify({{ period: 30, k: 2.5 }}));
            assert.equal(JSON.stringify(records[3].outputs), JSON.stringify({{ middle: 'indicator.basic.bb.1.middle', upper: 'indicator.basic.bb.1.upper', lower: 'indicator.basic.bb.1.lower' }}));
            assert.equal(withWmaBb.panes[0].visualizers.filter((item) => item.callback === 'series.line').length, 6);
            const projected = api.projectBasicVisualizationSpec(withWmaBb);
            assert.equal(JSON.stringify(projected.temporaryModules.map((item) => item.instanceId)), JSON.stringify([
              'basic-indicator-price-close', 'basic-indicator-sma-1', 'basic-indicator-sma-2', 'basic-indicator-wma-1', 'basic-indicator-bb-1',
            ]));
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_indicator_mutations_update_and_remove_by_instance(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const source = fs.readFileSync(filename, 'utf8').replace(
              'void main();',
              'globalThis.__basicTest = {{ state, applyIndicatorMutation, addedIndicatorRecords }};',
            );
            const context = {{ console, structuredClone, URLSearchParams, AbortController, window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }}, fetch() {{ throw new Error('fetch must not run'); }} }};
            context.globalThis = context;
            vm.runInNewContext(source, context, {{ filename }});
            const api = context.__basicTest;
            const configSchema = (properties) => ({{ type: 'object', properties, additionalProperties: false }});
            api.state.catalog = {{
              visualizers: [
                {{ id: 'ohlc.candles', protocolId: 'trade.basic-workflow' }},
                {{ id: 'series.line', protocolId: 'trade.basic-workflow' }},
              ],
              modules: [
                {{ kind: 'Signal', moduleId: 'basic-price-close-selector', version: '1', protocolId: 'trade.basic-workflow', status: 'archived', ports: {{ inputs: {{ price: {{}} }}, outputs: {{ close: {{}} }} }} }},
                {{ kind: 'Signal', moduleId: 'sma-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ value: {{}} }}, outputs: {{ sma: {{}} }} }}, configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'wma-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ value: {{}} }}, outputs: {{ wma: {{}} }} }}, configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'bollinger-bands-indicator', version: '1', builtin: true, status: 'active', ports: {{ inputs: {{ price: {{}} }}, outputs: {{ middle: {{}}, upper: {{}}, lower: {{}} }} }}, configSchema: configSchema({{ period: {{ type: 'integer', default: 20, minimum: 1 }}, k: {{ type: 'number', default: 2 }} }}) }},
              ],
            }};
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            const base = {{ schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', panes: [{{
              id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
              view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
              visualizers: [{{ id: 'market-candles', callback: 'ohlc.candles', params: {{ dataKey: 'price.day.US-AAPL', timeDomainId: 'market-time', priceScaleId: 'market-price' }} }}],
            }}] }};
            const originalJson = JSON.stringify(base);
            const both = api.applyIndicatorMutation(
              api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }}),
              {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 50 }} }},
            );
            const updated = api.applyIndicatorMutation(both, {{ type: 'update', instanceId: 'basic-indicator-sma-1', config: {{ period: 21 }} }});
            assert.equal(JSON.stringify(api.addedIndicatorRecords(updated).map((item) => item.instanceId)), JSON.stringify(['basic-indicator-sma-1', 'basic-indicator-sma-2']));
            assert.equal(JSON.stringify(updated.temporaryModules.find((item) => item.instanceId === 'basic-indicator-sma-1').config), JSON.stringify({{ period: 21 }}));
            assert.equal(updated.temporaryModules.find((item) => item.instanceId === 'basic-indicator-sma-1').version, both.temporaryModules.find((item) => item.instanceId === 'basic-indicator-sma-1').version);
            assert.equal(updated.temporaryModules.find((item) => item.instanceId === 'basic-indicator-sma-1').outputs.sma, 'indicator.basic.sma.1.sma');
            const one = api.applyIndicatorMutation(updated, {{ type: 'remove', instanceId: 'basic-indicator-sma-1' }});
            assert.equal(JSON.stringify(api.addedIndicatorRecords(one).map((item) => item.instanceId)), JSON.stringify(['basic-indicator-sma-2']));
            assert.equal(one.temporaryModules.some((item) => item.instanceId === 'basic-indicator-price-close'), true, 'shared selector remains while another indicator exists');
            const empty = api.applyIndicatorMutation(one, {{ type: 'remove', instanceId: 'basic-indicator-sma-2' }});
            assert.equal(api.addedIndicatorRecords(empty).length, 0);
            assert.equal(empty.temporaryModules.some((item) => item.instanceId === 'basic-indicator-price-close'), false, 'shared selector is removed with the last indicator');
            assert.throws(() => api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 0 }} }}));
            assert.throws(() => api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 2.5 }} }}));
            assert.throws(() => api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20, extra: 1 }} }}));
            const malformed = structuredClone(both);
            malformed.panes[0].visualizers = malformed.panes[0].visualizers.filter((item) => item.params?.dataKey !== 'indicator.basic.sma.1.sma');
            assert.throws(() => api.addedIndicatorRecords(malformed), /chart lines are malformed/);
            const reserved = structuredClone(base);
            reserved.temporaryModules = [{{
              instanceId: 'basic-indicator-price-close', kind: 'Signal', moduleId: 'vendor-selector', version: '1',
              config: {{}}, inputs: {{}}, outputs: {{ close: 'indicator.source.close' }},
            }}];
            assert.throws(() => api.applyIndicatorMutation(reserved, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }}), /reserved Module identity/);
            const legacy = api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }});
            const legacyModule = legacy.temporaryModules.find((item) => item.moduleId === 'sma-indicator');
            legacyModule.instanceId = 'basic-indicator-sma20';
            legacyModule.outputs.sma = 'indicator.sma20';
            const legacyLine = legacy.panes[0].visualizers.find((item) => item.callback === 'series.line');
            legacyLine.id = 'basic-indicator-sma20-line';
            legacyLine.params.dataKey = 'indicator.sma20';
            const legacyUpdated = api.applyIndicatorMutation(legacy, {{ type: 'update', instanceId: 'basic-indicator-sma20', config: {{ period: 50 }} }});
            const legacyRecord = api.addedIndicatorRecords(legacyUpdated)[0];
            assert.equal(legacyRecord.instanceId, 'basic-indicator-sma20');
            assert.equal(legacyRecord.outputs.sma, 'indicator.sma20');
            assert.equal(legacyRecord.config.period, 50);
            assert.equal(JSON.stringify(base), originalJson);
            """
        )
        completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_indicator_revision_save_is_pessimistic_and_conflict_safe(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, persistIndicatorMutation }};',
            );
            const classes = new Set();
            const chart = {{
              classList: {{ toggle(name, yes) {{ if (yes) classes.add(name); else classes.delete(name); }} }},
              setAttribute() {{}},
            }};
            const roots = new Map();
            const root = (id) => {{
              if (!roots.has(id)) roots.set(id, {{
                id, children: [], textContent: '', dataset: {{}},
                setAttribute(name, value) {{ this[name] = String(value); }},
                replaceChildren(...items) {{ this.children = items; }},
                append(...items) {{ this.children.push(...items); }},
                querySelector() {{ return null; }},
              }});
              return roots.get(id);
            }};
            const document = {{
              getElementById(id) {{ return id === 'bwChart' ? chart : root(id); }},
              createElement(tag) {{
                return {{
                  tag, dataset: {{}}, disabled: false, textContent: '',
                  setAttribute(name, value) {{ this[name] = String(value); }},
                  addEventListener() {{}},
                }};
              }},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const basic = 'trade.basic-workflow';
            const base = {{
              schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', temporaryModules: [], panes: [{{
                id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
                view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
                visualizers: [{{ id: 'market-candles', callback: 'ohlc.candles', params: {{
                  dataKey: 'price.day.US-AAPL', timeDomainId: 'market-time', priceScaleId: 'market-price',
                }} }}],
              }}],
            }};
            api.state.catalog = {{
              visualizers: [
                {{ id: 'ohlc.candles', protocolId: basic }},
                {{ id: 'series.line', protocolId: basic }},
              ],
              modules: [{{
                kind: 'Signal', moduleId: 'basic-price-close-selector', version: '1', status: 'archived', protocolId: basic,
                ports: {{ inputs: {{ price: {{}} }}, outputs: {{ close: {{}} }} }},
              }}, {{
                kind: 'Signal', moduleId: 'sma-indicator', version: '1', status: 'archived', builtin: true,
                ports: {{ inputs: {{ value: {{}} }}, outputs: {{ sma: {{}} }} }},
                configSchema: {{ type: 'object', properties: {{ period: {{ type: 'integer', default: 20, minimum: 1 }} }}, additionalProperties: false }},
              }}],
            }};
            api.state.openSeq = 4;
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            api.state.resultView = {{ backtestId: 'bt', dataKeys: {{}} }};
            api.state.visualizationCanonical = {{ revision: 1, spec: structuredClone(base) }};
            api.state.visualizationPresentationSpec = structuredClone(base);
            api.state.visualizationSaveRequest = {{
              backtestId: 'bt', visualizationId: 'current', name: 'current',
              expectedRevision: 1, spec: structuredClone(base),
            }};
            let renderCalls = 0;
            context.renderCurrentChart = async () => {{ renderCalls += 1; }};
            const saves = [];
            context.saveVisualizationRevision = async (request) => {{
              saves.push(structuredClone(request));
              assert.equal(api.state.indicatorBusy, true);
              return {{ revision: 2, spec: structuredClone(request.spec) }};
            }};

            (async () => {{
              assert.equal(await api.persistIndicatorMutation({{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }}), true);
              assert.equal(saves.length, 1);
              assert.equal(saves[0].expectedRevision, 1);
              assert.equal(api.state.visualizationCanonical.revision, 2);
              assert.equal(api.state.visualizationSaveRequest.expectedRevision, 2);
              assert.equal(api.state.visualizationCanonical.spec.temporaryModules.some((item) => item.instanceId === 'basic-indicator-sma-1'), true);
              assert.equal(api.state.indicatorBusy, false);
              assert.equal(classes.has('indicator-persistence-busy'), false);
              assert.equal(renderCalls, 1);

              const conflictSpec = structuredClone(base);
              context.saveVisualizationRevision = async () => {{
                const error = new Error('revision conflict');
                error.code = 'visualization_revision_conflict';
                error.currentVisualization = {{ revision: 9, spec: conflictSpec }};
                throw error;
              }};
              assert.equal(await api.persistIndicatorMutation({{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 50 }} }}), false);
              assert.equal(api.state.visualizationCanonical.revision, 9);
              assert.equal(api.state.visualizationSaveRequest.expectedRevision, 9);
              assert.equal(api.state.visualizationCanonical.spec.temporaryModules.some((item) => item.instanceId === 'basic-indicator-sma-1'), false);
              assert.equal(renderCalls, 2, 'a conflict renders only the server revision');
              assert.equal(root('bwIndicatorStatus').dataset.state, 'error');

              context.saveVisualizationRevision = async () => {{ throw new Error('repository unavailable'); }};
              assert.equal(await api.persistIndicatorMutation({{ type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }} }}), false);
              assert.equal(api.state.visualizationCanonical.revision, 9);
              assert.equal(api.state.visualizationCanonical.spec.temporaryModules.some((item) => item.instanceId === 'basic-indicator-sma-1'), false);
              assert.equal(renderCalls, 2, 'a rejected save must preserve the current chart');
              assert.equal(api.state.indicatorBusy, false);
            }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_first_indicator_set_compiles_against_installed_engine_contracts(self):
        from builtin_implementations import resources as builtin_resources
        from builtin_implementations.basic_workflow_contracts import PRICE_SCHEMA
        from builtin_implementations.visualizer_contracts import visualizer_definition_map
        from engine.compiler import visualization as visualization_compiler
        from engine.control import database as engine_database
        from engine.repository import module_definitions as module_repository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
                "liveRoot": str(root / "live"),
            }
            engine_database.prepare_database(config)
            builtin_resources.install(config)
            definitions = module_repository.load_pipeline_definitions(config)

            def latest_version(module_id):
                matches = [
                    definition
                    for definition in definitions.values()
                    if definition["kind"] == "Signal"
                    and definition["moduleId"] == module_id
                ]
                self.assertTrue(matches)
                return max(matches, key=lambda item: int(item["version"]))["version"]

            modules = [
                {
                    "instanceId": "basic-indicator-price-close",
                    "kind": "Signal",
                    "moduleId": "basic-price-close-selector",
                    "version": latest_version("basic-price-close-selector"),
                    "config": {
                        "decisionPeriod": "day",
                        "instrumentId": "US-AAPL",
                    },
                    "inputs": {"price": "price"},
                    "outputs": {"close": "indicator.source.close"},
                },
                {
                    "instanceId": "basic-indicator-sma-1",
                    "kind": "Signal",
                    "moduleId": "sma-indicator",
                    "version": latest_version("sma-indicator"),
                    "config": {"period": 20},
                    "inputs": {"value": "indicator.source.close"},
                    "outputs": {"sma": "indicator.basic.sma.1.sma"},
                },
                {
                    "instanceId": "basic-indicator-ema-1",
                    "kind": "Signal",
                    "moduleId": "ema-indicator",
                    "version": latest_version("ema-indicator"),
                    "config": {"period": 20},
                    "inputs": {"value": "indicator.source.close"},
                    "outputs": {"ema": "indicator.basic.ema.1.ema"},
                },
                {
                    "instanceId": "basic-indicator-wma-1",
                    "kind": "Signal",
                    "moduleId": "wma-indicator",
                    "version": latest_version("wma-indicator"),
                    "config": {"period": 34},
                    "inputs": {"value": "indicator.source.close"},
                    "outputs": {"wma": "indicator.basic.wma.1.wma"},
                },
                {
                    "instanceId": "basic-indicator-bb-1",
                    "kind": "Signal",
                    "moduleId": "bollinger-bands-indicator",
                    "version": latest_version("bollinger-bands-indicator"),
                    "config": {"period": 20, "k": 2},
                    "inputs": {"price": "indicator.source.close"},
                    "outputs": {
                        "middle": "indicator.basic.bb.1.middle",
                        "upper": "indicator.basic.bb.1.upper",
                        "lower": "indicator.basic.bb.1.lower",
                    },
                },
            ]
            line_keys = [
                ("basic-indicator-sma-1-sma", "indicator.basic.sma.1.sma", "#2563eb", 2),
                ("basic-indicator-ema-1-ema", "indicator.basic.ema.1.ema", "#f59e0b", 2),
                ("basic-indicator-wma-1-wma", "indicator.basic.wma.1.wma", "#16a34a", 2),
                ("basic-indicator-bb-1-upper", "indicator.basic.bb.1.upper", "#7c3aed", 1),
                ("basic-indicator-bb-1-middle", "indicator.basic.bb.1.middle", "#8b5cf6", 1),
                ("basic-indicator-bb-1-lower", "indicator.basic.bb.1.lower", "#7c3aed", 1),
            ]
            spec = {
                "schemaVersion": 3,
                "datasetId": "dataset",
                "timeZone": "UTC",
                "temporaryModules": modules,
                "panes": [{
                    "id": "market",
                    "title": "Market",
                    "role": "financial",
                    "view": {
                        "start": None,
                        "end": None,
                        "logScale": False,
                        "controlsCollapsed": False,
                    },
                    "temporaryModules": [],
                    "visualizers": [{
                        "id": "market-candles",
                        "callback": "ohlc.candles",
                        "params": {
                            "dataKey": "price.day.US-AAPL",
                            "timeDomainId": "market-time",
                            "priceScaleId": "market-price",
                        },
                    }] + [{
                        "id": identifier,
                        "callback": "series.line",
                        "params": {
                            "dataKey": data_key,
                            "timeKey": "time",
                            "timeDomainId": "market-time",
                            "priceScaleId": "market-price",
                            "color": color,
                            "lineWidth": width,
                        },
                    } for identifier, data_key, color, width in line_keys],
                }],
            }
            contracts = visualization_compiler.compile_visualization_contracts(
                {
                    "time": {"schema": {"type": "string"}, "required": True},
                    "price": {"schema": PRICE_SCHEMA, "required": True},
                },
                spec,
                definitions,
                visualizer_definition_map(),
            )
            for data_key in (
                "indicator.basic.sma.1.sma",
                "indicator.basic.ema.1.ema",
                "indicator.basic.wma.1.wma",
                "indicator.basic.bb.1.middle",
                "indicator.basic.bb.1.upper",
                "indicator.basic.bb.1.lower",
            ):
                self.assertIn(data_key, contracts)

    def test_basic_price_close_selector_is_exact_and_fail_closed(self):
        from builtin_implementations.pipeline.basic_price_close_selector import (
            BasicPriceCloseSelector,
        )

        selector = BasicPriceCloseSelector()
        selector.config = {"decisionPeriod": "day", "instrumentId": "US-AAPL"}
        self.assertEqual(
            selector.update({
                "day": {
                    "US-AAPL": {
                        "eventTime": "2026-08-26T20:00:00Z",
                        "open": 100.0,
                        "high": 103.0,
                        "low": 99.0,
                        "close": 102.0,
                    },
                },
            }),
            {"close": 102.0},
        )
        with self.assertRaisesRegex(ValueError, "instrument is absent"):
            selector.update({"day": {}})

    def test_drawing_toolbar_reenables_from_current_controller_after_mutation(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const persistence = original.split('function persistDrawingEvent(event) {{', 2)[1]
              .split('async function renderCurrentChart', 1)[0];
            assert.doesNotMatch(persistence, /setDrawingInteractionBusy\(true\)/);
            assert.doesNotMatch(persistence, /renderCurrentChart/);
            assert.match(original, /function startNextDrawingSave\(\)/);
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, syncDrawingToolAvailability, setDrawingInteractionBusy, handleDrawingKeydown }};',
            );
            const root = {{
              children: [],
              replaceChildren() {{ this.children = []; }},
              append(value) {{ this.children.push(value); }},
              querySelectorAll(selector) {{
                return selector === '[data-drawing-tool]'
                  ? this.children.filter((item) => Object.hasOwn(item.dataset, 'drawingTool'))
                  : [];
              }},
              querySelector(selector) {{
                return selector === '[data-drawing-delete]'
                  ? this.children.find((item) => Object.hasOwn(item.dataset, 'drawingDelete')) || null
                  : null;
              }},
            }};
            const status = {{ textContent: '', dataset: {{ }}, classList: {{ toggle() {{}} }} }};
            const chartClasses = new Set();
            const chart = {{
              focusCalls: 0, attributes: {{}},
              focus() {{ this.focusCalls += 1; }},
              setAttribute(name, value) {{ this.attributes[name] = String(value); }},
              classList: {{ toggle(name, yes) {{
                if (yes) chartClasses.add(name); else chartClasses.delete(name);
              }} }},
            }};
            const document = {{
              getElementById(id) {{
                if (id === 'bwDrawingTools') return root;
                if (id === 'bwDrawingStatus') return status;
                if (id === 'bwChart') return chart;
                return null;
              }},
              createElement() {{
                const classes = new Set();
                const attributes = {{}};
                return {{
                  dataset: {{}}, disabled: false, textContent: '', className: '', attributes,
                  listeners: {{}}, classList: {{ toggle(name, yes) {{
                    if (yes) classes.add(name); else classes.delete(name);
                  }} }},
                  setAttribute(name, value) {{ attributes[name] = String(value); }},
                  addEventListener(name, listener) {{ this.listeners[name] = listener; }},
                }};
              }},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const basic = 'trade.basic-workflow';
            api.state.catalog = {{
              modules: [],
              visualizers: [{{
                id: 'ohlc.candles', protocolId: basic, inputPorts: {{}},
                capabilities: {{ requires: [] }},
              }}],
            }};
            api.state.visualizationPresentationSpec = {{
              schemaVersion: 3, datasetId: 'd', timeZone: 'UTC', panes: [{{
                id: 'market', title: 'Market', role: 'financial',
                view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
                temporaryModules: [],
                visualizers: [{{
                  id: 'base-candle', callback: 'ohlc.candles',
                  params: {{ dataKey: 'price.day.US-AAPL' }},
                }}],
              }}],
            }};
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            let deleteCalls = 0;
            let cancelCalls = 0;
            api.state.chartContext = {{ interactionController: {{
              listTools() {{ return {{ tools: [
                {{ id: 'selection-from-core', label: 'Select', bindings: [] }},
                {{ id: 'extension-tool', label: 'Extension', bindings: [{{
                  name: 'coordinate', candidates: [{{ visualizerId: 'base-candle' }}],
                }}] }},
              ] }}; }},
              activate() {{ return true; }},
              cancel() {{ cancelCalls += 1; }},
              deleteSelected() {{ deleteCalls += 1; return true; }},
            }} }};
            api.state.drawingBusy = true;
            api.syncDrawingToolAvailability();
            assert.equal(root.children[0].disabled, true);
            assert.equal(root.children[1].disabled, true);
            assert.equal(root.children[2].textContent, 'Delete');
            assert.equal(root.children[2].attributes['aria-keyshortcuts'], 'Delete Backspace');
            api.state.drawingBusy = false;
            api.syncDrawingToolAvailability();
            assert.equal(root.children[0].disabled, false);
            assert.equal(root.children[1].disabled, false);
            assert.equal(root.children[1].dataset.drawingTool, 'extension-tool');
            assert.equal(root.children[0].textContent, 'Select');
            assert.equal(root.children[1].textContent, 'Extension');
            assert.equal(root.children[2].disabled, true);

            api.state.selectedDrawingId = 'drawing-from-controller';
            api.syncDrawingToolAvailability();
            assert.equal(root.children[2].disabled, false);
            let prevented = false;
            api.handleDrawingKeydown({{
              key: 'Delete', target: {{ matches() {{ return false; }} }},
              preventDefault() {{ prevented = true; }},
            }});
            assert.equal(deleteCalls, 1);
            assert.equal(prevented, true);
            api.handleDrawingKeydown({{
              key: 'Backspace', target: {{ matches() {{ return true; }} }},
              preventDefault() {{ throw new Error('input deletion must not be intercepted'); }},
            }});
            assert.equal(deleteCalls, 1);
            api.setDrawingInteractionBusy(true);
            assert.equal(api.state.drawingBusy, true);
            assert.equal(api.state.selectedDrawingId, '');
            assert.equal(cancelCalls, 1);
            assert.equal(chart.attributes['aria-busy'], 'true');
            assert.equal(chartClasses.has('drawing-persistence-busy'), true);
            assert.equal(root.children.every((button) => button.disabled), true);
            api.setDrawingInteractionBusy(false);
            assert.equal(api.state.drawingBusy, false);
            assert.equal(chart.attributes['aria-busy'], 'false');
            assert.equal(chartClasses.has('drawing-persistence-busy'), false);
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_basic_chart_uses_core_cleanup_order_without_reversal(self):
        source = (ROOT / "web" / "basic_workflow_workspace.js").read_text(encoding="utf-8")
        self.assertIn('"/api/subsystems/basic/result-projections"', source)
        dispose = source.split("function disposeChart(", 1)[1].split(
            "async function projectionForPlan(", 1,
        )[0]
        self.assertIn("for (const cleanup of state.chartContext?.cleanups || [])", dispose)
        self.assertNotIn(".reverse()", dispose)

    def test_indicator_output_paths_share_one_pending_completed_and_restored_projection(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, projectPane, restoreProjectionCache }};',
            );
            const storage = new Map();
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}}, location: {{}},
              sessionStorage: {{
                getItem(key) {{ return storage.has(key) ? storage.get(key) : null; }},
                setItem(key, value) {{ storage.set(key, String(value)); }},
                removeItem(key) {{ storage.delete(key); }},
              }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            api.state.openSeq = 4;
            api.state.projectionCacheOwner = 'user_1';
            const calls = [];
            let resolvePost;
            context.postJson = async (_path, payload) => {{
              calls.push(structuredClone(payload));
              return new Promise((resolve) => {{ resolvePost = resolve; }});
            }};
            const module = {{ instanceId: 'bb-1', moduleId: 'bollinger-bands-indicator' }};
            const plans = ['lower', 'middle', 'upper'].map((output) => ({{
              visualizerId: `bb-${{output}}`,
              paths: [`cycles.data.indicator.basic.bb.1.${{output}}`],
              temporaryModules: [module],
            }}));
            context.window.TradeChartCore = {{ visualizerDependencyPlan() {{ return plans; }} }};
            const view = {{ backtestId: 'bt_1', dataKeys: {{}} }};
            const first = api.projectPane(view, {{}}, {{}}, 4);
            await Promise.resolve();
            assert.equal(calls.length, 1, 'three Bollinger output paths must share one Runtime');
            assert.deepEqual(calls[0].paths, [
              'cycles.data.indicator.basic.bb.1.lower',
              'cycles.data.indicator.basic.bb.1.middle',
              'cycles.data.indicator.basic.bb.1.upper',
            ]);
            resolvePost({{ result: {{ cycles: [] }} }});
            const firstResult = await first;
            assert.deepEqual(Object.keys(firstResult.instanceResults).sort(), [
              'bb-lower', 'bb-middle', 'bb-upper',
            ]);
            await api.projectPane(view, {{}}, {{}}, 4);
            assert.equal(calls.length, 1, 'completed dependency projection must remain cached');
            assert.equal(storage.size, 1, 'completed Result slice must be session-persisted');
            api.state.projectionCache.clear();
            api.restoreProjectionCache('user_1');
            await api.projectPane(view, {{}}, {{}}, 4);
            assert.equal(calls.length, 1, 'restored immutable Result slice must avoid recomputation');
            """
        )
        completed = subprocess.run(
            ["node", "-e", f"(async () => {{{script}}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _legacy_delayed_drawing_save_locks_pointer_input_without_promoting_pending_spec(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, persistDrawingEvent }};',
            );
            const classes = new Set();
            const chart = {{
              attributes: {{}},
              classList: {{ toggle(name, yes) {{
                if (yes) classes.add(name); else classes.delete(name);
              }} }},
              setAttribute(name, value) {{ this.attributes[name] = String(value); }},
            }};
            const openStatus = {{ textContent: '', dataset: {{}} }};
            const drawingStatus = {{ textContent: '', dataset: {{}} }};
            const document = {{
              getElementById(id) {{
                if (id === 'bwChart') return chart;
                if (id === 'bwOpenStatus') return openStatus;
                if (id === 'bwDrawingStatus') return drawingStatus;
                return null;
              }},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{ TradeChartCore: {{
                upsertIdentity(items, previousId, next) {{
                  return previousId
                    ? items.map((item) => item.id === previousId ? structuredClone(next) : item)
                    : [...items, structuredClone(next)];
                }},
              }} }},
              document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            let resolveSave;
            let saveCalls = 0;
            let savedRequest = null;
            let renderCalls = 0;
            let cancelCalls = 0;
            context.syncDrawingToolAvailability = () => {{}};
            context.renderCurrentChart = async () => {{ renderCalls += 1; }};
            context.saveAndVerifyVisualization = (request) => {{
              saveCalls += 1;
              savedRequest = structuredClone(request);
              return new Promise((resolve) => {{ resolveSave = resolve; }});
            }};
            api.state.openSeq = 7;
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            api.state.visualizationSpec = {{ panes: [{{
              id: 'market',
              visualizers: [{{
                id: 'base-candle', callback: 'ohlc.candles',
                params: {{ dataKey: 'price.day.US-AAPL' }},
              }}],
            }}] }};
            api.state.visualizationSaveRequest = {{
              backtestId: 'bt', visualizationId: 'current', name: 'current',
              expectedRevision: 1, spec: structuredClone(api.state.visualizationSpec),
            }};
            api.state.chartContext = {{ interactionController: {{
              cancel() {{ cancelCalls += 1; }},
            }} }};
            const committed = {{
              id: 'drawing-from-core', callback: 'descriptor-supplied-callback',
              params: {{ target: 'base-candle', points: [1, 2] }},
            }};

            (async () => {{
              const pending = api.persistDrawingEvent({{ type: 'commit', instance: committed }});
              assert.equal(saveCalls, 1);
              assert.equal(cancelCalls, 1);
              assert.equal(api.state.drawingBusy, true);
              assert.equal(chart.attributes['aria-busy'], 'true');
              assert.equal(classes.has('drawing-persistence-busy'), true);
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === committed.id),
                false,
                'pending drawing must not become canonical before CAS verification',
              );
              await api.persistDrawingEvent({{ type: 'commit', instance: committed }});
              assert.equal(saveCalls, 1, 'busy surface must not queue a second drawing save');
              resolveSave({{ revision: 2, spec: structuredClone(savedRequest.spec) }});
              await pending;
              assert.equal(api.state.drawingBusy, false);
              assert.equal(chart.attributes['aria-busy'], 'false');
              assert.equal(classes.has('drawing-persistence-busy'), false);
              assert.equal(renderCalls, 1);
              assert.equal(api.state.visualizationSaveRequest.expectedRevision, 2);
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === committed.id),
                true,
              );

              const failed = {{
                id: 'drawing-not-saved', callback: 'descriptor-supplied-callback',
                params: {{ target: 'base-candle', points: [3, 4] }},
              }};
              context.saveAndVerifyVisualization = async () => {{ throw new Error('repository unavailable'); }};
              await api.persistDrawingEvent({{ type: 'commit', instance: failed }});
              assert.equal(renderCalls, 2, 'ordinary failure must redraw the last canonical spec');
              assert.equal(api.state.drawingBusy, false);
              assert.equal(chart.attributes['aria-busy'], 'false');
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === failed.id),
                false,
                'failed drawing must not enter canonical state',
              );

              const conflictingSave = {{
                id: 'drawing-stale-conflict', callback: 'descriptor-supplied-callback',
                params: {{ target: 'base-candle', points: [5, 6] }},
              }};
              const serverDrawing = {{
                id: 'drawing-from-server', callback: 'descriptor-supplied-callback',
                params: {{ target: 'base-candle', points: [7, 8] }},
              }};
              context.saveAndVerifyVisualization = async () => {{
                const error = new Error('revision conflict');
                error.code = 'visualization_revision_conflict';
                throw error;
              }};
              context.reloadVisualizationAfterConflict = async () => {{
                const serverSpec = structuredClone(api.state.visualizationSpec);
                serverSpec.panes[0].visualizers.push(serverDrawing);
                api.state.visualizationSpec = serverSpec;
                renderCalls += 1;
                return {{ revision: 3, spec: serverSpec }};
              }};
              await api.persistDrawingEvent({{ type: 'commit', instance: conflictingSave }});
              assert.equal(api.state.drawingBusy, false);
              assert.equal(chart.attributes['aria-busy'], 'false');
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === conflictingSave.id),
                false,
                'stale conflict draft must not be merged',
              );
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === serverDrawing.id),
                true,
                'successful conflict reload must use the server canonical spec',
              );

              const conflicted = {{
                id: 'drawing-conflict', callback: 'descriptor-supplied-callback',
                params: {{ target: 'base-candle', points: [9, 10] }},
              }};
              context.saveAndVerifyVisualization = async () => {{
                const error = new Error('revision conflict');
                error.code = 'visualization_revision_conflict';
                throw error;
              }};
              context.reloadVisualizationAfterConflict = async () => {{ throw new Error('reload unavailable'); }};
              await api.persistDrawingEvent({{ type: 'commit', instance: conflicted }});
              assert.equal(api.state.drawingBusy, true, 'failed conflict reload must remain fail-closed');
              assert.equal(chart.attributes['aria-busy'], 'true');
              assert.equal(classes.has('drawing-persistence-busy'), true);
              assert.match(openStatus.textContent, /Reload this workspace/);
              assert.equal(
                api.state.visualizationSpec.panes[0].visualizers.some((item) => item.id === conflicted.id),
                false,
              );
            }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_drawing_persistence_is_local_first_serial_and_fail_closed(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, persistDrawingEvent }};',
            );
            const classes = new Set();
            const chart = {{
              attributes: {{}},
              classList: {{ toggle(name, yes) {{
                if (yes) classes.add(name); else classes.delete(name);
              }} }},
              setAttribute(name, value) {{ this.attributes[name] = String(value); }},
            }};
            const openStatus = {{ textContent: '', dataset: {{}} }};
            const drawingStatus = {{ textContent: '', dataset: {{}} }};
            const toolRoot = {{ replaceChildren() {{}}, querySelector() {{ return null; }} }};
            const document = {{
              getElementById(id) {{
                if (id === 'bwChart') return chart;
                if (id === 'bwOpenStatus') return openStatus;
                if (id === 'bwDrawingStatus') return drawingStatus;
                if (id === 'bwDrawingTools') return toolRoot;
                return null;
              }},
            }};
            const context = {{
              console, structuredClone, URLSearchParams, AbortController, setTimeout,
              window: {{ TradeChartCore: {{
                upsertIdentity(items, previousId, next) {{
                  return previousId
                    ? items.map((item) => item.id === previousId ? structuredClone(next) : item)
                    : [...items, structuredClone(next)];
                }},
              }} }},
              document,
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const basic = 'trade.basic-workflow';
            const baseSpec = {{
              schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', panes: [{{
              id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
              view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
              visualizers: [{{
                id: 'base-candle', callback: 'ohlc.candles',
                params: {{ dataKey: 'price.day.US-AAPL' }},
              }}],
            }}] }};
            api.state.catalog = {{
              modules: [],
              visualizers: [
                {{ id: 'ohlc.candles', protocolId: basic, inputPorts: {{}}, capabilities: {{ requires: [] }} }},
                {{
                  id: 'drawing.generic', protocolId: basic, inputPorts: {{}},
                  capabilities: {{ requires: [{{ bindingParam: 'targetVisualizerId' }}] }},
                }},
              ],
            }};
            api.state.openSeq = 7;
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            api.state.visualizationCanonical = {{ revision: 1, spec: structuredClone(baseSpec) }};
            api.state.visualizationPresentationSpec = structuredClone(baseSpec);
            api.state.visualizationSaveRequest = {{
              backtestId: 'bt', visualizationId: 'current', name: 'current',
              expectedRevision: 1, spec: structuredClone(baseSpec),
            }};
            let cancelCalls = 0;
            const reconciliations = [];
            api.state.chartContext = {{ interactionController: {{
              cancel() {{ cancelCalls += 1; }},
              reconcilePresentation(value) {{
                reconciliations.push(structuredClone(value));
                return true;
              }},
            }} }};
            context.syncDrawingToolAvailability = () => {{}};
            let renderCalls = 0;
            context.renderCurrentChart = async () => {{ renderCalls += 1; }};
            const saves = [];
            context.saveVisualizationRevision = (request) => {{
              let resolve;
              let reject;
              const promise = new Promise((yes, no) => {{ resolve = yes; reject = no; }});
              saves.push({{ request: structuredClone(request), resolve, reject }});
              return promise;
            }};
            const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
            const drawing = (id, value) => ({{
              id, callback: 'drawing.generic',
              params: {{ targetVisualizerId: 'base-candle', value }},
            }});
            const hasDrawing = (spec, id) => spec.panes[0].visualizers.some((item) => item.id === id);

            (async () => {{
              const first = drawing('drawing-first', 1);
              const second = drawing('drawing-second', 2);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: first }}), true);
              assert.equal(saves.length, 1);
              assert.equal(api.state.drawingBusy, false, 'an in-flight save must not lock drawing input');
              assert.equal(api.state.pendingDrawingMutations.length, 1);
              assert.equal(api.state.drawingSaveInFlight.mutationSequence, 1);
              assert.equal(hasDrawing(api.state.visualizationCanonical.spec, first.id), false);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, first.id), true);

              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: second }}), true);
              assert.equal(saves.length, 1, 'only one CAS request may be in flight');
              assert.equal(api.state.pendingDrawingMutations.length, 2);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, second.id), true);

              saves[0].resolve({{ revision: 2, spec: structuredClone(saves[0].request.spec) }});
              await settle();
              assert.equal(saves.length, 2);
              assert.equal(saves[1].request.expectedRevision, 2);
              assert.equal(hasDrawing(saves[1].request.spec, first.id), true);
              assert.equal(hasDrawing(saves[1].request.spec, second.id), true);
              assert.equal(api.state.visualizationCanonical.revision, 2);
              assert.equal(api.state.pendingDrawingMutations.length, 1);

              saves[1].resolve({{ revision: 3, spec: structuredClone(saves[1].request.spec) }});
              await settle();
              assert.equal(api.state.visualizationCanonical.revision, 3);
              assert.equal(api.state.pendingDrawingMutations.length, 0);
              assert.equal(api.state.drawingSaveInFlight, null);
              assert.equal(renderCalls, 0, 'successful drawing ACKs must not rebuild the chart');

              assert.equal(api.persistDrawingEvent({{ type: 'delete', visualizerId: first.id }}), true);
              assert.equal(saves.length, 3);
              assert.equal(saves[2].request.expectedRevision, 3);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, first.id), false);
              assert.equal(hasDrawing(api.state.visualizationCanonical.spec, first.id), true);
              saves[2].resolve({{ revision: 4, spec: structuredClone(saves[2].request.spec) }});
              await settle();
              assert.equal(api.state.visualizationCanonical.revision, 4);
              assert.equal(hasDrawing(api.state.visualizationCanonical.spec, first.id), false);
              assert.equal(renderCalls, 0);

              const failed = drawing('drawing-unconfirmed', 3);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: failed }}), true);
              assert.equal(saves.length, 4);
              saves[3].reject(new Error('repository unavailable'));
              await settle();
              assert.equal(api.state.drawingBusy, true);
              assert.equal(api.state.pendingDrawingMutations.length, 1, 'unconfirmed mutations stay ordered and retained');
              assert.equal(api.state.drawingSaveInFlight, null);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, failed.id), false);
              assert.equal(reconciliations.length, 1);
              assert.equal(saves.length, 4, 'an uncertain save must never retry automatically');
              assert.equal(renderCalls, 0);
              assert.equal(cancelCalls, 1);

              api.state.pendingDrawingMutations = [];
              api.state.drawingPersistenceFailure = null;
              api.state.drawingBusy = false;
              api.state.visualizationPresentationSpec = structuredClone(api.state.visualizationCanonical.spec);
              const conflicting = drawing('drawing-conflict', 4);
              const conflictQueued = drawing('drawing-conflict-queued', 5);
              const serverDrawing = drawing('drawing-from-server', 90);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: conflicting }}), true);
              assert.equal(saves.length, 5);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: conflictQueued }}), true);
              assert.equal(saves.length, 5, 'the second local operation must remain behind the conflicting request');
              const serverSpec = structuredClone(api.state.visualizationCanonical.spec);
              serverSpec.panes[0].visualizers.push(serverDrawing);
              const conflict = new Error('revision conflict');
              conflict.code = 'visualization_revision_conflict';
              conflict.currentVisualization = {{
                visualizationId: 'current', backtestId: 'bt', name: 'current',
                createdAt: '2026-08-26T20:00:00Z', revision: 9, spec: serverSpec,
              }};
              saves[4].reject(conflict);
              await settle();
              assert.equal(api.state.drawingBusy, true);
              assert.equal(api.state.pendingDrawingMutations.length, 0, 'conflicting local operations must be isolated');
              assert.equal(api.state.drawingPersistenceFailure.code, 'conflict');
              assert.equal(api.state.visualizationCanonical.revision, 9);
              assert.equal(api.state.visualizationSaveRequest.expectedRevision, 9);
              assert.equal(hasDrawing(api.state.visualizationCanonical.spec, serverDrawing.id), true);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, serverDrawing.id), true);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, conflicting.id), false);
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, conflictQueued.id), false);
              assert.equal(reconciliations.length, 2);
              assert.equal(saves.length, 5, 'a conflict must not merge or retry automatically');
              assert.match(openStatus.textContent, /Reload this workspace/);
              assert.equal(renderCalls, 0);

              api.state.pendingDrawingMutations = [];
              api.state.drawingPersistenceFailure = null;
              api.state.drawingBusy = false;
              api.state.visualizationPresentationSpec = structuredClone(api.state.visualizationCanonical.spec);
              api.state.chartContext.interactionController.reconcilePresentation = () => false;
              const unreconciled = drawing('drawing-unreconciled', 5);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: unreconciled }}), true);
              assert.equal(saves.length, 6);
              saves[5].reject(new Error('save failed'));
              await settle();
              assert.equal(api.state.drawingBusy, true);
              assert.equal(
                hasDrawing(api.state.visualizationPresentationSpec, unreconciled.id),
                true,
                'failed atomic reconciliation must preserve the existing live presentation',
              );
              assert.equal(renderCalls, 0, 'reconciliation failure must not fall back to a chart rebuild');

              api.state.pendingDrawingMutations = [];
              api.state.drawingPersistenceFailure = null;
              api.state.drawingBusy = false;
              api.state.visualizationPresentationSpec = structuredClone(api.state.visualizationCanonical.spec);
              api.state.chartContext.interactionController.reconcilePresentation = (value) => {{
                reconciliations.push(structuredClone(value));
                return true;
              }};
              context.window.TradeChartCore.upsertIdentity = () => {{
                throw new Error('local staging rejected');
              }};
              const ghost = drawing('drawing-not-staged', 6);
              assert.equal(api.persistDrawingEvent({{ type: 'commit', instance: ghost }}), false);
              assert.equal(api.state.drawingBusy, true, 'a rejected local stage must fail closed');
              assert.equal(api.state.drawingPersistenceFailure.code, 'stage-failed');
              assert.equal(api.state.pendingDrawingMutations.length, 0);
              assert.equal(reconciliations.length, 3, 'the already-live Core scene must reconcile to canonical');
              assert.equal(hasDrawing(api.state.visualizationPresentationSpec, ghost.id), false);
              assert.equal(saves.length, 6, 'a rejected local stage must not start persistence');
              assert.equal(renderCalls, 0);
            }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_visualization_conflict_preserves_only_a_strict_atomic_current_record(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, saveVisualizationRevision }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            api.state.openSeq = 3;
            const spec = {{
              schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', panes: [],
            }};
            const request = {{
              backtestId: 'bt', visualizationId: 'current', name: 'current',
              expectedRevision: 4, spec,
            }};
            const current = {{
              visualizationId: 'current', backtestId: 'bt', name: 'current',
              createdAt: '2026-08-26T20:00:00Z', revision: 7, spec,
            }};
            let payload = {{
              accepted: false, code: 'visualization_revision_conflict',
              error: 'revision conflict', visualization: current,
            }};
            let postCalls = 0;
            let getCalls = 0;
            context.getJson = async () => {{ getCalls += 1; throw new Error('GET must not run'); }};
            context.postJson = async () => {{
              postCalls += 1;
              const error = new Error(payload.error);
              error.status = 409;
              error.payload = structuredClone(payload);
              throw error;
            }};

            (async () => {{
              let conflict;
              try {{
                await api.saveVisualizationRevision(structuredClone(request), 3);
              }} catch (error) {{ conflict = error; }}
              assert.equal(conflict.code, 'visualization_revision_conflict');
              assert.deepEqual(conflict.currentVisualization, current);
              assert.equal(postCalls, 1);
              assert.equal(getCalls, 0);

              payload.visualization = {{ ...current, backtestId: 'other' }};
              let malformed;
              try {{
                await api.saveVisualizationRevision(structuredClone(request), 3);
              }} catch (error) {{ malformed = error; }}
              assert.equal(malformed.code, 'visualization_revision_conflict');
              assert.equal(malformed.currentVisualization, null);

              payload.visualization = {{ ...current, unexpected: true }};
              let extraField;
              try {{
                await api.saveVisualizationRevision(structuredClone(request), 3);
              }} catch (error) {{ extraField = error; }}
              assert.equal(extraField.currentVisualization, null);
              assert.equal(postCalls, 3);
              assert.equal(getCalls, 0, '409 evidence must never trigger an extra GET');
            }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_open_response_requires_exact_pipeline_content_digest(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ requireOpenResponse }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const basic = 'trade.basic-workflow';
            const digest = `sha256:${{'a'.repeat(64)}}`;
            const response = {{
              accepted: true,
              protocolId: basic,
              snapshotId: 'snapshot-1',
              instrument: {{
                instrumentId: 'US-AAPL', symbol: 'AAPL', name: 'Apple',
                exchange: 'NASDAQ', currency: 'USD', assetType: 'stock',
                availablePeriods: ['day'],
              }},
              barSnapshot: {{
                providerId: 'nasdaq-us-snapshot', asOf: '2026-08-25T20:00:00Z',
                period: 'day', firstTime: '2026-08-25T20:00:00Z',
                lastTime: '2026-08-25T20:00:00Z', contentDigest: digest,
                barCount: 1,
              }},
              materialization: {{
                dataset: {{ datasetId: 'dataset-1', datasetVersionId: digest, protocolId: basic }},
                pipeline: {{ pipelineId: 'pipe-1', version: '2', protocolId: basic, contentDigest: digest }},
                sampler: {{ samplerId: 'sampler-1', version: '1', protocolId: basic }},
                environment: {{ environmentId: 'environment-1', version: '1', protocolId: basic }},
                analysis: {{ analysisId: 'analysis-1', version: '1', protocolId: basic }},
                backtestRequest: {{}},
                visualizationSaveRequest: {{
                  backtestId: 'bt-1', visualizationId: 'bt-1-current', name: 'current',
                  expectedRevision: 0, spec: {{}},
                }},
              }},
              job: {{ jobId: 'job-1', backtestId: 'bt-1', status: 'queued' }},
              prepared: {{ requestDigest: digest, snapshotHash: digest }},
              cache: {{ materializationHit: false }},
            }};
            const expected = {{ snapshotId: 'snapshot-1', instrumentId: 'US-AAPL', period: 'day' }};
            assert.equal(
              context.__basicTest.requireOpenResponse(structuredClone(response), expected)
                .materialization.pipeline.contentDigest,
              digest,
            );
            const missing = structuredClone(response);
            delete missing.materialization.pipeline.contentDigest;
            assert.throws(
              () => context.__basicTest.requireOpenResponse(missing, expected),
              /Materialized Pipeline.contentDigest must be a non-empty canonical string/,
            );
            const malformed = structuredClone(response);
            malformed.materialization.pipeline.contentDigest = `sha256:${{'A'.repeat(64)}}`;
            assert.throws(
              () => context.__basicTest.requireOpenResponse(malformed, expected),
              /Materialized Pipeline.contentDigest must be a canonical sha256 digest/,
            );
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_initial_candles_save_fails_closed_without_basic_definition(self):
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(ROOT / 'web' / 'basic_workflow_workspace.js'))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__basicTest = {{ state, requireBasicCandlePreset }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__basicTest;
            const spec = {{ panes: [{{ visualizers: [{{
              id: 'market-candles', callback: 'ohlc.candles',
              params: {{ dataKey: 'price.day.US-AAPL' }},
            }}] }}] }};
            api.state.catalog = {{ visualizers: [] }};
            assert.throws(
              () => api.requireBasicCandlePreset(spec, 'US-AAPL', 'day'),
              /no unique installed Visualizer definition/,
            );
            api.state.catalog.visualizers = [{{
              id: 'ohlc.candles', protocolId: 'vendor.other',
            }}];
            assert.throws(
              () => api.requireBasicCandlePreset(spec, 'US-AAPL', 'day'),
              /outside trade.basic-workflow/,
            );
            api.state.catalog.visualizers = [{{
              id: 'ohlc.candles', protocolId: 'trade.basic-workflow',
            }}];
            assert.equal(
              api.requireBasicCandlePreset(spec, 'US-AAPL', 'day').item.id,
              'market-candles',
            );
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
