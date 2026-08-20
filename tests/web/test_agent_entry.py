#!/usr/bin/env python3
"""Static contracts for the separate Agent Web entry point."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class AgentEntryTests(unittest.TestCase):
    def test_engine_exposes_only_the_sidebar_agent_entry(self):
        self.assertIn('id="agentNavLink"', HTML_SOURCE)
        self.assertNotIn("agentUseCurrentContextBtn", HTML_SOURCE)
        self.assertNotIn("Add current selection to Agent", HTML_SOURCE)

    def test_agent_entry_is_the_last_sidebar_group(self):
        sidebar = HTML_SOURCE.split('<nav class="side"', 1)[1].split("</nav>", 1)[0]
        self.assertGreater(sidebar.index('>Agent</h2>'), sidebar.index('>Mining</h2>'))
        self.assertEqual(sidebar.count('class="side-nav-group side-nav-agent"'), 1)

    def test_removed_selection_handoff_has_no_frontend_state_or_request(self):
        for obsolete in (
            "explicitAgentContextSelection",
            "syncAgentContextCapture",
            "agentRepositorySelections",
            "agentRepositorySelectionSource",
            "agentExplicitPageSelections",
            "contextDraftFingerprint",
            "pipelineDraftFingerprint",
            'postJson("/api/agent-handoffs"',
        ):
            self.assertNotIn(obsolete, APP_SOURCE)

    def test_ui_sync_replaces_handoff_and_sse_contracts(self):
        service = (ROOT / "engine_service.py").read_text(encoding="utf-8")
        agent_server = (ROOT / "agent_web" / "src" / "server" / "server.ts").read_text(encoding="utf-8")
        self.assertNotIn("/api/agent-handoffs", service)
        self.assertNotIn("/api/events", service)
        self.assertNotIn("/api/trade-context/exchange", agent_server)
        self.assertIn("/ws/ui", agent_server)

    def test_agent_navigation_keeps_only_the_return_path(self):
        handler = APP_SOURCE.split(
            '$("agentNavLink")?.addEventListener("click",', 1
        )[1].split('$("cancelRepositoryFolderBtn")', 1)[0]
        self.assertIn("returnTo", handler)
        self.assertNotIn("handoff", handler)


if __name__ == "__main__":
    unittest.main()
