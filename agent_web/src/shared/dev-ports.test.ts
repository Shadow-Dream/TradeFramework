import { describe, expect, test } from "bun:test"
import {
  DEFAULT_DEV_CLIENT_PORT,
  getDefaultDevServerPort,
  parseDevArgs,
  resolveDevPorts,
  stripPortArg,
} from "./dev-ports"

describe("TradeEngine Agent dev ports", () => {
  test("derives the backend port from the client port", () => {
    expect(getDefaultDevServerPort()).toBe(DEFAULT_DEV_CLIENT_PORT + 1)
    expect(resolveDevPorts(["--port", "4000"])).toEqual({ clientPort: 4000, serverPort: 4001 })
  })

  test("rejects a missing port value", () => {
    expect(() => resolveDevPorts(["--port"])).toThrow("Missing value for --port")
  })

  test("strips only the client port arguments", () => {
    expect(stripPortArg(["--remote", "--port", "4000", "--host", "dev-box"]))
      .toEqual(["--remote", "--host", "dev-box"])
  })

  test("supports an explicit LAN host without cloud/share modes", () => {
    expect(parseDevArgs(["--host", "0.0.0.0", "--port", "3333"], "dev-machine")).toEqual({
      clientPort: 3333,
      serverPort: 3334,
      backendTargetHost: "127.0.0.1",
      allowedHosts: ["localhost", "127.0.0.1", "0.0.0.0", "dev-machine"],
      serverArgs: ["--host", "0.0.0.0"],
    })
  })
})
