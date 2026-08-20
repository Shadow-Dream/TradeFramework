import type {
  AppSettingsSnapshot,
  AppSettingsPatch,
  AgentProvider,
  AuthServiceId,
  ProviderAuthSnapshot,
  ChatAttachment,
  ChatDiffSnapshot,
  ChatSnapshot,
  KeybindingsSnapshot,
  LocalProjectsSnapshot,
  ModelOptions,
  SidebarData,
  UsageLimitsSnapshot,
} from "./types"
import type { UiTurnContextV1 } from "./ui-sync-protocol"

export type SubscriptionTopic =
  | { type: "sidebar" }
  | { type: "local-projects" }
  | { type: "keybindings" }
  | { type: "app-settings" }
  | { type: "usage-limits" }
  | { type: "provider-auth" }
  | { type: "ui-context" }
  | {
    type: "chat"
    chatId: string
    /**
     * The absolute transcript span the client already holds from its local
     * cache, so the first push can be incremental instead of a full window.
     * Omitted when the client has nothing cached.
     *
     * `endEntryId` is the `_id` of the entry at `end - 1`. The server only
     * honours the span when that entry still matches, which keeps a cache
     * belonging to a different machine — or one written before a transcript
     * was rewritten — from being spliced onto unrelated history.
     */
    cachedSpan?: { start: number; end: number; endEntryId: string }
  }
  | { type: "project-git"; projectId: string }
  | { type: "terminal"; terminalId: string }

export interface TerminalSnapshot {
  terminalId: string
  title: string
  shell: string
  cols: number
  rows: number
  scrollback: number
  serializedState: string
  status: "running" | "exited"
  exitCode: number | null
  signal?: number
}

export type TerminalEvent =
  | { type: "terminal.output"; terminalId: string; data: string }
  | { type: "terminal.exit"; terminalId: string; exitCode: number; signal?: number }

export type ClientCommand =
  | { type: "project.rename"; projectId: string; title: string }
  | { type: "sidebar.reorderProjectGroups"; projectIds: string[] }
  | { type: "project.readDiffPatch"; projectId: string; path: string }
  | { type: "system.ping" }
  | { type: "settings.readKeybindings" }
  | { type: "settings.writeKeybindings"; bindings: KeybindingsSnapshot["bindings"] }
  | { type: "settings.readAppSettings" }
  | { type: "settings.writeAppSettingsPatch"; patch: AppSettingsPatch }
  | { type: "usage.refresh"; force?: boolean }
  | { type: "auth.refresh"; force?: boolean }
  | { type: "auth.login.start"; service: AuthServiceId }
  | { type: "auth.login.cancel"; service: AuthServiceId }
  /** Claude Code + DeepSeek only. The secret is written to provider-scoped
   * storage and is never returned in a snapshot. */
  | { type: "auth.claude.deepseek.setApiKey"; apiKey: string }
  | { type: "chat.listSkills"; provider: AgentProvider; chatId?: string; projectId?: string }
  | { type: "chat.create"; projectId: string; provider: AgentProvider; clientRequestId: string }
  | { type: "chat.fork"; chatId: string; clientRequestId: string }
  | { type: "chat.rename"; chatId: string; title: string }
  | { type: "chat.archive"; chatId: string }
  | { type: "chat.unarchive"; chatId: string }
  | { type: "chat.delete"; chatId: string }
  | { type: "chat.setDraftProtection"; chatIds: string[] }
  /**
   * The files a chat changed, for its sidebar hover card. Ack-only and read
   * on demand: the list is too big per chat to ride along on every sidebar
   * snapshot, and it's only ever wanted for the one row under the pointer.
   */
  | { type: "chat.touchedFiles"; chatId: string }
  | { type: "chat.markRead"; chatId: string }
  | { type: "chat.setDone"; chatId: string; done: boolean }
  /**
   * Persist where the user left off reading. Sent on a throttle while
   * scrolling. Deliberately ack-only: the anchor is not part of any snapshot,
   * so a scroll never triggers a sidebar or chat re-push to other sockets.
   */
  | {
    type: "chat.setReadAnchor"
    chatId: string
    messageId: string
    atEnd: boolean
    /** Transcript column width and the position's distance into the message. */
    transcriptWidth?: number
    offsetFromMessage?: number
  }
  /** Read back the stored anchor when opening a chat. Result: ResolvedChatReadAnchor | null. */
  | { type: "chat.getReadAnchor"; chatId: string }
  /**
   * Fetch tool entries with their payloads intact. Snapshots ship tool calls
   * and results without their unbounded fields — a collapsed row draws none of
   * them — so opening a row asks for the real thing. Batched: expanding a tool
   * group wants every member at once. Result: TranscriptEntry[].
   */
  | { type: "chat.getToolEntries"; chatId: string; entryIds: string[] }
  | {
      type: "chat.send"
      chatId?: string
      projectId?: string
      provider: AgentProvider
      content: string
      attachments?: ChatAttachment[]
      model: string
      modelOptions?: ModelOptions
      effort?: string
      planMode?: boolean
      autoPlan?: boolean
      clientRequestId: string
    }
  | { type: "chat.refreshDiffs"; chatId: string }
  | { type: "chat.cancel"; chatId: string }
  | { type: "chat.retry"; chatId: string; clientRequestId: string }
  | { type: "chat.stopDraining"; chatId: string }
  | { type: "chat.respondTool"; chatId: string; toolUseId: string; result: unknown }
  | {
      type: "message.enqueue"
      chatId: string
      content: string
      attachments?: ChatAttachment[]
      provider: AgentProvider
      model: string
      modelOptions?: ModelOptions
      planMode?: boolean
      autoPlan?: boolean
      clientRequestId: string
    }
  | {
      type: "message.steer"
      chatId: string
      queuedMessageId: string
    }
  | {
      type: "message.dequeue"
      chatId: string
      queuedMessageId: string
    }
  | { type: "terminal.create"; projectId: string; terminalId: string; cols: number; rows: number; scrollback: number }
  | { type: "terminal.input"; terminalId: string; data: string }
  | { type: "terminal.resize"; terminalId: string; cols: number; rows: number }
  | { type: "terminal.close"; terminalId: string }

export type ClientEnvelope =
  | { v: 1; type: "subscribe"; id: string; topic: SubscriptionTopic }
  | { v: 1; type: "unsubscribe"; id: string }
  | { v: 1; type: "command"; id: string; command: ClientCommand }

export type ServerSnapshot =
  | { type: "sidebar"; data: SidebarData }
  | { type: "local-projects"; data: LocalProjectsSnapshot }
  | { type: "keybindings"; data: KeybindingsSnapshot }
  | { type: "app-settings"; data: AppSettingsSnapshot }
  | { type: "usage-limits"; data: UsageLimitsSnapshot }
  | { type: "provider-auth"; data: ProviderAuthSnapshot }
  | { type: "ui-context"; data: UiTurnContextV1 }
  | { type: "chat"; data: ChatSnapshot | null }
  | { type: "project-git"; data: ChatDiffSnapshot | null }
  | { type: "terminal"; data: TerminalSnapshot | null }

export type ServerEnvelope =
  | { v: 1; type: "snapshot"; id: string; snapshot: ServerSnapshot }
  | { v: 1; type: "event"; id: string; event: TerminalEvent }
  | { v: 1; type: "ack"; id: string; result?: unknown }
  | { v: 1; type: "error"; id?: string; message: string }

export function isClientEnvelope(value: unknown): value is ClientEnvelope {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<ClientEnvelope>
  return candidate.v === 1 && typeof candidate.type === "string"
}
