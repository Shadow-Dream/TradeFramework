import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from trade_agent_bridge.contracts import context_digest
from trade_agent_bridge.tool_api import TOOL_NAMES, ToolCallError, execute_tool
from trade_agent_bridge.tool_grants import TOOL_SCOPES, ToolGrantError, ToolGrantStore
from trade_agent_bridge.ui_tool_bridge import UiToolBridgeError, call_ui_tool


def empty_context():
    return {
        "schemaVersion": "1",
        "sourceView": "agent",
        "capturedAt": "2026-08-16T00:00:00Z",
        "references": [],
    }


class ToolGrantTests(unittest.TestCase):
    def test_grant_is_hashed_scoped_expiring_and_revocable(self):
        now = [1_000.0]
        context = empty_context()
        store = ToolGrantStore(ttl_seconds=30, clock=lambda: now[0])
        issued = store.create(
            owner_id="owner-1",
            chat_id="chat-1",
            turn_id="turn-1",
            context_digest=context_digest(context),
            context=context,
            scopes=["trade_context_get"],
        )
        raw = issued["grant"]
        self.assertNotIn(raw, repr(store._grants))
        self.assertIn(hashlib.sha256(raw.encode("ascii")).hexdigest(), store._grants)
        grant = store.authorize(raw, "trade_context_get")
        self.assertEqual(grant["ownerId"], "owner-1")
        with self.assertRaises(ToolGrantError):
            store.authorize(raw, "trade_validate")
        now[0] += 31
        with self.assertRaises(ToolGrantError):
            store.authorize(raw, "trade_context_get")

        issued = store.create(
            owner_id="owner-1",
            chat_id="chat-1",
            turn_id="turn-2",
            context_digest=context_digest(context),
            context=context,
            scopes=["trade_context_get"],
        )
        self.assertEqual(store.revoke_turn("turn-2"), 1)
        self.assertEqual(store.revoke_turn("turn-2"), 0)
        with self.assertRaises(ToolGrantError):
            store.authorize(issued["grant"], "trade_context_get")

    def test_default_grant_lifetime_covers_long_agent_turns(self):
        now = [1_000.0]
        context = empty_context()
        store = ToolGrantStore(clock=lambda: now[0])
        issued = store.create(
            owner_id="owner-1",
            chat_id="chat-1",
            turn_id="turn-long",
            context_digest=context_digest(context),
            context=context,
            scopes=["trade_context_get"],
        )
        self.assertEqual(issued["expiresAt"], now[0] + 21_600)
        now[0] += 21_599
        self.assertEqual(
            store.authorize(issued["grant"], "trade_context_get")["turnId"],
            "turn-long",
        )

    def test_allowlist_is_exactly_the_v1_tools(self):
        expected = {
            "trade_context_get",
            "trade_catalog_find",
            "trade_dataset_inspect",
            "trade_validate",
            "trade_backtest_get",
            "trade_result_query",
            "trade_proposal_create",
            "trade_ui_state_get",
            "trade_ui_document_get",
            "trade_ui_document_patch",
        }
        self.assertEqual(set(TOOL_NAMES), expected)
        self.assertEqual(set(TOOL_SCOPES), expected)


class ToolApiTests(unittest.TestCase):
    def test_context_get_is_read_only_and_exact(self):
        context = empty_context()
        result = execute_tool(
            None,
            {"context": context, "contextDigest": context_digest(context)},
            "trade_context_get",
            {},
        )
        self.assertEqual(result["context"], context)
        self.assertIs(result["mutationsAllowed"], False)
        with self.assertRaises(ToolCallError):
            execute_tool(None, {"context": context}, "submit_backtest", {})
        with self.assertRaises(ToolCallError):
            execute_tool(
                None,
                {"context": context, "contextDigest": context_digest(context)},
                "trade_context_get",
                {"unexpected": True},
            )

    def test_proposal_is_display_only_and_context_bound(self):
        reference = {"kind": "pipeline", "id": "pipeline-1", "version": "3"}
        context = {**empty_context(), "references": [reference]}
        grant = {"context": context, "contextDigest": context_digest(context)}
        artifact = {
            "schemaVersion": "1",
            "proposal": {
                "title": "Review a threshold adjustment",
                "summary": "Inspect this change before any separate save or run action.",
                "suggestedActions": ["Review the draft against the frozen Pipeline version."],
                "references": [reference],
            },
        }
        result = execute_tool(None, grant, "trade_proposal_create", {"artifact": artifact})
        self.assertEqual(result["artifact"], artifact)
        self.assertIs(result["displayOnly"], True)
        mismatched = json.loads(json.dumps(artifact))
        mismatched["proposal"]["references"][0]["version"] = "4"
        with self.assertRaises(ToolCallError) as raised:
            execute_tool(None, grant, "trade_proposal_create", {"artifact": mismatched})
        self.assertEqual(raised.exception.code, "reference_not_in_context")

    def test_ui_tools_are_exact_bounded_and_delegated(self):
        calls = []

        def invoke(name, arguments):
            calls.append((name, arguments))
            return {"ok": True}

        self.assertEqual(
            execute_tool(None, {}, "trade_ui_state_get", {}, ui_tool_call=invoke),
            {"ok": True},
        )
        self.assertEqual(calls[-1], ("trade_ui_state_get", {}))
        digest = "a" * 64
        result = execute_tool(None, {}, "trade_ui_document_patch", {
            "operationId": "operation-0001",
            "documentId": "jupyter:file.py",
            "baseRevision": 3,
            "baseDigest": digest,
            "patch": {"type": "replace", "start": 0, "end": 1, "text": "x"},
            "save": False,
        }, ui_tool_call=invoke)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[-1][1]["save"], False)
        for invalid in (
            {"operationId": "operation-0001", "documentId": "jupyter:file.py", "baseRevision": None, "baseDigest": digest, "patch": {"type": "replace", "start": 0, "end": 1, "text": "x"}, "save": False},
            {"operationId": "operation-0001", "documentId": "jupyter:file.py", "baseRevision": 3, "baseDigest": "bad", "patch": {"type": "replace", "start": 0, "end": 1, "text": "x"}, "save": False},
            {"operationId": "operation-0001", "documentId": "jupyter:file.py", "baseRevision": 3, "baseDigest": digest, "patch": {"type": "replace", "start": 2, "end": 1, "text": "x"}, "save": False},
        ):
            with self.assertRaises(ToolCallError):
                execute_tool(None, {}, "trade_ui_document_patch", invalid, ui_tool_call=invoke)


class UiToolBridgeTests(unittest.TestCase):
    def test_bridge_uses_exact_endpoint_without_proxy_and_parses_result(self):
        observed = {}

        class Response:
            status = 200

            def read(self, _limit):
                return b'{"result":{"serverSeq":4}}'

        class Opener:
            def open(self, request, timeout):
                observed["url"] = request.full_url
                observed["authorization"] = request.get_header("Authorization")
                observed["timeout"] = timeout
                observed["body"] = json.loads(request.data)
                return Response()

        result = call_ui_tool(
            "http://127.0.0.1:3210",
            "bridge-secret",
            "trade_ui_state_get",
            {},
            opener=Opener(),
        )
        self.assertEqual(result, {"serverSeq": 4})
        self.assertEqual(observed["url"], "http://127.0.0.1:3210/api/internal/ui-tools/call")
        self.assertEqual(observed["authorization"], "Bearer bridge-secret")
        self.assertEqual(observed["body"], {"tool": "trade_ui_state_get", "arguments": {}})

    def test_bridge_rejects_bad_origin_and_bad_response(self):
        with self.assertRaises(UiToolBridgeError):
            call_ui_tool("http://127.0.0.1:3210/path", "secret", "trade_ui_state_get", {})

        class Response:
            status = 200

            def read(self, _limit):
                return b'{"unexpected":true}'

        class Opener:
            def open(self, _request, timeout=None):
                return Response()

        with self.assertRaises(UiToolBridgeError) as raised:
            call_ui_tool("http://127.0.0.1:3210", "secret", "trade_ui_state_get", {}, opener=Opener())
        self.assertEqual(raised.exception.code, "invalid_tool_response")


class McpProtocolTests(unittest.TestCase):
    def test_stdio_server_lists_only_the_exact_tools(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            grant_file = os.path.join(directory, "turn.grant")
            with open(grant_file, "w", encoding="ascii") as handle:
                handle.write("a" * 43 + "\n")
            os.chmod(grant_file, 0o600)
            environment = os.environ.copy()
            environment.update({
                "PYTHONPATH": os.getcwd(),
                "TRADE_ENGINE_TOOL_GRANT_FILE": grant_file,
                "TRADE_ENGINE_TOOL_URL": "http://127.0.0.1:30809",
            })
            completed = subprocess.run(
                [sys.executable, "-m", "trade_agent_tools.mcp_server"],
                input="".join(json.dumps(item) + "\n" for item in messages),
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
                env=environment,
            )
            self.assertFalse(os.path.exists(grant_file))
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        listed = next(item for item in replies if item.get("id") == 2)
        self.assertEqual(
            {tool["name"] for tool in listed["result"]["tools"]},
            set(TOOL_SCOPES),
        )


if __name__ == "__main__":
    unittest.main()
