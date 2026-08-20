/**
 * Attachment uploads fail in places the user cannot see: a truncated request
 * body, a proxy error page, a write error on the machine. Mobile users have no
 * console, so every detail we have has to reach the UI and the clipboard.
 */

const MAX_BODY_CHARS = 4000

export interface UploadErrorReport {
  /** One line shown next to the composer. */
  message: string
  /** Everything known about the failure, for the details block and the copy button. */
  detail: string
}

export function simpleUploadError(message: string): UploadErrorReport {
  return { message, detail: message }
}

function parseJsonObject(body: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(body)
    return parsed !== null && typeof parsed === "object" ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

function readString(payload: Record<string, unknown> | null, key: string) {
  const value = payload?.[key]
  return typeof value === "string" ? value : null
}

function truncate(body: string) {
  return body.length > MAX_BODY_CHARS
    ? `${body.slice(0, MAX_BODY_CHARS)}\n… (${body.length - MAX_BODY_CHARS} more characters)`
    : body
}

/** The one-line summary. Prefers the server's own wording over the fetch error. */
export function resolveUploadErrorMessage(args: {
  responseStatus: number | null
  responseBody: string
  error: unknown
}) {
  const serverMessage = readString(parseJsonObject(args.responseBody), "error")
  if (serverMessage) return serverMessage
  if (args.responseStatus !== null && args.responseStatus >= 400) {
    return `The server returned ${args.responseStatus}.`
  }
  if (args.error instanceof Error && args.error.message) return args.error.message
  const text = String(args.error)
  return text === "undefined" ? "Upload failed." : text
}

export function buildUploadErrorReport(args: {
  file: { name: string, size: number, type: string }
  projectId: string
  requestUrl: string
  /** How far the upload got: "request", "response", or "read-body". */
  stage: string
  durationMs: number
  responseStatus: number | null
  responseStatusText: string
  responseBody: string
  error: unknown
  userAgent: string
  timestamp: string
}): UploadErrorReport {
  const message = resolveUploadErrorMessage(args)
  const payload = parseJsonObject(args.responseBody)

  const lines = [
    `Upload failed: ${message}`,
    "",
    `when:      ${args.timestamp}`,
    `stage:     ${args.stage}`,
    `duration:  ${args.durationMs}ms`,
    `file:      ${args.file.name}`,
    `size:      ${args.file.size} bytes`,
    `type:      ${args.file.type || "(none reported)"}`,
    `project:   ${args.projectId}`,
    `request:   POST ${args.requestUrl}`,
    args.responseStatus === null
      ? "response:  (none — the request never completed)"
      : `response:  ${args.responseStatus} ${args.responseStatusText}`.trimEnd(),
  ]

  const serverStage = readString(payload, "stage")
  if (serverStage) {
    lines.push(`server stage: ${serverStage}`)
  }

  const serverDetail = readString(payload, "detail")
  if (serverDetail) {
    lines.push("", "server detail:", serverDetail)
  }

  if (args.responseBody) {
    lines.push("", "response body:", truncate(args.responseBody))
  }

  if (args.error instanceof Error) {
    lines.push("", `client error: ${args.error.name}: ${args.error.message}`)
    if (args.error.stack) {
      lines.push(args.error.stack)
    }
  } else if (args.error !== undefined) {
    lines.push("", `client error: ${String(args.error)}`)
  }

  lines.push("", `user agent: ${args.userAgent}`)

  return { message, detail: lines.join("\n") }
}
