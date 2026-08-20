import { DEFAULT_KEYBINDINGS, type KeybindingAction, type KeybindingsSnapshot } from "../../shared/types"

export const KEYBINDING_ACTION_LABELS: Record<KeybindingAction, string> = {
  toggleEmbeddedTerminal: "Toggle Embedded Terminal",
  toggleRightSidebar: "Toggle Right Sidebar",
  addSplitTerminal: "Add Split Terminal",
  jumpToSidebarChat: "Jump To Sidebar Chat",
  createChatInCurrentProject: "New Agent Session",
  openCommandPalette: "Open Command Palette",
  toggleFocusMode: "Toggle Focus Mode",
}

const SHORTCUT_MODIFIER_GLYPHS: Record<string, string> = {
  cmd: "⌘", command: "⌘", meta: "⌘",
  ctrl: "⌃", control: "⌃",
  alt: "⌥", option: "⌥",
  shift: "⇧",
}

const SHORTCUT_KEY_GLYPHS: Record<string, string> = {
  enter: "↵", return: "↵", escape: "⎋", esc: "⎋",
  backspace: "⌫", delete: "⌦", tab: "⇥", space: "␣",
  up: "↑", down: "↓", left: "←", right: "→",
  arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→",
}

/** Canonical mac ordering: Control, Option, Shift, Command. */
const SHORTCUT_GLYPH_ORDER = ["⌃", "⌥", "⇧", "⌘"]

/** Render a binding like "cmd+alt+k" as glyphs "⌥⌘K". */
export function shortcutToGlyphs(binding: string): string {
  const modifiers = new Set<string>()
  let key = ""
  for (const raw of binding.split("+").map((part) => part.trim().toLowerCase()).filter(Boolean)) {
    const modifier = SHORTCUT_MODIFIER_GLYPHS[raw]
    if (modifier) modifiers.add(modifier)
    else key = raw
  }
  const orderedModifiers = SHORTCUT_GLYPH_ORDER.filter((glyph) => modifiers.has(glyph))
  const keyGlyph = SHORTCUT_KEY_GLYPHS[key] ?? key.toUpperCase()
  return [...orderedModifiers, keyGlyph].join("")
}

/** An action's first binding as glyphs, or null when it has none. */
export function formatActionShortcut(
  snapshot: KeybindingsSnapshot | null,
  action: KeybindingAction
): string | null {
  const binding = getBindingsForAction(snapshot, action)[0]
  return binding ? shortcutToGlyphs(binding) : null
}

export function formatKeybindingInput(bindings: string[] | undefined) {
  return (bindings ?? []).join(", ")
}

export function parseKeybindingInput(value: string) {
  return value
    .split(",")
    .map((binding) => binding.trim())
    .map((binding) => binding.toLowerCase())
    .filter(Boolean)
}

type ParsedBinding = {
  key: string
  ctrl: boolean
  meta: boolean
  alt: boolean
  shift: boolean
}

const MODIFIER_TOKENS = new Map([
  ["cmd", "meta"],
  ["meta", "meta"],
  ["ctrl", "ctrl"],
  ["control", "ctrl"],
  ["alt", "alt"],
  ["option", "alt"],
  ["shift", "shift"],
])

export function bindingMatchesEvent(binding: string, event: KeyboardEvent) {
  const parsed = parseBinding(binding)
  if (!parsed) return false

  return (
    eventMatchesParsedKey(event, parsed.key) &&
    event.ctrlKey === parsed.ctrl &&
    event.metaKey === parsed.meta &&
    event.altKey === parsed.alt &&
    event.shiftKey === parsed.shift
  )
}

export function actionMatchesEvent(
  snapshot: KeybindingsSnapshot | null,
  action: KeybindingAction,
  event: KeyboardEvent
) {
  const bindings = getBindingsForAction(snapshot, action)
  return bindings.some((binding) => bindingMatchesEvent(binding, event))
}

export function findMatchingActionBinding(
  snapshot: KeybindingsSnapshot | null,
  action: KeybindingAction,
  event: KeyboardEvent
) {
  return getBindingsForAction(snapshot, action).find((binding) => bindingMatchesEvent(binding, event)) ?? null
}

export function getBindingsForAction(
  snapshot: KeybindingsSnapshot | null,
  action: KeybindingAction
) {
  return snapshot?.bindings[action] ?? DEFAULT_KEYBINDINGS[action]
}

export function getResolvedKeybindings(snapshot: KeybindingsSnapshot | null): KeybindingsSnapshot {
  return {
    bindings: {
      toggleEmbeddedTerminal: snapshot?.bindings.toggleEmbeddedTerminal ?? DEFAULT_KEYBINDINGS.toggleEmbeddedTerminal,
      toggleRightSidebar: snapshot?.bindings.toggleRightSidebar ?? DEFAULT_KEYBINDINGS.toggleRightSidebar,
      addSplitTerminal: snapshot?.bindings.addSplitTerminal ?? DEFAULT_KEYBINDINGS.addSplitTerminal,
      jumpToSidebarChat: snapshot?.bindings.jumpToSidebarChat ?? DEFAULT_KEYBINDINGS.jumpToSidebarChat,
      createChatInCurrentProject: snapshot?.bindings.createChatInCurrentProject ?? DEFAULT_KEYBINDINGS.createChatInCurrentProject,
      openCommandPalette: snapshot?.bindings.openCommandPalette ?? DEFAULT_KEYBINDINGS.openCommandPalette,
      toggleFocusMode: snapshot?.bindings.toggleFocusMode ?? DEFAULT_KEYBINDINGS.toggleFocusMode,
    },
    warning: snapshot?.warning ?? null,
  }
}

function parseBinding(binding: string): ParsedBinding | null {
  const parts = binding.split("+").map((part) => part.trim()).filter(Boolean)
  if (parts.length === 0) return null

  const parsed: ParsedBinding = {
    key: "",
    ctrl: false,
    meta: false,
    alt: false,
    shift: false,
  }

  for (const part of parts) {
    const token = part.toLowerCase()
    const modifier = MODIFIER_TOKENS.get(token)
    if (modifier === "ctrl") {
      parsed.ctrl = true
      continue
    }
    if (modifier === "meta") {
      parsed.meta = true
      continue
    }
    if (modifier === "alt") {
      parsed.alt = true
      continue
    }
    if (modifier === "shift") {
      parsed.shift = true
      continue
    }
    if (parsed.key) {
      return null
    }
    parsed.key = token
  }

  return parsed.key ? parsed : null
}

function eventMatchesParsedKey(event: KeyboardEvent, key: string) {
  if (event.key.toLowerCase() === key) {
    return true
  }

  const expectedCode = keyToCode(key)
  if (!expectedCode) {
    return false
  }

  return event.code === expectedCode
}

function keyToCode(key: string) {
  if (key.length === 1 && key >= "a" && key <= "z") {
    return `Key${key.toUpperCase()}`
  }
  if (key.length === 1 && key >= "0" && key <= "9") {
    return `Digit${key}`
  }

  switch (key) {
    case "/":
      return "Slash"
    case "`":
      return "Backquote"
    default:
      return null
  }
}
