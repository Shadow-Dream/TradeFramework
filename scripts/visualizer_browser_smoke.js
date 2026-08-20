const fs = require("node:fs")

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const html = fs.readFileSync("web/index.html", "utf8")
const app = fs.readFileSync("web/app.js", "utf8")
const browser = fs.readFileSync("web_src/trade_resource_browser.jsx", "utf8")
const styles = fs.readFileSync("web/styles.css", "utf8")

assert(html.includes('id="visualizerRepositoryBrowser"'), "Visualizer must mount the shared Resource Browser")
assert(!html.includes("visualizerBrowserList") && !html.includes("visualizerBrowserDetails"), "Legacy Visualizer browser DOM remains")
assert(html.indexOf('data-view="mining-kline"') < html.indexOf('id="agentNavLink"'), "Agent navigation must follow Mining")
assert(app.includes('visualizers: "visualizerRepositoryBrowser"'), "Visualizer repository is absent from the common browser registry")
assert(app.includes('readOnly: repository === "visualizers"'), "Visualizer browser is not explicitly read-only")
assert(!app.includes("function renderVisualizerBrowser"), "Legacy Visualizer renderer remains")
assert(browser.includes('visualizers: ["Visualizer"]'), "Shared Resource Browser lacks the Visualizer resource type")
assert(browser.includes("create: !readOnly") && browser.includes("move: !readOnly"), "Read-only browser permissions are incomplete")
assert(browser.includes("function ResourceBrowserLoading") && browser.includes("props.loading ? <ResourceBrowserLoading"), "Shared Browser first-load animation is missing")
assert(browser.includes("onRefresh={handleRefresh}") && browser.includes("Refreshing resources…"), "Shared Browser refresh feedback is missing")
assert(app.includes("renderEmbeddedRepositoryLoading(scope)"), "Repository catalog loading does not mount the shared Browser skeleton")
assert(!styles.includes(".visualizer-browser-item") && !styles.includes(".visualizer-browser-layout"), "Legacy Visualizer styles remain")

console.log(JSON.stringify({ sharedBrowser: true, readOnly: true, agentAtBottom: true }))
