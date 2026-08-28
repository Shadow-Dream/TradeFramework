"""Host-neutral application subsystem registration boundaries."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from application_subsystems import build_builtin_subsystem_registry
from application_subsystems.registry import (
    SubsystemApiRoute,
    SubsystemContext,
    SubsystemDefinition,
    SubsystemPage,
    SubsystemRegistry,
    SubsystemResponse,
)


def _response(_context, _payload):
    return SubsystemResponse(200, {"accepted": True})


def _definition(
    subsystem_id,
    *,
    page_path=None,
    page_file=None,
    api_path=None,
    pages=None,
):
    return SubsystemDefinition(
        subsystem_id=subsystem_id,
        protocol_id=f"trade.{subsystem_id}",
        label=subsystem_id.title(),
        pages=pages or (
            SubsystemPage(
                page_path or f"/{subsystem_id}",
                page_file or f"{subsystem_id}.html",
                True,
            ),
        ),
        api_routes=(
            SubsystemApiRoute(
                "GET",
                api_path or f"/api/subsystems/{subsystem_id}/state",
                f"{subsystem_id} state",
                _response,
            ),
        ),
    )


class SubsystemRegistryTests(unittest.TestCase):
    def test_builtin_catalog_and_dispatch_are_host_neutral(self):
        registry = build_builtin_subsystem_registry()
        self.assertEqual(
            registry.catalog(),
            {
                "subsystems": [
                    {
                        "subsystemId": "basic",
                        "protocolId": "trade.basic-workflow",
                        "label": "Basic",
                        "pagePath": "/basic-workflow",
                    }
                ]
            },
        )
        route = registry.api_route("GET", "/api/subsystems/basic/market")
        self.assertIsNotNone(route)
        self.assertEqual(
            registry.page_paths,
            frozenset({"/basic-workflow", "/basic-workflow/workspace"}),
        )
        self.assertEqual(
            registry.page_file("/basic-workflow/workspace"),
            "basic_workflow_workspace.html",
        )

    def test_duplicate_identity_page_file_and_api_route_fail_closed(self):
        repeated_route = _definition("route")
        repeated_route = replace(
            repeated_route,
            api_routes=(
                repeated_route.api_routes[0],
                repeated_route.api_routes[0],
            ),
        )
        cases = (
            (
                (_definition("one"), _definition("one", page_path="/two", page_file="two.html")),
                "Duplicate Subsystem ID",
            ),
            (
                (_definition("one"), _definition("two", page_path="/one")),
                "Duplicate Subsystem page path",
            ),
            (
                (_definition("one"), _definition("two", page_file="one.html")),
                "Duplicate Subsystem page file",
            ),
            (
                (
                    _definition("one"),
                    _definition(
                        "two",
                        api_path="/api/subsystems/one/state",
                    ),
                ),
                "outside its namespace",
            ),
            ((repeated_route,), "Duplicate Subsystem API route"),
            (
                (
                    _definition(
                        "pages",
                        pages=(
                            SubsystemPage("/pages", "pages.html", True),
                            SubsystemPage("/pages", "workspace.html"),
                        ),
                    ),
                ),
                "Duplicate Subsystem page path",
            ),
        )
        for definitions, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                (ValueError, TypeError),
                message,
            ):
                SubsystemRegistry(definitions)

    def test_each_subsystem_requires_exactly_one_catalog_page(self):
        cases = (
            (
                SubsystemPage("/one", "one.html"),
                SubsystemPage("/one/workspace", "one_workspace.html"),
            ),
            (
                SubsystemPage("/one", "one.html", True),
                SubsystemPage("/one/workspace", "one_workspace.html", True),
            ),
        )
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaisesRegex(
                ValueError,
                "exactly one catalog page",
            ):
                SubsystemRegistry((_definition("one", pages=pages),))

    def test_host_page_conflicts_and_missing_static_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.html").write_text("<!doctype html>", encoding="utf-8")
            registry = SubsystemRegistry((_definition("one"),))
            with self.assertRaisesRegex(ValueError, "page path conflicts"):
                registry.validate_host(root, reserved_page_paths={"/one"})
            with self.assertRaisesRegex(ValueError, "page file conflicts"):
                registry.validate_host(root, reserved_page_files={"one.html"})
            registry.validate_host(root)
            (root / "one.html").unlink()
            with self.assertRaisesRegex(ValueError, "unavailable or unsafe"):
                registry.validate_host(root)

    def test_every_registered_page_is_checked_for_host_conflicts_and_static_safety(self):
        pages = (
            SubsystemPage("/one", "one.html", True),
            SubsystemPage("/one/workspace", "one_workspace.html"),
        )
        registry = SubsystemRegistry((_definition("one", pages=pages),))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.html").write_text("<!doctype html>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one_workspace.html"):
                registry.validate_host(root)

            workspace = root / "one_workspace.html"
            workspace.write_text("<!doctype html>", encoding="utf-8")
            registry.validate_host(root)
            with self.assertRaisesRegex(ValueError, "page path conflicts"):
                registry.validate_host(
                    root,
                    reserved_page_paths={"/one/workspace"},
                )
            with self.assertRaisesRegex(ValueError, "page file conflicts"):
                registry.validate_host(
                    root,
                    reserved_page_files={"one_workspace.html"},
                )

            target = root / "workspace_target.html"
            target.write_text("<!doctype html>", encoding="utf-8")
            workspace.unlink()
            workspace.symlink_to(target.name)
            with self.assertRaisesRegex(ValueError, "one_workspace.html"):
                registry.validate_host(root)

    def test_unregistered_route_cannot_be_invoked(self):
        registry = SubsystemRegistry((_definition("one"),))
        foreign = _definition("two").api_routes[0]
        with self.assertRaisesRegex(ValueError, "not registered"):
            registry.invoke(
                foreign,
                SubsystemContext({}, object(), object(), "session"),
            )


if __name__ == "__main__":
    unittest.main()
