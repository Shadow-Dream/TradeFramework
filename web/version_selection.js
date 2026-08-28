(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.TradeVersionSelection = api;
})(typeof window === "undefined" ? globalThis : window, function () {
  "use strict";

  function identityFor(row, identityFields) {
    return identityFields.map((field) => String(row?.[field] ?? "")).join("\u0000");
  }

  function numericVersion(row) {
    const value = Number(row?.version);
    return Number.isSafeInteger(value) && value > 0 ? value : Number.NEGATIVE_INFINITY;
  }

  function preferCandidate(current, candidate) {
    if (!current) return true;
    const currentIsSelected = current.current === true;
    const candidateIsSelected = candidate.current === true;
    if (currentIsSelected !== candidateIsSelected) return candidateIsSelected;
    const currentVersion = numericVersion(current);
    const candidateVersion = numericVersion(candidate);
    if (candidateVersion !== currentVersion) return candidateVersion > currentVersion;
    return String(candidate.createdAt || "") > String(current.createdAt || "");
  }

  function currentRows(rows, identityFields) {
    if (!Array.isArray(rows)) return [];
    if (!Array.isArray(identityFields) || !identityFields.length) {
      throw new Error("Current-version projection requires identity fields.");
    }
    const selected = new Map();
    rows.forEach((row) => {
      if (!row || typeof row !== "object") return;
      const identity = identityFor(row, identityFields);
      const existing = selected.get(identity);
      if (preferCandidate(existing, row)) selected.set(identity, row);
    });
    return [...selected.values()];
  }

  function currentEntries(records, identityFields) {
    return currentRows(
      Object.entries(records || {}).map(([key, value]) => ({ key, ...value })),
      identityFields,
    );
  }

  function projectCurrentCatalog(catalog) {
    if (!catalog || !Array.isArray(catalog.items)) return catalog;
    const repository = String(catalog.repository || "");
    let items = [...catalog.items];
    const retainCurrent = (predicate, identityFields) => {
      const candidates = items.filter(predicate);
      const selectedIds = new Set(currentRows(candidates, identityFields).map((item) => item.itemId));
      items = items.filter((item) => !predicate(item) || selectedIds.has(item.itemId));
    };

    if (repository === "data") {
      retainCurrent((item) => item.sourceRepository === "samplers", ["samplerId"]);
      retainCurrent((item) => item.sourceRepository === "scripts", ["recipeId"]);
    } else if (repository === "samplers") {
      retainCurrent(() => true, ["samplerId"]);
    } else if (repository === "scripts") {
      retainCurrent(() => true, ["recipeId"]);
    } else if (["modules", "analysis-modules", "environment-modules"].includes(repository)) {
      retainCurrent(() => true, ["kind", "moduleId"]);
    } else if (repository === "environments") {
      retainCurrent(() => true, ["environmentId"]);
    } else if (repository === "analyses") {
      retainCurrent(() => true, ["analysisId"]);
    }

    return { ...catalog, items, total: items.length };
  }

  return Object.freeze({ currentRows, currentEntries, projectCurrentCatalog });
});
