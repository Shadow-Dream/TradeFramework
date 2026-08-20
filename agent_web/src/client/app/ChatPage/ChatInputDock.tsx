import { memo, useState, type RefObject } from "react"
import { Clock3, KeyRound, RotateCcw, TriangleAlert, Wrench } from "lucide-react"
import { ChatInput, type ChatInputHandle } from "../../components/chat-ui/ChatInput"
import { Button } from "../../components/ui/button"
import type { ContextWindowSnapshot } from "../../lib/contextWindow"
import type { KannaState } from "../useKannaState"
import type { AgentProvider, ChatSkillsSnapshot } from "../../../shared/types"

interface ChatInputDockProps {
  inputRef: RefObject<HTMLDivElement | null>
  onLayoutChange: () => void
  chatInputRef: RefObject<ChatInputHandle | null>
  chatInputElementRef: RefObject<HTMLTextAreaElement | null>
  activeChatId: string | null
  previousPrompt: string | null
  hasSelectedProject: boolean
  runtimeStatus: string | null
  runtimeModel?: string
  runtimeProvider?: AgentProvider | null
  runtimeTurnStartedAt?: number
  runtimeLastEventAt?: number
  runtimeCurrentTool?: string
  runtimeErrorCode?: string
  runtimeErrorMessage?: string
  runtimeErrorRetryable?: boolean
  canCancel: boolean
  projectId: string | null
  projectLabel: string | null
  activeProvider: AgentProvider | null
  availableProviders: KannaState["availableProviders"]
  contextWindowSnapshot: ContextWindowSnapshot | null
  onSubmit: KannaState["handleSend"]
  onCancel: () => void
  onRetry: () => Promise<void>
  onListSkills?: (provider: AgentProvider) => Promise<ChatSkillsSnapshot>
}

export const ChatInputDock = memo(function ChatInputDock({
  inputRef,
  onLayoutChange,
  chatInputRef,
  chatInputElementRef,
  activeChatId,
  previousPrompt,
  hasSelectedProject,
  runtimeStatus,
  runtimeModel,
  runtimeProvider,
  runtimeTurnStartedAt,
  runtimeLastEventAt,
  runtimeCurrentTool,
  runtimeErrorCode,
  runtimeErrorMessage,
  runtimeErrorRetryable,
  canCancel,
  projectId,
  projectLabel,
  activeProvider,
  availableProviders,
  contextWindowSnapshot,
  onSubmit,
  onCancel,
  onRetry,
  onListSkills,
}: ChatInputDockProps) {
  const [isContinuing, setIsContinuing] = useState(false)

  const continueSession = async () => {
    if (isContinuing) return
    setIsContinuing(true)
    try {
      await onSubmit(
        "Continue from the interrupted turn. First inspect the current workspace and session state, then proceed without repeating completed actions.",
        {
          provider: activeProvider ?? undefined,
          model: runtimeModel,
        },
      )
    } finally {
      setIsContinuing(false)
    }
  }

  const retryTurn = async () => {
    if (isContinuing) return
    setIsContinuing(true)
    try {
      await onRetry()
    } finally {
      setIsContinuing(false)
    }
  }

  return (
    <div className="absolute bottom-0 left-0 right-0 z-20 pointer-events-none">
      <div className="relative pointer-events-auto" ref={inputRef}>
        {/* The wash is its own layer, ending at the transcript's scrollbar
            gutter so it stops dimming the scrollbar (which paints below any
            later positioned sibling and can't be raised with z-index). It has
            to be a layer rather than a background on this wrapper: the wrapper
            stays full width so the composer inside it remains centred on the
            card, not on the card minus the gutter. */}
        <div className="absolute inset-y-0 left-0 right-[var(--transcript-scrollbar-w,0px)] bg-gradient-to-t from-background via-background to-background/10 md:to-background/0 pointer-events-none" />
        <div className="relative">
          {activeChatId && runtimeStatus ? (
            <div className="mx-auto mb-2 flex w-[min(800px,calc(100%-1.5rem))] flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border/70 bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
              <span className="font-medium text-foreground">{runtimeStatus.replaceAll("_", " ")}</span>
              <span>{runtimeProvider === "claude-deepseek" ? "Claude Code + DeepSeek" : runtimeProvider === "codex-openai" ? "Codex + GPT" : "Backend pending"}</span>
              {runtimeModel ? <span>{runtimeModel}</span> : null}
              {runtimeCurrentTool ? <span className="flex items-center gap-1"><Wrench className="size-3" />{runtimeCurrentTool}</span> : null}
              {runtimeTurnStartedAt ? <span className="flex items-center gap-1"><Clock3 className="size-3" />Started {new Date(runtimeTurnStartedAt).toLocaleTimeString()}</span> : null}
              {runtimeLastEventAt ? <span>Last event {new Date(runtimeLastEventAt).toLocaleTimeString()}</span> : null}
            </div>
          ) : null}
          {runtimeStatus === "interrupted" ? (
            <div className="mx-auto mb-2 flex w-[min(800px,calc(100%-1.5rem))] items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-foreground shadow-sm">
              <span>The Web service restarted. Your native agent session is preserved.</span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={isContinuing}
                onClick={() => void continueSession()}
              >
                <RotateCcw className={isContinuing ? "animate-spin" : ""} />
                {isContinuing ? "Continuing..." : "Continue session"}
              </Button>
            </div>
          ) : null}
          {runtimeStatus === "reauth_required" ? (
            <div className="mx-auto mb-2 flex w-[min(800px,calc(100%-1.5rem))] items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-foreground shadow-sm">
              <KeyRound className="size-4 shrink-0 text-red-500" />
              <span>{runtimeErrorMessage || "This backend must be authenticated again before the session can continue."}</span>
            </div>
          ) : null}
          {runtimeStatus === "failed" ? (
            <div className="mx-auto mb-2 flex w-[min(800px,calc(100%-1.5rem))] items-center justify-between gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-foreground shadow-sm">
              <span className="flex items-center gap-2">
                <TriangleAlert className="size-4 shrink-0 text-red-500" />
                {runtimeErrorMessage || runtimeErrorCode || "The Agent runtime failed."}
              </span>
              {runtimeErrorRetryable && previousPrompt ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={isContinuing}
                  onClick={() => void retryTurn()}
                >
                  <RotateCcw className={isContinuing ? "animate-spin" : ""} />
                  {isContinuing ? "Retrying..." : "Retry"}
                </Button>
              ) : null}
            </div>
          ) : null}
          <ChatInput
            ref={chatInputRef}
            inputElementRef={chatInputElementRef}
            onLayoutChange={onLayoutChange}
            key={activeChatId ?? "new-chat"}
            onSubmit={onSubmit}
            onCancel={onCancel}
            disabled={!hasSelectedProject}
            canCancel={canCancel}
            chatId={activeChatId}
            projectId={projectId}
            projectLabel={projectLabel}
            activeProvider={activeProvider}
            availableProviders={availableProviders}
            contextWindowSnapshot={contextWindowSnapshot}
            previousPrompt={previousPrompt}
            onListSkills={onListSkills}
          />
        </div>
      </div>
    </div>
  )
})
