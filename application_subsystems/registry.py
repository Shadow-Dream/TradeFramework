"""Fail-closed registry for trusted application pages and HTTP adapters."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path

from engine.contracts.protocol import normalize_protocol_id


_SUBSYSTEM_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_PAGE_PATH = re.compile(r"^/[a-z0-9]+(?:[/-][a-z0-9]+)*$")
_API_PATH = re.compile(
    r"^/api/subsystems/[a-z][a-z0-9-]*(?:/[A-Za-z0-9_-]+)*$"
)
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9.-]+$")
_HTTP_METHODS = frozenset({"GET", "POST"})


@dataclass(frozen=True)
class SubsystemContext:
    """Engine-owned capabilities made available to one application adapter."""

    config: object
    prepared_store: object
    job_manager: object
    # Prepared submission tokens remain bound to this ephemeral login session.
    session_identity: str
    # Persistent application caches use this stable authenticated owner.
    owner_identity: str = ""


@dataclass(frozen=True)
class SubsystemEvent:
    event_type: str
    payload: dict


@dataclass(frozen=True)
class SubsystemResponse:
    status: int
    payload: dict
    events: tuple[SubsystemEvent, ...] = ()


@dataclass(frozen=True)
class SubsystemApiRoute:
    method: str
    path: str
    label: str
    handler: object


@dataclass(frozen=True)
class SubsystemPage:
    """One authenticated HTML entry owned by an application subsystem."""

    path: str
    file: str
    catalog_entry: bool = False


@dataclass(frozen=True)
class SubsystemDefinition:
    subsystem_id: str
    protocol_id: str
    label: str
    pages: tuple[SubsystemPage, ...]
    api_routes: tuple[SubsystemApiRoute, ...]


def _canonical_text(value, label):
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a canonical non-empty string.")
    return value


def _normalize_definition(definition):
    if type(definition) is not SubsystemDefinition:
        raise TypeError("Subsystem registry entries must be SubsystemDefinition values.")
    subsystem_id = _canonical_text(definition.subsystem_id, "Subsystem ID")
    if _SUBSYSTEM_ID.fullmatch(subsystem_id) is None:
        raise ValueError(f"Subsystem ID '{subsystem_id}' is invalid.")
    protocol_id = normalize_protocol_id(
        definition.protocol_id,
        label=f"Subsystem '{subsystem_id}' protocolId",
    )
    label = _canonical_text(definition.label, f"Subsystem '{subsystem_id}' label")
    if type(definition.pages) is not tuple or not definition.pages:
        raise ValueError(f"Subsystem '{subsystem_id}' must declare pages.")
    normalized_pages = []
    seen_page_paths = set()
    seen_page_files = set()
    for page in definition.pages:
        if type(page) is not SubsystemPage:
            raise TypeError(
                f"Subsystem '{subsystem_id}' pages must be SubsystemPage values."
            )
        page_path = _canonical_text(
            page.path,
            f"Subsystem '{subsystem_id}' page path",
        )
        if _PAGE_PATH.fullmatch(page_path) is None or page_path.startswith("/api/"):
            raise ValueError(f"Subsystem '{subsystem_id}' page path is invalid.")
        page_file = _canonical_text(
            page.file,
            f"Subsystem '{subsystem_id}' page file",
        )
        if (
            Path(page_file).name != page_file
            or Path(page_file).suffix.lower() != ".html"
        ):
            raise ValueError(f"Subsystem '{subsystem_id}' page file is invalid.")
        if type(page.catalog_entry) is not bool:
            raise TypeError(
                f"Subsystem '{subsystem_id}' page catalog_entry must be boolean."
            )
        if page_path in seen_page_paths:
            raise ValueError(f"Duplicate Subsystem page path: {page_path}")
        if page_file in seen_page_files:
            raise ValueError(f"Duplicate Subsystem page file: {page_file}")
        seen_page_paths.add(page_path)
        seen_page_files.add(page_file)
        normalized_pages.append(
            SubsystemPage(
                path=page_path,
                file=page_file,
                catalog_entry=page.catalog_entry,
            )
        )
    if sum(page.catalog_entry for page in normalized_pages) != 1:
        raise ValueError(
            f"Subsystem '{subsystem_id}' must declare exactly one catalog page."
        )
    if type(definition.api_routes) is not tuple or not definition.api_routes:
        raise ValueError(f"Subsystem '{subsystem_id}' must declare API routes.")

    api_prefix = f"/api/subsystems/{subsystem_id}"
    normalized_routes = []
    seen_routes = set()
    for route in definition.api_routes:
        if type(route) is not SubsystemApiRoute:
            raise TypeError(
                f"Subsystem '{subsystem_id}' API routes must be SubsystemApiRoute values."
            )
        method = _canonical_text(route.method, "Subsystem API method").upper()
        path = _canonical_text(route.path, "Subsystem API path")
        route_label = _canonical_text(route.label, "Subsystem API label")
        if method not in _HTTP_METHODS:
            raise ValueError(f"Subsystem API method '{method}' is unsupported.")
        if (
            _API_PATH.fullmatch(path) is None
            or not path.startswith(api_prefix + "/")
        ):
            raise ValueError(
                f"Subsystem '{subsystem_id}' API route '{path}' is outside its namespace."
            )
        if not callable(route.handler):
            raise TypeError(f"Subsystem API route '{method} {path}' has no handler.")
        key = (method, path)
        if key in seen_routes:
            raise ValueError(f"Duplicate Subsystem API route: {method} {path}")
        seen_routes.add(key)
        normalized_routes.append(
            SubsystemApiRoute(
                method=method,
                path=path,
                label=route_label,
                handler=route.handler,
            )
        )
    return SubsystemDefinition(
        subsystem_id=subsystem_id,
        protocol_id=protocol_id,
        label=label,
        pages=tuple(normalized_pages),
        api_routes=tuple(normalized_routes),
    )


def _normalize_response(response, route):
    if type(response) is not SubsystemResponse:
        raise TypeError(
            f"Subsystem handler '{route.method} {route.path}' returned an invalid response."
        )
    if (
        isinstance(response.status, bool)
        or not isinstance(response.status, int)
        or response.status < 200
        or response.status > 599
    ):
        raise ValueError(
            f"Subsystem handler '{route.method} {route.path}' returned an invalid status."
        )
    if type(response.payload) is not dict:
        raise TypeError(
            f"Subsystem handler '{route.method} {route.path}' payload must be an object."
        )
    if type(response.events) is not tuple:
        raise TypeError(
            f"Subsystem handler '{route.method} {route.path}' events must be a tuple."
        )
    events = []
    for event in response.events:
        if type(event) is not SubsystemEvent:
            raise TypeError("Subsystem events must be SubsystemEvent values.")
        if (
            type(event.event_type) is not str
            or _EVENT_TYPE.fullmatch(event.event_type) is None
            or type(event.payload) is not dict
        ):
            raise ValueError("Subsystem event is invalid.")
        events.append(
            SubsystemEvent(event.event_type, copy.deepcopy(event.payload))
        )
    return SubsystemResponse(
        status=response.status,
        payload=copy.deepcopy(response.payload),
        events=tuple(events),
    )


class SubsystemRegistry:
    """Exact trusted subsystem catalog with collision-free host projections."""

    def __init__(self, definitions):
        if type(definitions) not in {list, tuple}:
            raise TypeError("Subsystem definitions must be an array.")
        normalized = tuple(_normalize_definition(value) for value in definitions)
        by_id = {}
        pages = {}
        page_files = {}
        routes = {}
        for definition in normalized:
            if definition.subsystem_id in by_id:
                raise ValueError(
                    f"Duplicate Subsystem ID: {definition.subsystem_id}"
                )
            by_id[definition.subsystem_id] = definition
            for page in definition.pages:
                if page.path in pages:
                    raise ValueError(
                        f"Duplicate Subsystem page path: {page.path}"
                    )
                if page.file in page_files:
                    raise ValueError(
                        f"Duplicate Subsystem page file: {page.file}"
                    )
                pages[page.path] = page.file
                page_files[page.file] = definition.subsystem_id
            for route in definition.api_routes:
                key = (route.method, route.path)
                if key in routes:
                    raise ValueError(
                        f"Duplicate Subsystem API route: {route.method} {route.path}"
                    )
                routes[key] = route
        self._definitions = normalized
        self._pages = pages
        self._page_files = page_files
        self._routes = routes

    @property
    def page_paths(self):
        return frozenset(self._pages)

    @property
    def page_files(self):
        return frozenset(self._page_files)

    def validate_host(
        self,
        web_root,
        *,
        reserved_page_paths=(),
        reserved_page_files=(),
    ):
        path_conflicts = sorted(set(reserved_page_paths) & set(self._pages))
        if path_conflicts:
            raise ValueError(
                "Subsystem page path conflicts with the Trade Engine host: "
                + ", ".join(path_conflicts)
            )
        file_conflicts = sorted(set(reserved_page_files) & set(self._page_files))
        if file_conflicts:
            raise ValueError(
                "Subsystem page file conflicts with the Trade Engine host: "
                + ", ".join(file_conflicts)
            )
        web_root = Path(web_root).resolve()
        for page_file in self._page_files:
            candidate = web_root / page_file
            page = candidate.resolve()
            if (
                page.parent != web_root
                or not page.is_file()
                or candidate.is_symlink()
            ):
                raise ValueError(
                    f"Subsystem page file is unavailable or unsafe: {page_file}"
                )
        return self

    def catalog(self):
        return {
            "subsystems": [
                self._catalog_record(definition)
                for definition in self._definitions
            ]
        }

    @staticmethod
    def _catalog_record(definition):
        page = next(page for page in definition.pages if page.catalog_entry)
        return {
            "subsystemId": definition.subsystem_id,
            "protocolId": definition.protocol_id,
            "label": definition.label,
            "pagePath": page.path,
        }

    def page_file(self, path):
        return self._pages.get(path)

    def api_route(self, method, path):
        return self._routes.get((str(method or "").upper(), path))

    def invoke(self, route, context, payload=None):
        registered = (
            self._routes.get((route.method, route.path))
            if type(route) is SubsystemApiRoute
            else None
        )
        if registered is not route:
            raise ValueError("Subsystem API route is not registered.")
        if type(context) is not SubsystemContext:
            raise TypeError("Subsystem context must be Engine-owned.")
        return _normalize_response(route.handler(context, payload), route)


__all__ = (
    "SubsystemApiRoute",
    "SubsystemContext",
    "SubsystemDefinition",
    "SubsystemEvent",
    "SubsystemPage",
    "SubsystemRegistry",
    "SubsystemResponse",
)
