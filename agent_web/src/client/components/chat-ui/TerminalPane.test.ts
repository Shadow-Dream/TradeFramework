import { describe, expect, test } from "bun:test"
import { getMacOptionInputSequence, getTerminalOptions, resolveSurfaceBackground, TERMINAL_THEMES } from "./TerminalPane"

describe("getTerminalOptions", () => {
  test("treats Option as Meta on macOS", () => {
    const options = getTerminalOptions(1_000, { foreground: "#fff" }, "MacIntel")

    expect(options.macOptionIsMeta).toBe(true)
    expect(options.scrollback).toBe(1_000)
    expect(options.lineHeight).toBe(1)
  })

  test("does not enable macOS Option behavior on non-mac platforms", () => {
    const options = getTerminalOptions(500, { foreground: "#fff" }, "Linux x86_64")

    expect(options.macOptionIsMeta).toBe(false)
    expect(options.scrollback).toBe(500)
  })

  // Reading `terminal.unicode` throws from xterm's _checkProposedApi() without
  // this, which crashes the whole pane at construction.
  test("opts into proposed API so the Unicode version can be set", () => {
    expect(getTerminalOptions(1_000, { foreground: "#fff" }, "Linux x86_64").allowProposedApi).toBe(true)
  })
})

describe("terminal themes", () => {
  // xterm's css.toColor() cannot parse the CSS keyword "transparent"; it throws
  // and the theme silently falls back to opaque black. The DOM renderer masks
  // that, the WebGL renderer paints it into the canvas.
  test("use a parseable zero-alpha background rather than the transparent keyword", () => {
    for (const theme of TERMINAL_THEMES) {
      expect(theme.background).not.toBe("transparent")
      expect(theme.background).toMatch(/^rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(\.0+)?\s*\)$/)
    }
  })

  // The WebGL RectangleRenderer forces alpha 1 on the per-cell background rects
  // it draws, and it draws one for any cell carrying a background-word flag —
  // DIM included. Pure black RGB there paints a black box behind dim text.
  test("do not fall back to black RGB, which the WebGL renderer paints behind dim runs", () => {
    for (const theme of TERMINAL_THEMES) {
      expect(theme.background).not.toMatch(/^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,/)
    }
  })
})

describe("resolveSurfaceBackground", () => {
  function fakeElement(backgroundColor: string, parentElement: Element | null = null) {
    return { backgroundColor, parentElement } as unknown as Element
  }

  function withComputedStyle<T>(run: () => T): T {
    const previous = globalThis.getComputedStyle
    // @ts-expect-error — stubbing the global for the walk
    globalThis.getComputedStyle = (node: { backgroundColor: string }) => ({ backgroundColor: node.backgroundColor })
    try {
      return run()
    } finally {
      // @ts-expect-error — restoring the global
      globalThis.getComputedStyle = previous
    }
  }

  test("keeps alpha at zero so the viewport rectangle stays invisible", () => {
    withComputedStyle(() => {
      expect(resolveSurfaceBackground(fakeElement("rgb(32, 33, 34)"))).toBe("rgba(32, 33, 34, 0)")
    })
  })

  test("walks past transparent ancestors to the first painted surface", () => {
    withComputedStyle(() => {
      const surface = fakeElement("rgb(255, 255, 255)")
      const pane = fakeElement("rgba(0, 0, 0, 0)", surface)
      const container = fakeElement("rgba(0, 0, 0, 0)", pane)

      expect(resolveSurfaceBackground(container)).toBe("rgba(255, 255, 255, 0)")
    })
  })

  test("returns null when nothing paints a background, leaving the theme fallback in place", () => {
    withComputedStyle(() => {
      expect(resolveSurfaceBackground(fakeElement("rgba(0, 0, 0, 0)"))).toBeNull()
      expect(resolveSurfaceBackground(fakeElement("oklch(0.2 0 0)"))).toBeNull()
      expect(resolveSurfaceBackground(null)).toBeNull()
    })
  })
})

describe("getMacOptionInputSequence", () => {
  test("maps plain arrow keys to standard escape sequences", () => {
    expect(getMacOptionInputSequence({
      key: "ArrowUp",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
    }, "MacIntel")).toBe("\x1b[A")

    expect(getMacOptionInputSequence({
      key: "ArrowDown",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
    }, "MacIntel")).toBe("\x1b[B")
  })

  test("maps Option+Left and Option+Right to shell word motion on macOS", () => {
    expect(getMacOptionInputSequence({
      key: "ArrowLeft",
      altKey: true,
      ctrlKey: false,
      metaKey: false,
    }, "MacIntel")).toBe("\x1bb")

    expect(getMacOptionInputSequence({
      key: "ArrowRight",
      altKey: true,
      ctrlKey: false,
      metaKey: false,
    }, "MacIntel")).toBe("\x1bf")
  })

  test("maps Option+Backspace to backward delete word on macOS", () => {
    expect(getMacOptionInputSequence({
      key: "Backspace",
      altKey: true,
      ctrlKey: false,
      metaKey: false,
    }, "MacIntel")).toBe("\x1b\x7f")
  })

  test("maps Command+Delete to kill backward and forward by line on macOS", () => {
    expect(getMacOptionInputSequence({
      key: "Backspace",
      altKey: false,
      ctrlKey: false,
      metaKey: true,
    }, "MacIntel")).toBe("\x15")

    expect(getMacOptionInputSequence({
      key: "Delete",
      altKey: false,
      ctrlKey: false,
      metaKey: true,
    }, "MacIntel")).toBe("\x0b")
  })

  test("ignores non-mac or modified key combinations", () => {
    expect(getMacOptionInputSequence({
      key: "ArrowLeft",
      altKey: true,
      ctrlKey: false,
      metaKey: false,
    }, "Linux x86_64")).toBeNull()

    expect(getMacOptionInputSequence({
      key: "ArrowLeft",
      altKey: true,
      ctrlKey: true,
      metaKey: false,
    }, "MacIntel")).toBeNull()
  })
})
