import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { FileManager } from "@cubone/react-file-manager";

const roots = new WeakMap();
const MODULE_REPOSITORIES = new Set(["modules", "analysis-modules", "environment-modules"]);
const isModuleRepository = (repository) => MODULE_REPOSITORIES.has(repository);

const RESOURCE_TYPE_PRESENTATIONS = {
  dataset: { label: "Dataset", icon: "▤", color: "#1769aa", background: "#e5f2ff" },
  sampler: { label: "Sampler", icon: "⌁", color: "#7a4aa8", background: "#f2eaff" },
  script: { label: "Script", icon: "</>", color: "#ad4d13", background: "#fff0e5" },
  submittedscript: { label: "Script", icon: "</>", color: "#ad4d13", background: "#fff0e5" },
  workspace: { label: "Workspace", icon: "▦", color: "#087b6b", background: "#e2f7f2" },
  environment: { label: "Environment", icon: "⚙", color: "#52616f", background: "#eaf0f5" },
  environmentmodule: { label: "Environment Module", icon: "⬡", color: "#7b5b13", background: "#fff4cf" },
  result: { label: "Result", icon: "↗", color: "#21743b", background: "#e5f6e9" },
  backtest: { label: "Backtest", icon: "▶", color: "#21743b", background: "#e5f6e9" },
  pipeline: { label: "Pipeline", icon: "⇢", color: "#3155a4", background: "#e8eeff" },
  module: { label: "Module", icon: "◇", color: "#5e55a7", background: "#eeebff" },
  visualizer: { label: "Visualizer", icon: "◫", color: "#087b6b", background: "#e2f7f2" },
};

const RESOURCE_COLORS = [
  ["#1769aa", "#e5f2ff"],
  ["#7a4aa8", "#f2eaff"],
  ["#ad4d13", "#fff0e5"],
  ["#087b6b", "#e2f7f2"],
  ["#9a3f58", "#fdebf0"],
  ["#52616f", "#eaf0f5"],
];

const RESOURCE_TYPES_BY_REPOSITORY = {
  data: ["Dataset", "Sampler", "Script", "Workspace"],
  backtest: ["Result"],
  visualizers: ["Visualizer"],
};

function resourceTypeKey(value) {
  return String(value || "resource").trim().toLocaleLowerCase().replace(/[^a-z0-9]+/g, "") || "resource";
}

function titleCaseResourceType(value) {
  const spaced = String(value || "Resource")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return spaced.replace(/\b\w/g, (character) => character.toLocaleUpperCase()) || "Resource";
}

function resourceTypeValue(record, repository = "") {
  if (record?.resourceType) return record.resourceType;
  if (record?.kind) return record.kind;
  const source = record?.sourceRepository || repository;
  const singular = String(source || "resource").replace(/s$/, "");
  return singular === "backtest" && record?.visualizable ? "Result" : singular;
}

function resourceTypePresentation(record, repository = "") {
  const value = resourceTypeValue(record, repository);
  const key = resourceTypeKey(value);
  const configured = RESOURCE_TYPE_PRESENTATIONS[key];
  if (configured) return { key, ...configured };
  let hash = 0;
  for (const character of key) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
  const [color, background] = RESOURCE_COLORS[Math.abs(hash) % RESOURCE_COLORS.length];
  const label = titleCaseResourceType(value);
  return { key, label, icon: label.slice(0, 2).toLocaleUpperCase(), color, background };
}

function normalizedSourceRepository(record, repository) {
  const explicit = String(record?.sourceRepository || record?.resourceRepository || "").trim().toLocaleLowerCase();
  const aliases = {
    dataset: "datasets",
    sampler: "samplers",
    script: "scripts",
    workspace: "workspaces",
    result: "backtests",
    backtest: "backtests",
    environment: "environments",
  };
  if (explicit) return aliases[explicit] || explicit;
  const type = resourceTypePresentation(record, repository).key;
  return aliases[type] || repository;
}

function resourceOpenCapability(repository, record) {
  if (repository === "visualizers") {
    return { enabled: false, visible: false, label: "Open", title: "Built-in Visualizers are inspected in place" };
  }
  if (isModuleRepository(repository)) {
    return record?.builtin
      ? { enabled: false, label: "Edit", title: "Built-in Modules are read-only" }
      : { enabled: true, label: "Edit", title: "Open an isolated Jupyter edit Workspace" };
  }
  const sourceRepository = normalizedSourceRepository(record, repository);
  if (sourceRepository === "samplers") {
    if (record?.builtin) {
      return { enabled: false, label: "Edit", title: "Built-in Samplers are read-only" };
    }
    if (!["row-map", "python-script"].includes(record?.type)) {
      return { enabled: false, label: "Edit", title: "This Sampler type cannot be edited" };
    }
    return { enabled: true, label: "Edit", title: "Open an isolated Jupyter edit Workspace" };
  }
  return { enabled: true, label: "Open", title: "" };
}

function callbackRecord(entry) {
  const record = entry?.tradeRecord || entry || {};
  return { ...record };
}

function safeResourceName(value) {
  return String(value || "resource").replace(/[\\/]/g, "∕");
}

function itemPath(folderPath, name) {
  const parent = folderPath === "/" ? "" : folderPath;
  return `${parent}/${name}`;
}

function parentPath(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  return parts.length > 1 ? `/${parts.slice(0, -1).join("/")}` : "";
}

function normalizedSearchText(value) {
  return String(value || "").normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

function fuzzySearchScore(query, value) {
  const needle = normalizedSearchText(query);
  const haystack = normalizedSearchText(value);
  if (!needle) return 0;
  const directIndex = haystack.indexOf(needle);
  if (directIndex >= 0) return 2000 - directIndex * 4 - Math.max(0, haystack.length - needle.length);
  let cursor = 0;
  let score = 0;
  let previous = -1;
  for (const character of needle) {
    const index = haystack.indexOf(character, cursor);
    if (index < 0) return -1;
    score += previous < 0 ? Math.max(0, 80 - index) : Math.max(1, 24 - (index - previous - 1) * 3);
    previous = index;
    cursor = index + 1;
  }
  return score - Math.max(0, haystack.length - needle.length);
}

function resourceSearchValue(item, repository) {
  return [
    item.label,
    item.itemId,
    item.folderPath,
    item.resourceType,
    item.kind,
    item.moduleId,
    item.datasetId,
    item.workspaceId,
    item.id,
    resourceTypePresentation(item, repository).label,
  ].filter(Boolean).join(" ");
}

function resourceSearchTree(catalog) {
  const root = { name: "/", path: "/", children: new Map(), items: [] };
  const ensurePath = (folderPath) => {
    let node = root;
    let path = "";
    String(folderPath || "/").split("/").filter(Boolean).forEach((segment) => {
      path += `/${segment}`;
      if (!node.children.has(segment)) {
        node.children.set(segment, { name: segment, path, children: new Map(), items: [] });
      }
      node = node.children.get(segment);
    });
    return node;
  };
  (catalog?.folders || []).forEach((folder) => ensurePath(folder.path));
  (catalog?.items || []).forEach((item) => ensurePath(item.folderPath).items.push(item));
  return root;
}

function ResourceSearchCombobox({ repository, catalog, query, onQueryChange, onSelect }) {
  const [open, setOpen] = useState(false);
  const hostRef = useRef(null);
  const normalizedQuery = normalizedSearchText(query);
  const matches = useMemo(() => (catalog?.items || [])
    .map((item) => ({ item, score: fuzzySearchScore(normalizedQuery, resourceSearchValue(item, repository)) }))
    .filter((match) => match.score >= 0)
    .sort((left, right) => right.score - left.score || String(left.item.label).localeCompare(String(right.item.label)))
    .slice(0, 80), [catalog, normalizedQuery, repository]);
  const tree = useMemo(() => resourceSearchTree(catalog), [catalog]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (!hostRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  const choose = (item) => {
    onQueryChange(item.label || item.itemId || "");
    setOpen(false);
    onSelect(item);
  };
  const renderItem = (item, depth = 0) => {
    const presentation = resourceTypePresentation(item, repository);
    return <button
      type="button"
      key={item.itemId}
      className="trade-resource-search-item"
      data-trade-item-id={item.itemId}
      style={{ "--hierarchy-depth": depth, "--trade-resource-color": presentation.color, "--trade-resource-background": presentation.background }}
      onClick={() => choose(item)}
    >
      <span className="trade-resource-search-icon" aria-hidden="true">{presentation.icon}</span>
      <span><strong>{item.label || item.itemId}</strong><small>{item.folderPath || "/"}{normalizedSourceRepository(item, repository) !== "datasets" && item.version ? ` · ${item.version}` : ""}</small></span>
      <em>{presentation.label}</em>
    </button>;
  };
  const renderNode = (node, depth = 0) => <details className="trade-resource-search-folder" key={node.path} open={depth === 0}>
    <summary style={{ "--hierarchy-depth": depth }}>{node.name}</summary>
    {node.items.slice().sort((left, right) => String(left.label).localeCompare(String(right.label))).map((item) => renderItem(item, depth + 1))}
    {[...node.children.values()].sort((left, right) => left.name.localeCompare(right.name)).map((child) => renderNode(child, depth + 1))}
  </details>;

  return <div className="trade-resource-search" ref={hostRef}>
    <div className="trade-resource-search-control" role="combobox" aria-expanded={open} aria-haspopup="tree">
      <span aria-hidden="true">⌕</span>
      <input
        value={query}
        placeholder="Search or browse resources"
        aria-label={`Search ${repository} resources`}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(event) => { onQueryChange(event.target.value); setOpen(true); }}
      />
      {query && <button type="button" className="trade-resource-search-clear" title="Clear search" onClick={() => { onQueryChange(""); setOpen(true); }}>×</button>}
      <button type="button" className="trade-resource-search-toggle" title="Browse folders" aria-label="Browse resource folders" onClick={() => setOpen((value) => !value)}>▾</button>
    </div>
    {open && <div className="trade-resource-search-menu" role="tree">
      {normalizedQuery
        ? (matches.length ? matches.map(({ item }) => renderItem(item)) : <div className="trade-resource-search-empty">No matching resources</div>)
        : <>{tree.items.map((item) => renderItem(item))}{[...tree.children.values()].sort((left, right) => left.name.localeCompare(right.name)).map((node) => renderNode(node))}</>}
    </div>}
  </div>;
}

function catalogFiles(catalog) {
  const folders = (catalog?.folders || []).map((folder) => ({
    name: folder.name,
    isDirectory: true,
    path: folder.path,
    updatedAt: folder.updatedAt || "",
    tradeKind: "folder",
    tradeFolderId: folder.folderId,
    tradeFixed: Boolean(folder.fixed),
    tradeRecord: folder,
  }));
  const names = new Set();
  const items = (catalog?.items || []).map((item) => {
    const parent = item.folderPath || "/";
    const base = safeResourceName(item.label || item.itemId);
    let name = base;
    if (names.has(`${parent}\0${name.toLocaleLowerCase()}`)) {
      name = `${base} · ${String(item.itemId).slice(-10)}`;
    }
    names.add(`${parent}\0${name.toLocaleLowerCase()}`);
    return {
      name,
      isDirectory: false,
      path: itemPath(parent, name),
      updatedAt: item.updatedAt || item.createdAt || item.completedAt || "",
      size: item.size || undefined,
      tradeKind: "item",
      tradeItemId: item.itemId,
      tradeRecord: item,
    };
  });
  return [...folders, ...items];
}

function resourceLayoutStorageKey(repository) {
  return `trade.resource-browser.layout.v1:${repository}`;
}

function storedResourceLayout(repository) {
  try {
    const stored = window.localStorage.getItem(resourceLayoutStorageKey(repository));
    return stored === "list" ? "list" : "grid";
  } catch {
    return "grid";
  }
}

function displayValue(value) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.map((entry) => entry?.alias || entry?.name || entry).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ResourceInspector({ repository, selection, busy, openingPath, onOpen, onAction, onFolderAction, readOnly }) {
  const [editingFolder, setEditingFolder] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = selection.length === 1 ? selection[0] : null;
  useEffect(() => {
    setEditingFolder(false);
    setConfirmDelete(false);
    setFolderName(selected?.tradeRecord?.name || selected?.name || "");
  }, [selected?.path]);
  if (!selection.length) {
    return <aside className="trade-resource-inspector"><div className="trade-resource-inspector-empty"><span>ⓘ</span><strong>Details</strong><p>Select a folder or resource to inspect it.</p></div></aside>;
  }
  if (!selected) {
    return <aside className="trade-resource-inspector"><h3>{selection.length} selected</h3><p className="muted">Drag the selection onto a folder to classify these resources together.</p></aside>;
  }
  const record = selected.tradeRecord || {};
  const isFolder = selected.isDirectory;
  const presentation = resourceTypePresentation(record, repository);
  const sourceRepository = normalizedSourceRepository(record, repository);
  const openCapability = resourceOpenCapability(repository, record);
  const visualizerRows = repository === "visualizers" && !isFolder
    ? [
        ["Input contract", Object.entries(record.inputPorts || {}).map(([name, port]) => `${name}: ${JSON.stringify(port?.schema || {})}`)],
        ["Configuration", (record.params || []).map((parameter) => `${parameter.label || parameter.name} (${parameter.type || "value"})${parameter.required ? " · required" : ""}${parameter.default !== undefined ? ` · default ${displayValue(parameter.default)}` : ""}`)],
      ]
    : [];
  const rows = isFolder
    ? [["Path", record.path || selected.path], ["Type", record.fixed ? "Fixed folder" : "Folder"]]
    : [
        ["Type", presentation.label],
        ["Source", sourceRepository],
        ["ID", record.itemId],
        ["Folder", record.folderPath || "/"],
        ["Status", record.status],
        ...(sourceRepository === "datasets" ? [] : [["Version", record.version || record.versionId || record.latestVersionId]]),
        ["Created", record.createdAt || record.completedAt],
        ...visualizerRows,
      ].filter(([, value]) => value != null && value !== "");
  const title = record.label || record.name || selected.name;
  return (
    <aside className="trade-resource-inspector">
      <div className="trade-resource-inspector-heading">
        <span
          className={isFolder ? "folder-glyph" : "resource-glyph"}
          style={isFolder ? undefined : { "--trade-resource-color": presentation.color, "--trade-resource-background": presentation.background }}
        >{isFolder ? "▰" : presentation.icon}</span>
        <div><h3>{title}</h3><span>{isFolder ? "Folder" : presentation.label}</span></div>
      </div>
      <dl>{rows.map(([label, value]) => <React.Fragment key={label}><dt>{label}</dt><dd title={displayValue(value)}>{displayValue(value)}</dd></React.Fragment>)}</dl>
      <div className="trade-resource-inspector-actions">
        {isFolder && !readOnly && !record.fixed && !editingFolder && <button type="button" disabled={busy} onClick={() => setEditingFolder(true)}>Rename Folder</button>}
        {isFolder && !readOnly && !record.fixed && editingFolder && <form className="trade-resource-folder-form" onSubmit={async (event) => { event.preventDefault(); await onFolderAction("rename", selected, folderName); setEditingFolder(false); }}><input value={folderName} maxLength={80} autoFocus onChange={(event) => setFolderName(event.target.value)} /><div><button type="button" onClick={() => setEditingFolder(false)}>Cancel</button><button type="submit" disabled={busy || !folderName.trim()}>Apply</button></div></form>}
        {isFolder && !readOnly && !record.fixed && <button className={confirmDelete ? "danger" : ""} type="button" disabled={busy} onClick={async () => { if (!confirmDelete) { setConfirmDelete(true); return; } await onFolderAction("delete", selected); }}>{confirmDelete ? "Confirm Delete Empty Folder" : "Delete Empty Folder"}</button>}
        {!isFolder && openCapability.visible !== false && <button
          type="button"
          data-resource-open-action={isModuleRepository(repository) ? "edit" : "open"}
          disabled={busy || !openCapability.enabled}
          title={openCapability.title}
          onClick={() => onOpen(selected)}
        >{openingPath === selected.path ? "Opening…" : openCapability.label}</button>}
        {!isFolder && sourceRepository === "datasets" && record.status !== "archived" && <button className="danger" type="button" disabled={busy} onClick={() => onAction("archive", selected)}>Archive + Downstream</button>}
        {!isFolder && ["backtests", "results"].includes(sourceRepository) && record.status !== "archived" && <button className="danger" type="button" disabled={busy} onClick={() => onAction("archive", selected)}>Archive</button>}
        {!isFolder && sourceRepository === "workspaces" && <button type="button" disabled={busy} onClick={() => onAction("jupyter", selected)}>Open Jupyter</button>}
        {!isFolder && sourceRepository === "scripts" && <button type="button" disabled={busy} onClick={() => onAction("use-script", selected)}>Use in Build</button>}
      </div>
    </aside>
  );
}

const RESOURCE_BROWSER_LABELS = {
  modules: "Modules",
  "analysis-modules": "Analysis Modules",
  "environment-modules": "Environment Modules",
  data: "Datasets",
  pipelines: "Pipelines",
  environments: "Environments",
  analyses: "Analyses",
  backtest: "Backtests",
  visualizers: "Visualizers",
};

function ResourceBrowserLoading({ repository }) {
  const label = RESOURCE_BROWSER_LABELS[repository] || "Resources";
  return (
    <div
      className="trade-resource-browser-shell trade-resource-loading-shell"
      data-repository={repository}
      data-loading="true"
      aria-busy="true"
    >
      <div className="trade-resource-browser-main trade-resource-loading-main">
        <div className="trade-resource-loading-toolbar" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="trade-resource-loading-status" role="status" aria-live="polite">
          <span className="trade-resource-spinner" aria-hidden="true" />
          <strong>Loading {label}…</strong>
          <small>Fetching folders and resources</small>
        </div>
        <div className="trade-resource-loading-grid" aria-hidden="true">
          {Array.from({ length: 8 }, (_, index) => <span key={index} />)}
        </div>
      </div>
      <aside className="trade-resource-browser-sidebar trade-resource-loading-sidebar" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </aside>
    </div>
  );
}

function TradeResourceBrowser(props) {
  const { repository, catalog, onMutation, onOpen, onResourceAction, onRefresh, readOnly = false } = props;
  const [selection, setSelection] = useState([]);
  const selectionRef = useRef([]);
  const [busy, setBusy] = useState(false);
  const [openingPath, setOpeningPath] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [currentPath, setCurrentPath] = useState(props.initialPath || "");
  const [contextMenu, setContextMenu] = useState(null);
  const [resourceTypeFilter, setResourceTypeFilter] = useState("");
  const [resourceQuery, setResourceQuery] = useState("");
  const [browserEpoch, setBrowserEpoch] = useState(0);
  const [layout, setLayout] = useState(() => storedResourceLayout(repository));
  const shellRef = useRef(null);
  const activeDropTargetRef = useRef(null);
  const dragEntriesRef = useRef([]);
  const resourceTypes = useMemo(() => {
    const types = new Map();
    (RESOURCE_TYPES_BY_REPOSITORY[repository] || []).forEach((resourceType) => {
      const presentation = resourceTypePresentation({ resourceType }, repository);
      types.set(presentation.key, { ...presentation, count: 0 });
    });
    (catalog?.items || []).forEach((item) => {
      if (!item.resourceType) return;
      const presentation = resourceTypePresentation(item, repository);
      const current = types.get(presentation.key);
      types.set(presentation.key, { ...presentation, count: (current?.count || 0) + 1 });
    });
    return [...types.values()].sort((left, right) => left.label.localeCompare(right.label));
  }, [catalog, repository]);
  const showResourceTypeFilter = resourceTypes.length > 0;
  const filteredCatalog = useMemo(() => ({
    ...catalog,
    // Folders intentionally remain untouched: filtering resources must not
    // alter navigation or valid drag-and-drop destinations.
    items: (catalog?.items || []).filter((item) => (
      (!resourceTypeFilter || resourceTypePresentation(item, repository).key === resourceTypeFilter)
      && (!resourceQuery || fuzzySearchScore(resourceQuery, resourceSearchValue(item, repository)) >= 0)
    )),
  }), [catalog, repository, resourceTypeFilter, resourceQuery]);
  const files = useMemo(() => catalogFiles(filteredCatalog), [filteredCatalog]);
  const foldersByPath = useMemo(() => new Map((catalog?.folders || []).map((folder) => [folder.path, folder])), [catalog]);

  useEffect(() => {
    setSelection((previous) => previous.filter((selected) => files.some((file) => file.path === selected.path)));
  }, [files, currentPath, repository]);

  useEffect(() => {
    if (resourceTypeFilter && !resourceTypes.some((type) => type.key === resourceTypeFilter)) {
      setResourceTypeFilter("");
    }
  }, [resourceTypeFilter, resourceTypes]);

  useEffect(() => {
    selectionRef.current = selection;
  }, [selection]);

  useEffect(() => {
    setLayout(storedResourceLayout(repository));
  }, [repository]);

  useEffect(() => {
    if (!contextMenu) return undefined;
    const close = () => setContextMenu(null);
    window.addEventListener("pointerdown", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("blur", close);
    };
  }, [contextMenu]);

  useEffect(() => {
    const decorateBrowser = () => {
      const shell = shellRef.current;
      if (!shell) return;
      shell.querySelectorAll("[data-trade-drop-path]").forEach((node) => {
        if (!node.classList.contains("trade-resource-parent-folder")) delete node.dataset.tradeDropPath;
      });
      shell.querySelectorAll(".sb-folders-list-item").forEach((row) => {
        const names = [row.querySelector(":scope > .sb-folder-details .sb-folder-name")?.textContent.trim()].filter(Boolean);
        let cursor = row;
        while (cursor) {
          const collapsible = cursor.parentElement?.closest(".folder-collapsible");
          const parentRow = collapsible?.parentElement?.previousElementSibling;
          if (!parentRow?.classList.contains("sb-folders-list-item")) break;
          const name = parentRow.querySelector(":scope > .sb-folder-details .sb-folder-name")?.textContent.trim();
          if (name) names.unshift(name);
          cursor = parentRow;
        }
        const path = `/${names.join("/")}`;
        if (foldersByPath.has(path)) row.dataset.tradeDropPath = path;
      });
      const ancestors = [{ name: "Home", path: "" }];
      let path = "";
      String(currentPath || "").split("/").filter(Boolean).forEach((name) => {
        path += `/${name}`;
        ancestors.push({ name, path });
      });
      let ancestorIndex = 0;
      shell.querySelectorAll(".breadcrumb .folder-name:not(button)").forEach((crumb) => {
        if (crumb.closest(".nav-toggler")) return;
        const label = crumb.textContent.trim();
        const matchIndex = ancestors.findIndex((entry, index) => index >= ancestorIndex && entry.name === label);
        if (matchIndex < 0) return;
        crumb.dataset.tradeDropPath = ancestors[matchIndex].path;
        ancestorIndex = matchIndex + 1;
      });
      shell.querySelectorAll(".file-item-container").forEach((card) => {
        const item = card.querySelector(".file-item");
        const statusCell = card.querySelector(":scope > .size");
        const cardPath = itemPath(currentPath || "/", card.getAttribute("title"));
        const entry = files.find((candidate) => candidate.path === cardPath);
        if (!entry) {
          delete card.dataset.resourceType;
          delete card.dataset.resourceLabel;
          delete card.dataset.resourceStatus;
          if (item) delete item.dataset.resourceLabel;
          if (statusCell) delete statusCell.dataset.resourceStatus;
          card.querySelector(".trade-resource-type-icon")?.remove();
          card.querySelector(".trade-resource-type-badge")?.remove();
          return;
        }
        card.dataset.resourceLabel = entry.isDirectory ? "Folder" : resourceTypePresentation(entry.tradeRecord, repository).label;
        card.dataset.resourceStatus = entry.isDirectory ? "—" : (entry.tradeRecord?.status || "active");
        if (item) item.dataset.resourceLabel = card.dataset.resourceLabel;
        if (statusCell) statusCell.dataset.resourceStatus = card.dataset.resourceStatus;
        if (entry.isDirectory) {
          delete card.dataset.resourceType;
          delete card.dataset.tradeItemId;
          return;
        }
        const presentation = resourceTypePresentation(entry.tradeRecord, repository);
        card.dataset.tradeItemId = entry.tradeItemId;
        card.dataset.resourceType = presentation.key;
        card.dataset.resourceStatus = entry.tradeRecord?.status || "active";
        card.style.setProperty("--trade-resource-color", presentation.color);
        card.style.setProperty("--trade-resource-background", presentation.background);
        const iconHost = card.querySelector(".file-icon") || item;
        if (iconHost && !iconHost.querySelector(":scope > .trade-resource-type-icon")) {
          const icon = document.createElement("span");
          icon.className = "trade-resource-type-icon";
          icon.textContent = presentation.icon;
          icon.setAttribute("aria-hidden", "true");
          const nativeIcon = iconHost.querySelector(":scope > svg");
          iconHost.insertBefore(icon, nativeIcon || iconHost.firstChild);
        } else if (iconHost) {
          const icon = iconHost.querySelector(":scope > .trade-resource-type-icon");
          if (icon.textContent !== presentation.icon) icon.textContent = presentation.icon;
        }
        if (item && !item.querySelector(".trade-resource-type-badge")) {
          const badge = document.createElement("span");
          badge.className = "trade-resource-type-badge";
          const badgeLabel = isModuleRepository(repository) && entry.tradeRecord?.version
            ? `${presentation.label} · ${entry.tradeRecord.version}`
            : presentation.label;
          if (badge.textContent !== badgeLabel) badge.textContent = badgeLabel;
          badge.title = presentation.label;
          item.appendChild(badge);
        } else if (item) {
          const badge = item.querySelector(".trade-resource-type-badge");
          badge.textContent = isModuleRepository(repository) && entry.tradeRecord?.version
            ? `${presentation.label} · ${entry.tradeRecord.version}`
            : presentation.label;
          badge.title = presentation.label;
        }
      });
    };
    const frame = requestAnimationFrame(decorateBrowser);
    const timer = window.setTimeout(decorateBrowser, 120);
    const observer = new MutationObserver(() => requestAnimationFrame(decorateBrowser));
    if (shellRef.current) observer.observe(shellRef.current, { childList: true, subtree: true });
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [browserEpoch, currentPath, files, foldersByPath, layout, repository]);

  function handleLayoutChange(nextLayout) {
    const normalized = nextLayout === "list" ? "list" : "grid";
    setLayout(normalized);
    try {
      window.localStorage.setItem(resourceLayoutStorageKey(repository), normalized);
    } catch {
      // Browsing still works when storage is disabled; only persistence is lost.
    }
    props.onLayoutChange?.(normalized);
  }

  async function mutate(action, payload) {
    setBusy(true);
    setError("");
    try {
      await onMutation(action, payload);
      setSelection([]);
      return true;
    } catch (cause) {
      setError(cause?.message || String(cause));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function handleRefresh() {
    if (busy || !onRefresh) return;
    setBusy(true);
    setRefreshing(true);
    setError("");
    try {
      await onRefresh();
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setRefreshing(false);
      setBusy(false);
    }
  }

  function destinationId(folder) {
    return folder?.tradeFolderId || foldersByPath.get(folder?.path || currentPath)?.folderId || "";
  }

  async function handlePaste(entries, destination, operation) {
    if (operation !== "move") return;
    for (const entry of entries) {
      if (entry.isDirectory) {
        if (!await mutate("moveFolder", { folderId: entry.tradeFolderId, parentId: destinationId(destination) })) break;
      } else {
        if (!await mutate("moveItem", { itemId: entry.tradeItemId, folderId: destinationId(destination) })) break;
      }
    }
  }

  async function moveEntriesToPath(entries, path) {
    const destination = path ? foldersByPath.get(path) : null;
    if (path && !destination) throw new Error(`Unknown destination folder '${path}'.`);
    setBusy(true);
    setError("");
    try {
      for (const entry of entries) {
        if (entry.isDirectory) {
          if (entry.tradeFolderId === destination?.folderId || entry.tradeRecord?.parentId === (destination?.folderId || "")) continue;
          await onMutation("moveFolder", { folderId: entry.tradeFolderId, parentId: destination?.folderId || "" });
        } else {
          if ((entry.tradeRecord?.folderId || "") === (destination?.folderId || "")) continue;
          await onMutation("moveItem", { itemId: entry.tradeItemId, folderId: destination?.folderId || "" });
        }
      }
      setSelection([]);
      selectionRef.current = [];
      setBrowserEpoch((value) => value + 1);
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  }

  function clearDropTarget() {
    activeDropTargetRef.current?.classList.remove("trade-resource-drop-target");
    activeDropTargetRef.current = null;
  }

  function handleResourceDragStart(event) {
    if (readOnly) return;
    const card = event.target.closest(".file-item-container");
    if (!card) return;
    const title = card.getAttribute("title");
    const source = files.find((entry) => entry.path === itemPath(currentPath || "/", title));
    if (!source) return;
    dragEntriesRef.current = selectionRef.current.some((entry) => entry.path === source.path)
      ? [...selectionRef.current]
      : [source];
  }

  function handleResourceDragEnd() {
    clearDropTarget();
    dragEntriesRef.current = [];
  }

  function navigationDropTarget(event) {
    return event.target.closest("[data-trade-drop-path]");
  }

  function handleNavigationDragOver(event) {
    if (readOnly) return;
    const target = navigationDropTarget(event);
    if (!target || !dragEntriesRef.current.length) return;
    event.preventDefault();
    event.stopPropagation();
    // Cubone marks its internal drag as copy-only even though onPaste resolves it
    // as a move. Matching that browser-level effect is required for drop to fire;
    // persistence below still uses moveItem/moveFolder exclusively.
    event.dataTransfer.dropEffect = "copy";
    if (activeDropTargetRef.current !== target) {
      clearDropTarget();
      activeDropTargetRef.current = target;
      target.classList.add("trade-resource-drop-target");
    }
  }

  function handleNavigationDrop(event) {
    if (readOnly) return;
    const target = navigationDropTarget(event);
    if (!target || !dragEntriesRef.current.length) return;
    event.preventDefault();
    event.stopPropagation();
    const entries = [...dragEntriesRef.current];
    const path = target.dataset.tradeDropPath || "";
    clearDropTarget();
    moveEntriesToPath(entries, path);
  }

  function openPath(path) {
    setSelection([]);
    selectionRef.current = [];
    setCurrentPath(path);
    props.onFolderChange?.(path);
    setBrowserEpoch((value) => value + 1);
  }

  async function handleCreate(name, parent) {
    await mutate("create", { name, parentId: destinationId(parent) });
  }

  async function handleRename(entry, name) {
    if (!entry.isDirectory) {
      setError("Repository resources are immutable; create a new version instead of renaming one.");
      return;
    }
    if (entry.tradeFixed) {
      setError("Fixed repository folders cannot be renamed.");
      return;
    }
    await mutate("rename", { folderId: entry.tradeFolderId, name });
  }

  async function handleDelete(entries) {
    if (entries.some((entry) => !entry.isDirectory)) {
      setError("Repository resources must be archived through their business workflow, not deleted from a folder.");
      return;
    }
    for (const entry of entries) {
      if (entry.tradeFixed) {
        setError("Fixed repository folders cannot be deleted.");
        return;
      }
      if (!await mutate("delete", { folderId: entry.tradeFolderId })) break;
    }
  }

  async function runResourceAction(action, entry) {
    setBusy(true);
    setError("");
    try {
      await onResourceAction(action, entry.tradeRecord);
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function openEntry(entry) {
    if (!entry || openingPath) return;
    const capability = resourceOpenCapability(repository, entry.tradeRecord || {});
    if (!capability.enabled) {
      setError(capability.title || "This resource cannot be opened.");
      return;
    }
    setOpeningPath(entry.path || entry.tradeItemId || "resource");
    setBusy(true);
    setError("");
    try {
      await onOpen({
        ...callbackRecord(entry),
        parentFolderId: foldersByPath.get(currentPath || "/")?.folderId || "",
      });
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setOpeningPath("");
      setBusy(false);
    }
  }

  function openResourceContextMenu(event) {
    const card = event.target.closest(".file-item-container");
    const clickedItemId = card?.dataset.tradeItemId || "";
    const clickedEntry = card
      ? files.find((candidate) => (
        clickedItemId ? candidate.tradeItemId === clickedItemId : candidate.path === itemPath(currentPath || "/", card.getAttribute("title"))
      ))
      : null;
    if (repository === "data") {
      event.preventDefault();
      event.stopPropagation();
      const selectedPaths = new Set(selectionRef.current.map((entry) => entry.path));
      let entries = files.filter((entry) => selectedPaths.has(entry.path) && !entry.isDirectory);
      if (clickedEntry && !clickedEntry.isDirectory && !selectedPaths.has(clickedEntry.path)) {
        entries = [clickedEntry];
        selectionRef.current = entries;
        setSelection(entries);
        props.onSelectionChange?.(entries.map(callbackRecord));
      }
      setContextMenu({
        x: event.clientX,
        y: event.clientY,
        entries,
        parentFolderId: clickedEntry?.isDirectory
          ? clickedEntry.tradeFolderId
          : (foldersByPath.get(currentPath || "/")?.folderId || ""),
      });
      return;
    }
    if (isModuleRepository(repository)) {
      if (!event.target.closest(".trade-resource-browser-main")) return;
      event.preventDefault();
      event.stopPropagation();
      const nextSelection = clickedEntry && !clickedEntry.isDirectory ? [clickedEntry] : [];
      selectionRef.current = nextSelection;
      setSelection(nextSelection);
      props.onSelectionChange?.(nextSelection.map(callbackRecord));
      setContextMenu({
        x: event.clientX,
        y: event.clientY,
        entry: clickedEntry?.isDirectory ? null : clickedEntry,
      });
      return;
    }
    if (repository === "backtest") {
      if (!clickedEntry || clickedEntry.isDirectory) return;
      event.preventDefault();
      event.stopPropagation();
      selectionRef.current = [clickedEntry];
      setSelection([clickedEntry]);
      props.onSelectionChange?.([callbackRecord(clickedEntry)]);
      setContextMenu({ x: event.clientX, y: event.clientY, entry: clickedEntry });
      return;
    }
    if (repository !== "pipelines") return;
    event.preventDefault();
    event.stopPropagation();
    const parentFolderId = clickedEntry?.isDirectory
      ? clickedEntry.tradeFolderId
      : (foldersByPath.get(currentPath || "/")?.folderId || "");
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      entry: clickedEntry?.isDirectory ? null : clickedEntry,
      parentFolderId,
    });
  }

  function handleResourceDoubleClick(event) {
    const card = event.target.closest(".file-item-container");
    if (!card) return;
    const itemId = card.dataset.tradeItemId || "";
    const entry = files.find((candidate) => (
      itemId ? candidate.tradeItemId === itemId : candidate.path === itemPath(currentPath || "/", card.getAttribute("title"))
    ));
    if (!entry || entry.isDirectory || resourceOpenCapability(repository, entry.tradeRecord || {}).visible === false) return;
    event.preventDefault();
    event.stopPropagation();
    openEntry(entry);
  }

  async function pipelineContextAction(action) {
    const entry = contextMenu?.entry || null;
    const parentFolderId = contextMenu?.parentFolderId || "";
    if (action === "open" && entry) {
      setContextMenu((current) => current ? { ...current, opening: true } : current);
      await openEntry(entry);
      setContextMenu(null);
      return;
    }
    setContextMenu(null);
    if (action === "add-folder") onResourceAction(action, { parentFolderId });
    else onResourceAction(action, entry?.tradeRecord || null);
  }

  async function moduleContextAction(action) {
    const entry = contextMenu?.entry || null;
    setContextMenu(null);
    if (action === "edit" && entry) {
      await openEntry(entry);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onResourceAction(action, entry?.tradeRecord || null);
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function downloadDatasetContextSelection() {
    const datasetIds = (contextMenu?.entries || [])
      .map((entry) => entry.tradeRecord?.datasetId)
      .filter(Boolean);
    if (!datasetIds.length) return;
    setContextMenu(null);
    setBusy(true);
    setError("");
    try {
      await onResourceAction("download", { sourceRepository: "datasets", datasetIds });
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function dataContextAction(action) {
    const entries = contextMenu?.entries || [];
    if (action === "open" && entries.length === 1) {
      setContextMenu(null);
      await openEntry(entries[0]);
      return;
    }
    const payload = {
      items: entries.map((entry) => entry.tradeRecord),
      parentFolderId: contextMenu?.parentFolderId || "",
    };
    setContextMenu(null);
    setBusy(true);
    setError("");
    try {
      await onResourceAction(action, payload);
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function backtestContextAction(action) {
    const entry = contextMenu?.entry;
    setContextMenu(null);
    if (!entry) return;
    if (action === "open") {
      await openEntry(entry);
      return;
    }
    await runResourceAction(action, entry);
  }

  function revealSearchResult(item) {
    setResourceTypeFilter("");
    setCurrentPath(item.folderPath === "/" ? "" : (item.folderPath || ""));
    setSelection([]);
    setBrowserEpoch((value) => value + 1);
  }

  return (
    <div
      ref={shellRef}
      className="trade-resource-browser-shell"
      data-repository={repository}
      data-layout={layout}
      onContextMenuCapture={openResourceContextMenu}
      onDoubleClickCapture={handleResourceDoubleClick}
      onDragStartCapture={handleResourceDragStart}
      onDragOverCapture={handleNavigationDragOver}
      onDropCapture={handleNavigationDrop}
      onDragEndCapture={handleResourceDragEnd}
    >
      {error && <div className="trade-resource-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError("")}>×</button></div>}
      <div className="trade-resource-browser-main" aria-busy={busy}>
        {showResourceTypeFilter && <div className="trade-resource-type-filter" role="group" aria-label="Filter resources by type">
          <span className="trade-resource-type-filter-label">Type</span>
          <button type="button" data-resource-type-filter="all" className={!resourceTypeFilter ? "active" : ""} aria-pressed={!resourceTypeFilter} onClick={() => setResourceTypeFilter("")}>All <small>{catalog?.items?.length || 0}</small></button>
          {resourceTypes.map((type) => <button
            type="button"
            key={type.key}
            data-resource-type-filter={type.key}
            className={resourceTypeFilter === type.key ? "active" : ""}
            aria-pressed={resourceTypeFilter === type.key}
            style={{ "--trade-resource-color": type.color, "--trade-resource-background": type.background }}
            onClick={() => setResourceTypeFilter(type.key)}
          ><span aria-hidden="true">{type.icon}</span>{type.label} <small>{type.count}</small></button>)}
        </div>}
        {currentPath && <button
          type="button"
          className="trade-resource-parent-folder"
          data-trade-drop-path={parentPath(currentPath)}
          title={`Open parent folder ${parentPath(currentPath) || "/"}; drop selected resources here to move them`}
          onClick={() => openPath(parentPath(currentPath))}
        >
          <span className="trade-resource-parent-icon">▰</span>
          <strong>..</strong>
          <small>Parent folder</small>
        </button>}
        <FileManager
          key={`${repository}:${browserEpoch}:${resourceTypeFilter}:${layout}`}
          files={files}
          initialPath={currentPath}
          onFolderChange={(path) => { setCurrentPath(path); props.onFolderChange?.(path); }}
          onSelectionChange={(entries) => {
            selectionRef.current = entries;
            setSelection(entries);
            props.onSelectionChange?.(entries.map(callbackRecord));
          }}
          onFileOpen={(entry) => { if (!entry.isDirectory) openEntry(entry); }}
          onCreateFolder={handleCreate}
          onRename={handleRename}
          onDelete={handleDelete}
          onPaste={handlePaste}
          onRefresh={handleRefresh}
          onError={(problem) => setError(problem?.message || String(problem))}
          enableFilePreview={false}
          layout={layout}
          onLayoutChange={handleLayoutChange}
          language="en-US"
          height={currentPath ? (showResourceTypeFilter ? "532px" : "578px") : (showResourceTypeFilter ? "574px" : "620px")}
          primaryColor="#2f7ee6"
          permissions={{
            create: !readOnly,
            upload: false,
            move: !readOnly,
            copy: false,
            rename: false,
            download: false,
            delete: false,
          }}
        />
        {busy && <div className="trade-resource-busy" role="status" aria-live="polite">
          <span className="trade-resource-spinner" aria-hidden="true" />
          <span>{openingPath ? "Opening resource…" : (refreshing ? "Refreshing resources…" : "Updating repository…")}</span>
        </div>}
      </div>
      <div className="trade-resource-browser-sidebar">
        <div className="trade-resource-search-row">
          <button
            type="button"
            className="trade-resource-layout-toggle"
            data-layout={layout}
            title={`Switch to ${layout === "grid" ? "list" : "grid"} view`}
            aria-label={`Switch to ${layout === "grid" ? "list" : "grid"} resource view; current view is ${layout}`}
            onClick={() => handleLayoutChange(layout === "grid" ? "list" : "grid")}
          ><span aria-hidden="true">{layout === "grid" ? "▦" : "☷"}</span></button>
          <ResourceSearchCombobox
            repository={repository}
            catalog={catalog}
            query={resourceQuery}
            onQueryChange={setResourceQuery}
            onSelect={revealSearchResult}
          />
        </div>
        <ResourceInspector
          repository={repository}
          selection={selection}
          busy={busy}
          openingPath={openingPath}
          onOpen={openEntry}
          onAction={runResourceAction}
          onFolderAction={(action, entry, name) => mutate(action, { folderId: entry.tradeFolderId, ...(action === "rename" ? { name } : {}) })}
          readOnly={readOnly}
        />
      </div>
      {repository === "pipelines" && contextMenu && <div className="trade-resource-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
        <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("add-pipeline")}>Add Pipeline</button>
        <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("add-folder")}>Add Folder</button>
        {contextMenu.entry && <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("open")}>{contextMenu.opening ? "Opening…" : "Open"}</button>}
        {contextMenu.entry && <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("clone-pipeline")}>Clone</button>}
        {contextMenu.entry && contextMenu.entry.tradeRecord?.status !== "inactive" && <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("rename")}>Rename…</button>}
        {contextMenu.entry && contextMenu.entry.tradeRecord?.status !== "inactive" && <button className="danger" type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("disable-pipeline")}>Disable</button>}
        <span className="trade-resource-context-separator" />
        <button type="button" disabled={contextMenu.opening} onClick={() => pipelineContextAction("toggle-inactive")}>{props.showInactive ? "Hide Inactive" : "Show Inactive"}</button>
      </div>}
      {isModuleRepository(repository) && contextMenu && <div
        className="trade-resource-context-menu trade-resource-module-context-menu"
        data-context-repository={repository}
        style={{ left: contextMenu.x, top: contextMenu.y }}
        onPointerDown={(event) => event.stopPropagation()}
      >
        {contextMenu.entry && <>
          <span className="trade-resource-context-label">Module Version</span>
          <button
            type="button"
            data-module-context-action="edit"
            disabled={contextMenu.entry.tradeRecord?.builtin}
            title={contextMenu.entry.tradeRecord?.builtin ? "Built-in Modules are read-only" : "Open an isolated Jupyter edit Workspace"}
            onClick={() => moduleContextAction("edit")}
          >Edit in Jupyter</button>
          <button
            type="button"
            data-module-context-action="publish"
            disabled={contextMenu.entry.tradeRecord?.builtin}
            title={contextMenu.entry.tradeRecord?.builtin ? "Built-in Modules are read-only" : "Publish the edited Jupyter Workspace through the common archive flow"}
            onClick={() => moduleContextAction("publish")}
          >Publish Workspace</button>
          <span className="trade-resource-context-separator" />
        </>}
        <span className="trade-resource-context-label">Repository</span>
        <button type="button" data-module-context-action="add" onClick={() => moduleContextAction("add")}>Add Module…</button>
      </div>}
      {repository === "data" && contextMenu && (() => {
        const entries = contextMenu.entries || [];
        const sources = entries.map((entry) => normalizedSourceRepository(entry.tradeRecord, repository));
        const allDatasets = entries.length > 0 && sources.every((source) => source === "datasets");
        const allActiveDatasets = allDatasets && entries.every((entry) => entry.tradeRecord?.status !== "archived");
        const allWorkspaces = entries.length > 0 && sources.every((source) => source === "workspaces");
        const samplerEntry = entries.length === 1 && sources[0] === "samplers" ? entries[0] : null;
        const samplerOpen = samplerEntry ? resourceOpenCapability(repository, samplerEntry.tradeRecord) : null;
        const archivable = entries.length > 0
          && sources.every((source) => source === "datasets")
          && entries.some((entry) => entry.tradeRecord?.status !== "archived");
        const scriptCount = sources.filter((source) => source === "scripts").length;
        const processable = scriptCount === 1
          && sources.every((source) => ["datasets", "scripts"].includes(source))
          && entries.every((entry) => entry.tradeRecord?.status !== "archived");
        const renameable = entries.length === 1 && ["datasets", "workspaces"].includes(sources[0]);
        return <div className="trade-resource-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
          <span className="trade-resource-context-label">Create</span>
          <button type="button" onClick={() => dataContextAction("add-dataset")}>Add Dataset…</button>
          <button type="button" onClick={() => dataContextAction("add-script")}>Add Script…</button>
          {allActiveDatasets && <button type="button" onClick={() => dataContextAction("add-workspace")}>Add Workspace…</button>}
          {processable && <button type="button" onClick={() => dataContextAction("process")}>Process…</button>}
          {entries.length > 0 && <span className="trade-resource-context-separator" />}
          {samplerEntry && <button
            type="button"
            data-sampler-context-action="edit"
            disabled={!samplerOpen.enabled}
            title={samplerOpen.title}
            onClick={() => dataContextAction("open")}
          >Edit in Jupyter</button>}
          {samplerEntry && <button
            type="button"
            data-sampler-context-action="publish"
            disabled={!samplerOpen.enabled}
            title={samplerOpen.enabled ? "Publish the edited Jupyter Workspace as an immutable Version" : samplerOpen.title}
            onClick={() => dataContextAction("publish")}
          >Publish Workspace</button>}
          {allDatasets && <button type="button" onClick={downloadDatasetContextSelection}>{entries.length === 1 ? "Download Dataset" : `Download ${entries.length} Datasets`}</button>}
          {entries.length === 1 && allActiveDatasets && <button type="button" onClick={() => dataContextAction("replace")}>Replace…</button>}
          {renameable && <button type="button" onClick={() => dataContextAction("rename")}>Rename…</button>}
          {archivable && <button className="danger" type="button" onClick={() => dataContextAction("archive")}>Archive</button>}
          {allWorkspaces && <button className="danger" type="button" onClick={() => dataContextAction("delete")}>Delete</button>}
        </div>;
      })()}
      {repository === "backtest" && contextMenu?.entry && <div className="trade-resource-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
        {["backtests", "results"].includes(normalizedSourceRepository(contextMenu.entry.tradeRecord, repository)) && <button type="button" onClick={() => backtestContextAction("rename")}>Rename…</button>}
      </div>}
    </div>
  );
}

window.TradeResourceBrowser = {
  mount(element, props) {
    let root = roots.get(element);
    if (!root) {
      root = createRoot(element);
      roots.set(element, root);
    }
    root.render(props.loading ? <ResourceBrowserLoading {...props} /> : <TradeResourceBrowser {...props} />);
  },
  unmount(element) {
    const root = roots.get(element);
    if (root) root.unmount();
    roots.delete(element);
  },
};
