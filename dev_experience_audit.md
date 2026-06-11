# Strategy Development Experience Audit

Date: 2026-05-26

This audit checks the current framework against the working principle: strategy authors should focus on strategy logic, not Engine internals, repeated control-plane wiring, or hidden deployment steps.

## Summary

The latest devkit path is now usable for real strategy-side development:

```bash
python3 -m strategy_devkit.scaffold new ...
python3 -m strategy_devkit.schema --root <project> --write
/root/.dotnet/dotnet test <project>/tests/*.csproj -c Release
python3 -m strategy_devkit.publish --root <project> --api http://127.0.0.1:8777
```

It provides single-module build/test, typed config, schema generation, replay tests, package-level upload, instance creation, attach, and artifact recording. The main remaining gaps are richer replay DSL, stronger schema metadata, transaction/resume semantics, and hiding more Lean test primitives from the strategy author.

## Findings

| Area | Current status | Remaining friction | Priority |
|---|---|---|---|
| Engine detail leakage | Improved | Strategy tests still expose `QCAlgorithm`, `Security`, `Slice`, `TradeBar`, `PortfolioTarget.Percent` when fixture behavior is nontrivial. | P1 |
| Single-module compile | Available | Generated tests compile the strategy module without starting Engine. SDK still references `Lean/Tests.Modules/bin/Debug/net10.0` rather than a packaged Module SDK. | P1 |
| Mock/replay testing | Available | Replay covers Signal/Target with bars and expected counts. It lacks weekly-bar helpers, direct warmup controls, order/fill/fee simulation, and fluent assertions. | P1 |
| Upload-before-feedback | Mostly fixed | Build/test/schema/validation happen before upload in `publish`. Full Engine behavior still requires attach or a separate backtest path. | P1 |
| Error control | Improved | Duplicate version is caught early. Control API still returns mostly free-text errors, not stable error codes. Publish is not transactional/resumable. | P0 |
| Version iteration | Improved | Version conflicts give a bump-version hint, and `python3 -m strategy_devkit.version bump` updates payload/module versions. It does not yet create semantic release notes or branch-safe migrations. | P1 |
| Alpha composition | Partial | Control API supports alphaGraph nodes/ports/wire ids and validation. Devkit scaffold still focuses on Signal/Target pair, not a graph of many alpha modules. | P1 |
| Storage vs load separation | Good | Module/package repository, configured instances, and active attachment are separate. UI/API still needs clearer delete/unload/list-current operations. | P1 |
| Strong decoupling | Mostly good | Strategy updates go through control API and live manifest/package releases, not Engine config/env vars. Some module tests still depend on Lean binary layout. | P1 |
| Cloud support | Partial | `--remote-file` and package APIs support remote artifact upload. Auth, signed package manifests, remote build workers, and upload policy are missing. | P1 |
| Frontend readiness | Partial | Generated schema now includes description/min/max/groups. Need enum/options conventions, UI ordering, examples, advanced arrays, and validation messages. | P1 |

## Detailed Assessment

### 1. Do developers still need to care about Engine details?

Less than before, but not zero.

What is hidden now:

- control API wiring,
- package upload,
- instance creation,
- attach,
- artifact recording,
- basic `QCAlgorithm`/`Slice` replay setup.

What still leaks:

- `AlphaModel.Update(QCAlgorithm, Slice)`,
- `OnSecuritiesChanged`,
- `TradeBar` time and aggregation semantics,
- `PortfolioTarget.Percent` needing prices and portfolio state,
- source model filtering between Signal and Target.

Recommendation:

- Add a strategy-domain replay DSL:
  - `fixture.weeklyBars`
  - `fixture.dailyBars`
  - `expect.insight(symbol, direction, sourceModel, weight)`
  - `expect.target(symbol, percent)`
- Add generated wrappers like `WeeklySignalModule` and `WeightedTargetModule` for common strategies.

### 2. Is single-module compile and mock test available?

Yes.

Generated scaffold includes:

- module `.csproj`,
- test `.csproj`,
- `ModuleSmokeTests.cs`,
- `ReplayHarness.cs`,
- `tests/fixtures/replay.json`.

The test path does not start Engine and does not publish first. This meets the basic requirement that a strategy author can see feedback locally before upload.

Remaining issue:

- SDK reference points to a build output directory, not a stable package:

```text
Lean/Tests.Modules/bin/Debug/net10.0
```

Recommendation:

- Produce a real `QuantConnect.ModuleSdk` package or local SDK folder with versioned binaries and templates.

### 3. Is error control adequate?

Partially.

Improved:

- publish validates local payload consistency before upload,
- duplicate package version is caught before build/test/upload,
- duplicate version output tells the user which files need version bump.

Still missing:

- stable error codes from `strategy_submit_api.py`,
- structured recovery hints from control API,
- transaction IDs,
- resumable publish,
- rollback if package succeeds but attach/artifact fails.

Recommendation:

- Standardize:

```json
{
  "accepted": false,
  "errorCode": "VERSION_EXISTS",
  "message": "...",
  "recovery": {
    "action": "bumpVersion",
    "files": ["payloads/attach.json", "payloads/package.json"]
  }
}
```

### 4. Is version iteration complete?

Not yet.

Current versioning preserves history and prevents overwriting. That is correct. The devkit now provides a version bump command so the developer does not edit every payload manually.

Command:

```bash
python3 -m strategy_devkit.version bump --root <project> --version 20260526-006
```

It updates:

- `payloads/attach.json`
- `payloads/package.json`
- custom `payloads/*.instance.json`
- `src/ModuleIdentity.cs`

Remaining:

- README examples are not rewritten.
- No semantic release notes are generated.
- No migration record is written until publish.

### 5. Can alpha be freely composed?

At control-plane level, yes partially.

Existing support:

- multiple Signal instances can exist,
- alphaGraph supports nodes, inputs, outputs, wire ids, type compatibility, duplicate producer checks, and cycle detection.

Remaining friction:

- scaffold creates one Signal and one Target, not an alpha graph project.
- no `devkit alpha new-node` or graph fixture.
- no local graph replay runner for multiple alpha nodes.
- no visual/JSON graph examples in scaffold.

Recommendation:

- Add graph scaffold:

```bash
python3 -m strategy_devkit.scaffold alpha-graph --name ...
```

Generate:

- `payloads/alpha.graph.json`,
- multiple signal node templates,
- graph replay test,
- frontend-ready ports schema.

### 6. Are storage, loading, and activation separated?

Yes.

Current separation:

- package/module repository: `/v1/module-packages`, `/v1/modules`,
- configured module instances: `/v1/pipeline/instances`,
- active runtime attachment: `/v1/pipeline/attach`,
- historical snapshots: iterations/artifacts.

This matches the target model:

- add module to repository,
- create configured instance,
- attach instance to live pipeline.

Remaining issue:

- UI needs clearer terminology:
  - `add package/module` = repository write,
  - `save instance` = config write,
  - `attach` = activate,
  - `detach` = deactivate,
  - `delete` = only if unused and history-preserving.

### 7. Does the design satisfy strong decoupling and cloud development?

Mostly.

Good:

- no environment variables for module updates,
- no Engine config editing for strategy updates,
- remote files can be submitted as artifacts,
- package release paths are persisted,
- Engine updates should not require restart for `Live` modules.

Remaining:

- auth and upload policy are not implemented,
- package signing/provenance is missing,
- remote build workers are not modeled,
- control API should expose stable download endpoints for artifacts,
- Module SDK should be versioned separately from the Engine source tree.

## Recommended Next Changes

P0:

- Add structured error codes and recovery hints to control API.
- Add publish transaction/resume model.

P1:

- Add graph devkit for multi-alpha composition.
- Add replay DSL and graph replay.
- Add packaged Module SDK.
- Add artifact download endpoint.
- Add MCP read-only resources and safe tools.
- Add AG-UI event stream for publish/test/attach.

P2:

- Add frontend schema extensions:
  - `x-order`,
  - `x-group`,
  - `examples`,
  - array item labels,
  - enum display names.
- Add remote/cloud build worker support.
- Add signed package manifests.
