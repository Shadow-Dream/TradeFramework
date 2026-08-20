import { useCallback, useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { readChatJumpLocationState } from "../../lib/chat-navigation"
import type { TranscriptJumpRequest } from "./transcriptScrollAnchors"

/**
 * Turns a "open this chat at this message" navigation into a one-shot request
 * for the transcript viewport.
 *
 * Two things have to happen and neither is the other's job:
 *
 * - The history entry is stripped as soon as the request is picked up. A jump
 *   is an act, not a place: leaving it on the entry means going back to this
 *   chat later, or reloading, silently re-runs a jump the reader didn't ask for
 *   a second time.
 * - The request outlives that strip, held in component state until the viewport
 *   reports it spent. The rows it names usually aren't mounted on the commit
 *   the navigation lands in — the transcript is still hydrating — so a request
 *   read straight off `location.state` would be gone by the time it could be
 *   honoured.
 */
export function useTranscriptJumpRequest(): {
  jumpRequest: TranscriptJumpRequest | null
  onJumpRequestHandled: (requestId: string) => void
} {
  const location = useLocation()
  const navigate = useNavigate()
  const [jumpRequest, setJumpRequest] = useState<TranscriptJumpRequest | null>(null)

  const jump = readChatJumpLocationState(location.state)
  const role = jump?.jumpToRole
  const requestId = jump?.jumpRequestId
  const { pathname, search } = location

  useEffect(() => {
    if (!role || !requestId) return
    setJumpRequest({ role, requestId })
    navigate(`${pathname}${search}`, { replace: true, state: null })
  }, [navigate, pathname, requestId, role, search])

  const onJumpRequestHandled = useCallback((handledId: string) => {
    setJumpRequest((current) => (current?.requestId === handledId ? null : current))
  }, [])

  return { jumpRequest, onJumpRequestHandled }
}
