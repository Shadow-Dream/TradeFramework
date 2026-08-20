import process from "node:process"

process.env.TRADE_AGENT_RUNTIME_PROFILE = "dev"

await import("../src/server/cli")
