import { useCallback } from "react"
import type { AskUserQuestionAnswerMap, SidebarChatRow } from "../../shared/types"
import type { AskUserQuestionItem } from "../components/messages/types"
import type { useAppDialog } from "../components/ui/app-dialog"
import { useChatPreferencesStore } from "../stores/chatPreferencesStore"
import { generateUUID } from "../lib/utils"
import type { KannaSocket } from "./socket"

type SocketCommand = Parameters<KannaSocket["command"]>[0]

// Simple chat command handlers: each one just sends a socket command and routes
// failures into setCommandError. Handlers that navigate or manage extra state
// stay in useKannaState.

export function useChatCommands(params: {
  socket: KannaSocket
  dialog: ReturnType<typeof useAppDialog>
  activeChatId: string | null
  setCommandError: (message: string | null) => void
}) {
  const { socket, dialog, activeChatId, setCommandError } = params

  // Wraps socket.command with the shared error handling. Most handlers clear
  // the command error on success; a few (cancel, stop-draining, tool responses)
  // historically leave it untouched — they pass keepCommandError.
  const wrapCommand = useCallback(async (command: SocketCommand, options?: { keepCommandError?: boolean }) => {
    try {
      await socket.command(command)
      if (!options?.keepCommandError) {
        setCommandError(null)
      }
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [setCommandError, socket])

  const handleSteerQueuedMessage = useCallback(async (queuedMessageId: string) => {
    if (!activeChatId) return
    await wrapCommand({ type: "message.steer", chatId: activeChatId, queuedMessageId })
  }, [activeChatId, wrapCommand])

  const handleRemoveQueuedMessage = useCallback(async (queuedMessageId: string) => {
    if (!activeChatId) return
    await wrapCommand({ type: "message.dequeue", chatId: activeChatId, queuedMessageId })
  }, [activeChatId, wrapCommand])

  const handleCancel = useCallback(async () => {
    if (!activeChatId) return
    await wrapCommand({ type: "chat.cancel", chatId: activeChatId }, { keepCommandError: true })
  }, [activeChatId, wrapCommand])

  const handleRetry = useCallback(async () => {
    if (!activeChatId) return
    await wrapCommand({ type: "chat.retry", chatId: activeChatId, clientRequestId: generateUUID() })
  }, [activeChatId, wrapCommand])

  const handleStopDraining = useCallback(async () => {
    if (!activeChatId) return
    await wrapCommand({ type: "chat.stopDraining", chatId: activeChatId }, { keepCommandError: true })
  }, [activeChatId, wrapCommand])

  const handleRenameChat = useCallback(async (chat: SidebarChatRow) => {
    const title = await dialog.prompt({
      title: "Rename Chat",
      initialValue: chat.title,
      confirmLabel: "Rename",
    })
    if (!title || title === chat.title) return
    await wrapCommand({ type: "chat.rename", chatId: chat.chatId, title })
  }, [dialog, wrapCommand])

  const handleRenameProject = useCallback(async (projectId: string, sidebarTitle: string | undefined, realTitle: string) => {
    const title = await dialog.prompt({
      title: "Rename Project",
      description: "This only changes the sidebar name. The folder path on disk stays the same.",
      initialValue: sidebarTitle ?? "",
      placeholder: realTitle,
      allowEmpty: true,
      resetLabel: "Reset",
      resetValue: "",
      confirmLabel: "Rename",
    })
    if (title === null || title === (sidebarTitle ?? "")) return
    await wrapCommand({ type: "project.rename", projectId, title })
  }, [dialog, wrapCommand])

  const handleAskUserQuestion = useCallback(async (
    toolUseId: string,
    questions: AskUserQuestionItem[],
    answers: AskUserQuestionAnswerMap
  ) => {
    if (!activeChatId) return
    await wrapCommand({
      type: "chat.respondTool",
      chatId: activeChatId,
      toolUseId,
      result: { questions, answers },
    }, { keepCommandError: true })
  }, [activeChatId, wrapCommand])

  const handleExitPlanMode = useCallback(async (toolUseId: string, confirmed: boolean, clearContext?: boolean, message?: string) => {
    if (!activeChatId) return
    if (confirmed) {
      // Clears plan mode only — an Auto Plan chat stays in Auto Plan.
      useChatPreferencesStore.getState().clearChatComposerPlanMode(activeChatId)
    }
    await wrapCommand({
      type: "chat.respondTool",
      chatId: activeChatId,
      toolUseId,
      result: {
        confirmed,
        ...(clearContext ? { clearContext: true } : {}),
        ...(message ? { message } : {}),
      },
    }, { keepCommandError: true })
  }, [activeChatId, wrapCommand])

  return {
    handleSteerQueuedMessage,
    handleRemoveQueuedMessage,
    handleCancel,
    handleRetry,
    handleStopDraining,
    handleRenameChat,
    handleRenameProject,
    handleAskUserQuestion,
    handleExitPlanMode,
  }
}
