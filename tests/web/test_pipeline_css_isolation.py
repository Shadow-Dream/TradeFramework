#!/usr/bin/env python3
"""Static boundaries between Pipeline Composer and the shared Module Graph UI."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
GRAPH_SOURCE = (ROOT / "web" / "module_graph_litegraph.js").read_text(
    encoding="utf-8"
)

PIPELINE_SCOPE = "body.route-pipeline-builder #pipelineComposerSection "
PIPELINE_SCOPE_PATTERN = re.compile(
    r"^body\.route-pipeline-builder(?:\.[A-Za-z0-9_-]+)*\s+"
    r"#pipelineComposerSection(?:\s|$)"
)
PIPELINE_LOCAL_CLASSES = (
    "flow-viewport",
    "flow-canvas",
    "flow-board",
    "graph-edges",
    "graph-edge",
    "graph-arrow",
    "component-group",
    "flow-node",
    "component-head",
    "component-head-actions",
    "pipeline-stage-container",
    "pipeline-stage-tag-list",
    "pipeline-stage-issues",
    "pipeline-stage-issue",
    "pipeline-single-module-card",
    "pipeline-single-module-head",
    "pipeline-single-module-copy",
    "pipeline-single-module-kind",
    "pipeline-single-module-name",
    "pipeline-single-module-configure",
    "pipeline-single-module-identity",
    "pipeline-single-stage-slot",
    "pipeline-single-stage-empty",
    "loaded-tags",
    "loaded-tags-scroll",
    "loaded-tag",
    "details-btn",
    "load-row",
)

REMOVED_STRUCTURED_ROW_CLASSES = (
    "pipeline-stage-module-list",
    "pipeline-stage-module-row",
    "pipeline-stage-module-copy",
    "pipeline-stage-module-state",
    "pipeline-stage-module-remove",
    "pipeline-stage-reference-issue",
    "pipeline-stage-empty",
    "pipeline-stage-summary",
    "pipeline-single-module-remove",
)

# These fingerprints are the approved shared Signal/Environment/Analysis graph
# implementation immediately before the Pipeline-only isolation repair. A later
# intentional graph redesign must update them in a graph-specific change.
APPROVED_GRAPH_RENDERER_SHA256 = (
    "9351e1296ec84533475724df6b007afbef6ad74ca964113ffbe2873eacefd639"
)
APPROVED_GRAPH_BASE_CSS_SHA256 = (
    "c60e903d70784e3d92bfa50ae3907100f1d2456a9e3d836ef022b132cc78c552"
)
APPROVED_GRAPH_ROUTE_CSS_SHA256 = (
    "996bc430bdd5ea4224c577c628cd4bc92befa66133e0321830a5330cce545fc1"
)


def _find_unquoted(source: str, needle: str, start: int) -> int:
    """Return the next unquoted character position, or -1."""

    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == needle:
            return index
    return -1


def _matching_brace(source: str, opening: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError("styles.css contains an unmatched opening brace")


def _split_selector_group(group: str) -> list[str]:
    selectors: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(group):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            selectors.append(group[start:index].strip())
            start = index + 1
    selectors.append(group[start:].strip())
    return [selector for selector in selectors if selector]


def css_rule_selectors(source: str) -> list[str]:
    """Extract selectors recursively from the small CSS subset used here."""

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    nested_at_rules = ("@media", "@supports", "@container", "@layer")

    def walk(block: str) -> list[str]:
        selectors: list[str] = []
        cursor = 0
        while True:
            opening = _find_unquoted(block, "{", cursor)
            if opening < 0:
                break
            prelude = block[cursor:opening].strip()
            closing = _matching_brace(block, opening)
            contents = block[opening + 1 : closing]
            if prelude.startswith(nested_at_rules):
                selectors.extend(walk(contents))
            elif prelude and not prelude.startswith("@"):
                selectors.extend(_split_selector_group(prelude))
            cursor = closing + 1
        return selectors

    return walk(source)


def class_token(selector: str, class_name: str) -> bool:
    return bool(
        re.search(
            rf"(?<![\w-])\.{re.escape(class_name)}(?![\w-])",
            selector,
        )
    )


def pipeline_scoped(selector: str) -> bool:
    """Accept route state classes without allowing an intervening container."""

    return bool(PIPELINE_SCOPE_PATTERN.search(selector))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PipelineCssIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selectors = css_rule_selectors(CSS_SOURCE)

    def test_generic_canvas_toolbar_selector_is_forbidden(self):
        offenders = [
            selector
            for selector in self.selectors
            if class_token(selector, "canvas-toolbar")
        ]
        self.assertEqual(
            offenders,
            [],
            "Use .pipeline-canvas-toolbar or .backtest-canvas-toolbar; "
            f"generic canvas toolbar rules leak across pages: {offenders}",
        )

    def test_pipeline_flow_card_module_and_edge_rules_are_route_scoped(self):
        offenders: list[str] = []
        for selector in self.selectors:
            if not any(
                class_token(selector, class_name)
                for class_name in PIPELINE_LOCAL_CLASSES
            ):
                continue
            if not pipeline_scoped(selector):
                offenders.append(selector)
        self.assertEqual(
            offenders,
            [],
            "Pipeline Composer selectors must remain under "
            f"'{PIPELINE_SCOPE.strip()}': {offenders}",
        )

    def test_pipeline_stage_modes_and_component_group_cannot_leak(self):
        for class_name in (
            "pipeline-stage-container",
            "pipeline-stage-tag-list",
            "pipeline-single-module-card",
            "pipeline-single-module-configure",
            "pipeline-single-stage-slot",
            "loaded-tags",
            "loaded-tag",
            "component-group",
        ):
            offenders = [
                selector
                for selector in self.selectors
                if class_token(selector, class_name)
                and not pipeline_scoped(selector)
            ]
            self.assertEqual(
                offenders,
                [],
                f"Unqualified .{class_name} rules change unrelated graph pages: "
                f"{offenders}",
            )

    def test_structured_pipeline_module_row_css_is_removed(self):
        for class_name in REMOVED_STRUCTURED_ROW_CLASSES:
            selectors = [
                selector
                for selector in self.selectors
                if class_token(selector, class_name)
            ]
            self.assertEqual(
                selectors,
                [],
                f"Structured Pipeline row CSS must not reappear: {selectors}",
            )

    def test_container_tag_contract_is_present_and_route_scoped(self):
        for class_name in (
            "pipeline-stage-container",
            "pipeline-stage-tag-list",
            "loaded-tags",
            "loaded-tags-scroll",
            "loaded-tag",
        ):
            selectors = [
                selector
                for selector in self.selectors
                if class_token(selector, class_name)
            ]
            self.assertTrue(selectors, f"Missing container .{class_name} Pipeline CSS")
            self.assertTrue(
                all(pipeline_scoped(selector) for selector in selectors),
                f"Container .{class_name} rules leaked outside Pipeline: {selectors}",
            )
            self.assertNotIn(f'"{class_name}"', GRAPH_SOURCE)
            self.assertNotIn(f'class="{class_name}', GRAPH_SOURCE)

    def test_pipeline_cards_keep_compact_width_and_use_content_height(self):
        flow_node = CSS_SOURCE.split(
            f"{PIPELINE_SCOPE}.flow-node {{", 1
        )[1].split("}", 1)[0]
        loaded_tags = CSS_SOURCE.split(
            f"{PIPELINE_SCOPE}.loaded-tags {{", 1
        )[1].split("}", 1)[0]
        loaded_tag = CSS_SOURCE.split(
            f"{PIPELINE_SCOPE}.loaded-tag {{", 1
        )[1].split("}", 1)[0]

        self.assertIn("width: 210px", flow_node)
        self.assertIn("height: auto", flow_node)
        self.assertIn("min-height: 0", flow_node)
        self.assertNotIn("188px", flow_node)
        self.assertNotIn("width: 240px", flow_node)
        self.assertNotIn("min-height: 250px", flow_node)
        self.assertIn("min-height: 0", loaded_tags)
        self.assertIn("display: flex", loaded_tags)
        self.assertIn("flex-wrap: wrap", loaded_tags)
        self.assertIn("gap: 8px", loaded_tags)
        self.assertIn("height: 28px", loaded_tag)

    def test_signal_and_constraint_tag_list_has_a_light_container_boundary(self):
        selectors = [
            selector
            for selector in self.selectors
            if class_token(selector, "pipeline-stage-container")
            and class_token(selector, "pipeline-stage-tag-list")
        ]
        self.assertTrue(
            selectors,
            "Signal/Constraint tag lists need an explicit container rule",
        )
        self.assertTrue(
            all(pipeline_scoped(selector) for selector in selectors),
            f"Stage-container boundary leaked outside Pipeline: {selectors}",
        )

        boundary_rule = re.search(
            r"body\.route-pipeline-builder\s+#pipelineComposerSection\s+"
            r"[^{}]*\.pipeline-stage-container[^{}]*"
            r"\.pipeline-stage-tag-list[^{}]*\{(?P<body>[^{}]*)\}",
            CSS_SOURCE,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(boundary_rule)
        declarations = boundary_rule.group("body")
        self.assertIn("border: 1px solid var(--line)", declarations)
        self.assertRegex(declarations, r"padding:\s*[1-9][0-9]*px")

    def test_pipeline_css_does_not_enter_the_shared_graph_renderer(self):
        for class_name in ("canvas-toolbar", *PIPELINE_LOCAL_CLASSES):
            self.assertNotIn(f'"{class_name}"', GRAPH_SOURCE)
            self.assertNotIn(f' class="{class_name}', GRAPH_SOURCE)
        self.assertNotIn("route-pipeline-builder", GRAPH_SOURCE)
        self.assertNotIn("pipelineComposerSection", GRAPH_SOURCE)

    def test_approved_module_graph_renderer_and_css_are_unchanged(self):
        self.assertEqual(
            hashlib.sha256(GRAPH_SOURCE.encode("utf-8")).hexdigest(),
            APPROVED_GRAPH_RENDERER_SHA256,
        )

        graph_base_marker = ".alpha-graph-builder {"
        graph_base_end = ".blueprint-raw {"
        graph_route_marker = (
            "/* Shared Signal, Environment, and Analysis graph workbench. */"
        )
        graph_route_end = ".account-menu {"
        for marker in (
            graph_base_marker,
            graph_base_end,
            graph_route_marker,
            graph_route_end,
        ):
            self.assertEqual(CSS_SOURCE.count(marker), 1)

        graph_base_css = graph_base_marker + CSS_SOURCE.split(
            graph_base_marker, 1
        )[1].split(graph_base_end, 1)[0]
        graph_route_css = graph_route_marker + CSS_SOURCE.split(
            graph_route_marker, 1
        )[1].split(graph_route_end, 1)[0]
        self.assertEqual(
            sha256_text(graph_base_css),
            APPROVED_GRAPH_BASE_CSS_SHA256,
        )
        self.assertEqual(
            sha256_text(graph_route_css),
            APPROVED_GRAPH_ROUTE_CSS_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
