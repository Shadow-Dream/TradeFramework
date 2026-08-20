import { useEffect, useRef, useState, type MutableRefObject } from "react"
import { SerializeAddon } from "@xterm/addon-serialize"
import { Unicode11Addon } from "@xterm/addon-unicode11"
import { WebglAddon } from "@xterm/addon-webgl"
import { WebLinksAddon } from "@xterm/addon-web-links"
import { Terminal, type ITheme, type ITerminalOptions } from "@xterm/xterm"
import type { TerminalSnapshot } from "../../../shared/protocol"
import type { KannaSocket, SocketStatus } from "../../app/socket"
import { useTheme } from "../../hooks/useTheme"
import { useTerminalPreferencesStore } from "../../stores/terminalPreferencesStore"

interface Props {
  /** null → a home-directory terminal (dev-box full-screen Terminal page). */
  projectId: string
  terminalId: string
  socket: KannaSocket
  scrollback: number
  connectionStatus: SocketStatus
  clearVersion?: number
  focusRequestVersion?: number
  initialCommand?: string
  onCommandSent?: () => void
  onInitialCommandSent?: (terminalId: string) => void
}

const TERMINAL_THEME_LIGHT: ITheme = {
  foreground: "#0f172a",
  // Zero alpha, and the RGB channels matter — see resolveSurfaceBackground().
  // This is only the fallback for when the surface colour can't be read off the
  // DOM; keep it in step with --background in index.css.
  background: "rgba(255, 255, 255, 0)",
  cursor: "#000000",
  cursorAccent: "#ffffff",
  selectionBackground: "rgba(221,228,236,0.55)",
  selectionInactiveBackground: "rgba(221,228,236,0.38)",
  black: "#0f172a",
  red: "#dc2626",
  green: "#16a34a",
  yellow: "#ca8a04",
  blue: "#2563eb",
  magenta: "#9333ea",
  cyan: "#0891b2",
  white: "#94a3b8",
  brightBlack: "#475569",
  brightRed: "#ef4444",
  brightGreen: "#22c55e",
  brightYellow: "#eab308",
  brightBlue: "#3b82f6",
  brightMagenta: "#a855f7",
  brightCyan: "#06b6d4",
  brightWhite: "#e2e8f0",
}

const TERMINAL_THEME_DARK: ITheme = {
  foreground: "#f8fafc",
  // Zero alpha, and the RGB channels matter — see resolveSurfaceBackground().
  // This is only the fallback for when the surface colour can't be read off the
  // DOM; keep it in step with --background in index.css (223 4% 13%).
  background: "rgba(32, 33, 34, 0)",
  cursor: "#ffffff",
  cursorAccent: "#000000",
  selectionBackground: "rgba(248,250,252,0.28)",
  selectionInactiveBackground: "rgba(248,250,252,0.18)",
  black: "#0f172a",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#facc15",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#cbd5e1",
  brightBlack: "#64748b",
  brightRed: "#fca5a5",
  brightGreen: "#86efac",
  brightYellow: "#fde047",
  brightBlue: "#93c5fd",
  brightMagenta: "#d8b4fe",
  brightCyan: "#67e8f9",
  brightWhite: "#f8fafc",
}

/** Exported so tests can assert both themes stay xterm-parseable. */
export const TERMINAL_THEMES: ITheme[] = [TERMINAL_THEME_LIGHT, TERMINAL_THEME_DARK]

/** Computed `background-color` is always `rgb(...)`/`rgba(...)`; anything else we skip. */
const COMPUTED_RGB = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/

/**
 * The theme background feeds two different rectangles in xterm's WebGL renderer:
 *
 * - the full-viewport rect, drawn with the colour's real alpha — zero keeps the
 *   pane see-through so index.css's transparent backgrounds show through;
 * - one rect per run of cells whose *background attribute word* is non-zero,
 *   drawn with alpha forced to 1 (RectangleRenderer `$a = 1`). DIM, ITALIC and
 *   OVERLINE live in that word, so dim text gets a rect even though its
 *   background is the default one — with black RGB that reads as a black box
 *   behind every dim run (Vite's "ready in", "press h + enter to show help", …).
 *
 * Both read the same colour, so keep alpha at 0 and point RGB at the surface the
 * pane actually sits on: the viewport rect stays invisible and the forced-opaque
 * per-cell rects blend into the background. The DOM renderer draws neither.
 */
export function resolveSurfaceBackground(element: Element | null): string | null {
  let node: Element | null = element
  while (node) {
    const match = globalThis.getComputedStyle?.(node).backgroundColor?.match(COMPUTED_RGB)
    if (match && (match[4] === undefined || parseFloat(match[4]) > 0)) {
      return `rgba(${match[1]}, ${match[2]}, ${match[3]}, 0)`
    }
    node = node.parentElement
  }
  return null
}

function withSurfaceBackground(theme: ITheme, element: Element | null): ITheme {
  const background = resolveSurfaceBackground(element)
  return background ? { ...theme, background } : theme
}

function getTerminalSize(terminal: Terminal) {
  return {
    cols: Math.max(1, terminal.cols || 80),
    rows: Math.max(1, terminal.rows || 24),
  }
}

function getMeasuredTerminalSize(terminal: Terminal, container: HTMLElement) {
  const xtermElement = terminal.element
  const cellDimensions = (
    terminal as unknown as {
      _core?: {
        _renderService?: {
          dimensions?: {
            css?: {
              cell?: {
                width?: number
                height?: number
              }
            }
          }
        }
      }
    }
  )._core?._renderService?.dimensions?.css?.cell

  const cellWidth = cellDimensions?.width ?? 0
  const cellHeight = cellDimensions?.height ?? 0

  if (!xtermElement || !Number.isFinite(cellWidth) || !Number.isFinite(cellHeight) || cellWidth <= 0 || cellHeight <= 0) {
    return null
  }

  const containerRect = container.getBoundingClientRect()
  const containerStyle = window.getComputedStyle(container)
  const xtermStyle = window.getComputedStyle(xtermElement)
  const overviewRulerWidth = terminal.options.scrollback === 0 ? 0 : (terminal.options.overviewRuler?.width ?? 14)
  const widthPadding = parseFloat(containerStyle.paddingLeft) + parseFloat(containerStyle.paddingRight) + parseFloat(xtermStyle.paddingLeft) + parseFloat(xtermStyle.paddingRight)
  const heightPadding = parseFloat(containerStyle.paddingTop) + parseFloat(containerStyle.paddingBottom) + parseFloat(xtermStyle.paddingTop) + parseFloat(xtermStyle.paddingBottom)
  const availableWidth = Math.max(0, containerRect.width - widthPadding - overviewRulerWidth - 1)
  const availableHeight = Math.max(0, containerRect.height - heightPadding)

  return {
    cols: Math.max(2, Math.floor(availableWidth / cellWidth)),
    rows: Math.max(1, Math.floor(availableHeight / cellHeight)),
  }
}

function refreshTerminal(terminal: Terminal) {
  terminal.refresh(0, Math.max(0, terminal.rows - 1))
}

function isMacPlatform(platform: string) {
  return /mac/i.test(platform)
}

interface MacOptionKeyEvent {
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  key: string
  getModifierState?: (key: string) => boolean
}

export function getTerminalOptions(scrollback: number, theme: ITheme, platform = globalThis.navigator?.platform ?? ""): ITerminalOptions {
  return {
    scrollback,
    // Required before touching `terminal.unicode` — xterm throws from
    // _checkProposedApi() otherwise. The server's shadow terminal already
    // sets it.
    allowProposedApi: true,
    cursorBlink: true,
    cursorStyle: "bar",
    cursorWidth: 1,
    lineHeight: 1,
    convertEol: false,
    allowTransparency: true,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: 13,
    theme,
    macOptionIsMeta: isMacPlatform(platform),
    // Shrink glyphs that a fallback font draws wider than their cell instead of
    // letting them bleed over the next column. xterm defaults this off.
    rescaleOverlappingGlyphs: true,
  }
}

export function getMacOptionInputSequence(event: MacOptionKeyEvent, platform = globalThis.navigator?.platform ?? "") {
  if (event.ctrlKey) return null

  if (!event.altKey && !event.metaKey) {
    switch (event.key) {
      case "ArrowUp":
        return "\x1b[A"
      case "ArrowDown":
        return "\x1b[B"
      case "ArrowLeft":
        return "\x1b[D"
      case "ArrowRight":
        return "\x1b[C"
      default:
        return null
    }
  }

  if (!isMacPlatform(platform)) return null

  if (event.metaKey && !event.altKey) {
    switch (event.key) {
      case "Backspace":
        return "\x15"
      case "Delete":
        return "\x0b"
      default:
        return null
    }
  }

  const isOptionPressed = event.altKey || event.getModifierState?.("AltGraph") === true
  if (!isOptionPressed) return null

  switch (event.key) {
    case "ArrowLeft":
      return "\x1bb"
    case "ArrowRight":
      return "\x1bf"
    case "Backspace":
      return "\x1b\x7f"
    case "Delete":
      return "\x1bd"
    default:
      return null
  }
}

function syncTerminalSize(
  terminal: Terminal,
  container: HTMLElement,
  lastSizeRef: MutableRefObject<{ cols: number; rows: number } | null>,
  hasCreated: boolean,
  sendResize: (cols: number, rows: number) => void
) {
  const nextSize = getMeasuredTerminalSize(terminal, container) ?? getTerminalSize(terminal)
  if (lastSizeRef.current && lastSizeRef.current.cols === nextSize.cols && lastSizeRef.current.rows === nextSize.rows) {
    return nextSize
  }
  terminal.resize(nextSize.cols, nextSize.rows)
  lastSizeRef.current = nextSize
  if (hasCreated) {
    sendResize(nextSize.cols, nextSize.rows)
  }
  return nextSize
}

export function TerminalPane({
  projectId,
  terminalId,
  socket,
  scrollback,
  connectionStatus,
  clearVersion = 0,
  focusRequestVersion = 0,
  initialCommand,
  onCommandSent,
  onInitialCommandSent,
}: Props) {
  const { resolvedTheme } = useTheme()
  // Labs opt-in. Read from the store rather than drilled through the workspace
  // so toggling it only re-mounts the panes.
  const webglRenderer = useTerminalPreferencesStore((store) => store.webglRenderer)
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const replayStateRef = useRef<string | null>(null)
  const onCommandSentRef = useRef<Props["onCommandSent"]>(onCommandSent)
  const hasCreatedRef = useRef(false)
  const createAttemptRef = useRef(0)
  const lastAppliedSnapshotKeyRef = useRef<string | null>(null)
  const sentInitialCommandRef = useRef<string | null>(null)
  const lastSizeRef = useRef<{ cols: number; rows: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const terminalTheme = resolvedTheme === "dark" ? TERMINAL_THEME_DARK : TERMINAL_THEME_LIGHT
  const sendInput = (data: string) => {
    void socket.command({
      type: "terminal.input",
      terminalId,
      data,
    }).catch((commandError) => {
      setError(commandError instanceof Error ? commandError.message : String(commandError))
    })
    if (data.includes("\r") || data.includes("\n")) {
      onCommandSentRef.current?.()
    }
  }
  const sendResize = (cols: number, rows: number) => {
    void socket.command({
      type: "terminal.resize",
      terminalId,
      cols,
      rows,
    }).catch(() => {})
  }
  const scheduleResizeSync = () => {
    const sync = () => {
      const terminalInstance = terminalRef.current
      const element = containerRef.current
      if (!terminalInstance || !element || !hasCreatedRef.current) return
      syncTerminalSize(terminalInstance, element, lastSizeRef, true, sendResize)
    }

    requestAnimationFrame(() => {
      sync()
      setTimeout(sync, 0)
    })
  }

  useEffect(() => {
    onCommandSentRef.current = onCommandSent
  }, [onCommandSent])

  useEffect(() => {
    sentInitialCommandRef.current = null
  }, [initialCommand])

  useEffect(() => {
    const terminal = new Terminal(getTerminalOptions(scrollback, withSurfaceBackground(terminalTheme, containerRef.current)))
    const serializeAddon = new SerializeAddon()
    terminal.loadAddon(serializeAddon)
    terminal.loadAddon(new WebLinksAddon())
    // Must match the shadow terminal on the server: xterm defaults to Unicode 6
    // width tables, which measure astral emoji as one cell instead of two. If
    // the two ends disagree, replayed snapshots land in the wrong columns.
    terminal.loadAddon(new Unicode11Addon())
    terminal.unicode.activeVersion = "11"
    terminal.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true

      const sequence = getMacOptionInputSequence(event)
      if (!sequence) return true

      event.preventDefault()
      sendInput(sequence)
      return false
    })

    terminalRef.current = terminal

    const element = containerRef.current
    let webglAddon: WebglAddon | null = null

    if (element) {
      terminal.open(element)
      // The WebGL renderer needs a live render service, so it can only be
      // attached after open(). Any failure (no GPU, blocklisted driver, lost
      // context) falls back to xterm's built-in DOM renderer rather than
      // leaving the pane blank.
      if (webglRenderer) {
        try {
          const addon = new WebglAddon()
          addon.onContextLoss(() => {
            addon.dispose()
            if (webglAddon === addon) webglAddon = null
          })
          terminal.loadAddon(addon)
          webglAddon = addon
        } catch (webglError) {
          console.warn("Terminal: WebGL renderer unavailable, using the DOM renderer.", webglError)
          webglAddon = null
        }
      }
      if (replayStateRef.current) {
        terminal.write(replayStateRef.current)
      }
      syncTerminalSize(terminal, element, lastSizeRef, false, () => {})
      refreshTerminal(terminal)
      scheduleResizeSync()
    }

    const dataDisposable = terminal.onData((data) => {
      sendInput(data)
    })

    const resizeDisposable = terminal.onResize(({ cols, rows }) => {
      if (!hasCreatedRef.current) return
      const nextSize = { cols, rows }
      if (lastSizeRef.current && lastSizeRef.current.cols === cols && lastSizeRef.current.rows === rows) {
        return
      }
      lastSizeRef.current = nextSize
      sendResize(cols, rows)
    })

    const observer = new ResizeObserver(() => {
      const terminalInstance = terminalRef.current
      const element = containerRef.current
      if (!terminalInstance || !element) return
      syncTerminalSize(terminalInstance, element, lastSizeRef, hasCreatedRef.current, (cols, rows) => {
        void socket.command({
          type: "terminal.resize",
          terminalId,
          cols,
          rows,
        }).catch(() => {})
      })
    })

    if (element) {
      observer.observe(element)
    }

    return () => {
      observer.disconnect()
      resizeDisposable.dispose()
      dataDisposable.dispose()
      replayStateRef.current = serializeAddon.serialize()
      // Release the GL context before the terminal goes away; browsers cap the
      // number of live contexts and won't reclaim it on their own.
      webglAddon?.dispose()
      webglAddon = null
      terminal.dispose()
      terminalRef.current = null
    }
  }, [scrollback, socket, terminalId, terminalTheme, webglRenderer])

  useEffect(() => {
    const terminal = terminalRef.current
    if (!terminal) return
    terminal.options.scrollback = scrollback
  }, [scrollback])

  useEffect(() => {
    const terminal = terminalRef.current
    if (!terminal) return
    terminal.options.theme = withSurfaceBackground(terminalTheme, containerRef.current)
    refreshTerminal(terminal)
  }, [terminalTheme])

  useEffect(() => {
    if (focusRequestVersion === 0) return

    const terminal = terminalRef.current
    if (!terminal) return

    requestAnimationFrame(() => {
      terminal.focus()
    })
  }, [focusRequestVersion])

  useEffect(() => {
    if (clearVersion === 0) return

    const terminal = terminalRef.current
    if (!terminal) return

    hasCreatedRef.current = false
    createAttemptRef.current += 1
    lastAppliedSnapshotKeyRef.current = null
    replayStateRef.current = null
    setError(null)
    terminal.reset()
    refreshTerminal(terminal)
    void socket.command({
      type: "terminal.close",
      terminalId,
    }).catch((commandError) => {
      setError(commandError instanceof Error ? commandError.message : String(commandError))
    })
  }, [clearVersion, socket, terminalId])

  useEffect(() => {
    const applySnapshot = (snapshot: TerminalSnapshot) => {
      const terminal = terminalRef.current
      if (!terminal) return false
      const snapshotKey = JSON.stringify({
        shell: snapshot.shell,
        cols: snapshot.cols,
        rows: snapshot.rows,
        scrollback: snapshot.scrollback,
        status: snapshot.status,
        exitCode: snapshot.exitCode,
        serializedState: snapshot.serializedState,
      })
      if (lastAppliedSnapshotKeyRef.current === snapshotKey) {
        replayStateRef.current = snapshot.serializedState || null
        return false
      }
      lastAppliedSnapshotKeyRef.current = snapshotKey
      replayStateRef.current = snapshot.serializedState || null
      terminal.options.scrollback = snapshot.scrollback
      terminal.reset()
      if (snapshot.serializedState) {
        terminal.write(snapshot.serializedState)
      }
      refreshTerminal(terminal)
      return true
    }

    const ensureSession = () => {
      const terminal = terminalRef.current
      const element = containerRef.current
      if (!terminal || !element) return
      const size = getMeasuredTerminalSize(terminal, element) ?? getTerminalSize(terminal)
      terminal.resize(size.cols, size.rows)
      lastSizeRef.current = size
      void socket.command({
        type: "terminal.create",
        projectId,
        terminalId,
        cols: size.cols,
        rows: size.rows,
        scrollback,
      }).then((snapshot) => {
        hasCreatedRef.current = true
        setError(null)
        if (snapshot) {
          applySnapshot(snapshot as TerminalSnapshot)
        }
        if (initialCommand && sentInitialCommandRef.current !== initialCommand) {
          sentInitialCommandRef.current = initialCommand
          sendInput(`${initialCommand}\r`)
          onInitialCommandSent?.(terminalId)
        }
        scheduleResizeSync()
      }).catch((commandError) => {
        setError(commandError instanceof Error ? commandError.message : String(commandError))
      })
    }

    const scheduleSessionCreate = () => {
      const attempt = ++createAttemptRef.current
      const run = () => {
        if (createAttemptRef.current !== attempt) return
        const terminal = terminalRef.current
        const element = containerRef.current
        if (!terminal || !element) return

        syncTerminalSize(terminal, element, lastSizeRef, false, () => {})
        const rect = element.getBoundingClientRect()
        if (rect.width <= 0 || rect.height <= 0) {
          requestAnimationFrame(run)
          return
        }

        ensureSession()
      }

      requestAnimationFrame(() => {
        requestAnimationFrame(run)
      })
    }

    scheduleSessionCreate()

    return socket.subscribeTerminal(terminalId, {
      onSnapshot: (snapshot) => {
        if (!snapshot) {
          hasCreatedRef.current = false
          lastAppliedSnapshotKeyRef.current = null
          if (connectionStatus === "connected") {
            scheduleSessionCreate()
          }
          return
        }
        hasCreatedRef.current = true
        setError(null)
        if (applySnapshot(snapshot)) {
          scheduleResizeSync()
        }
      },
      onEvent: (event) => {
        const terminal = terminalRef.current
        if (!terminal) return
        if (event.type === "terminal.output") {
          terminal.write(event.data)
          return
        }
        if (event.type === "terminal.exit") {
          return
        }
      },
    })
  }, [connectionStatus, initialCommand, onInitialCommandSent, projectId, scrollback, socket, terminalId])

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden pb-4">
      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden px-3 py-1">
        <div ref={containerRef} className="kanna-terminal min-h-0 min-w-0 flex-1 overflow-hidden w-full" />
      </div>
      {error ? <div className="px-3 py-1 text-xs text-destructive">Terminal error: {error}</div> : null}
    </div>
  )
}
