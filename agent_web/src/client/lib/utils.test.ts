import { afterEach, describe, expect, test } from "bun:test"
import { generateUUID } from "./utils"

const originalCrypto = globalThis.crypto

afterEach(() => {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: originalCrypto,
  })
})

describe("generateUUID", () => {
  test("uses random bytes when randomUUID alone is unavailable", () => {
    let requested = false
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {
        getRandomValues(bytes: Uint8Array) {
          requested = true
          bytes.fill(0x2a)
          return bytes
        },
      },
    })

    expect(generateUUID()).toBe("2a2a2a2a-2a2a-4a2a-aa2a-2a2a2a2a2a2a")
    expect(requested).toBe(true)
  })

  test("falls back when an HTTP browser does not expose crypto.randomUUID", () => {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {},
    })

    expect(generateUUID()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
  })
})
