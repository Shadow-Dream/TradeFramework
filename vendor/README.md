# Vendored projects

This directory contains independently versioned third-party or reference
projects. Engine production code must not import from `vendor`.

- `TradingAgents/` retains its dependency manifest; its local upstream Git
  metadata is workspace-only and not part of the Engine repository.
