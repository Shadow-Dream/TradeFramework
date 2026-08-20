export function fallbackTitleFromMessage(messageContent: string): string | null {
  const normalized = messageContent.replace(/\s+/g, " ").trim()
  if (!normalized) return null
  return normalized.length <= 35 ? normalized : `${normalized.slice(0, 35)}...`
}

export interface GenerateChatTitleResult {
  title: string | null
  usedFallback: boolean
  failureMessage: string | null
}

export async function generateTitleForChatDetailed(messageContent: string, _cwd: string): Promise<GenerateChatTitleResult> {
  return { title: fallbackTitleFromMessage(messageContent), usedFallback: true, failureMessage: null }
}
