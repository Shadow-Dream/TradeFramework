import { describe, expect, test } from "bun:test"
import { buildUploadErrorReport, resolveUploadErrorMessage, simpleUploadError } from "./uploadError"

const BASE = {
  file: { name: "screenshot.png", size: 482_113, type: "image/png" },
  projectId: "proj-1",
  requestUrl: "/api/projects/proj-1/uploads",
  stage: "response",
  durationMs: 42,
  responseStatus: 400,
  responseStatusText: "Bad Request",
  responseBody: "",
  error: new Error("The server returned 400."),
  userAgent: "Mozilla/5.0 (iPhone)",
  timestamp: "2026-08-13T00:00:00.000Z",
}

describe("resolveUploadErrorMessage", () => {
  test("prefers the server's own wording", () => {
    expect(resolveUploadErrorMessage({
      responseStatus: 400,
      responseBody: JSON.stringify({ error: "The uploaded file arrived incomplete." }),
      error: new Error("The server returned 400."),
    })).toBe("The uploaded file arrived incomplete.")
  })

  test("falls back to the status when the body is not JSON", () => {
    expect(resolveUploadErrorMessage({
      responseStatus: 502,
      responseBody: "<html>Bad Gateway</html>",
      error: new Error("The server returned 502."),
    })).toBe("The server returned 502.")
  })

  test("uses the client error when no response arrived", () => {
    expect(resolveUploadErrorMessage({
      responseStatus: null,
      responseBody: "",
      error: new TypeError("Load failed"),
    })).toBe("Load failed")
  })
})

describe("buildUploadErrorReport", () => {
  test("records the file, the request, and the response", () => {
    const report = buildUploadErrorReport(BASE)
    expect(report.detail).toContain("file:      screenshot.png")
    expect(report.detail).toContain("size:      482113 bytes")
    expect(report.detail).toContain("type:      image/png")
    expect(report.detail).toContain("request:   POST /api/projects/proj-1/uploads")
    expect(report.detail).toContain("response:  400 Bad Request")
    expect(report.detail).toContain("user agent: Mozilla/5.0 (iPhone)")
  })

  test("surfaces the server's stage and detail fields", () => {
    const report = buildUploadErrorReport({
      ...BASE,
      responseBody: JSON.stringify({
        error: "The uploaded file arrived incomplete.",
        stage: "size-mismatch",
        detail: "screenshot.png: client sent 482113 bytes, server received 0 bytes",
      }),
    })
    expect(report.message).toBe("The uploaded file arrived incomplete.")
    expect(report.detail).toContain("server stage: size-mismatch")
    expect(report.detail).toContain("server received 0 bytes")
  })

  test("says so when the request never completed", () => {
    const report = buildUploadErrorReport({
      ...BASE,
      stage: "request",
      responseStatus: null,
      responseStatusText: "",
      error: new TypeError("Load failed"),
    })
    expect(report.detail).toContain("response:  (none — the request never completed)")
    expect(report.detail).toContain("client error: TypeError: Load failed")
  })

  test("keeps a non-JSON body verbatim", () => {
    const report = buildUploadErrorReport({ ...BASE, responseBody: "<html>Bad Gateway</html>" })
    expect(report.detail).toContain("response body:\n<html>Bad Gateway</html>")
  })

  test("truncates a very long body", () => {
    const report = buildUploadErrorReport({ ...BASE, responseBody: "x".repeat(5000) })
    expect(report.detail).toContain("more characters)")
    expect(report.detail.length).toBeLessThan(5000)
  })
})

describe("simpleUploadError", () => {
  test("gives no detail beyond the message, so the UI hides the block", () => {
    const report = simpleUploadError("Open a project before uploading files.")
    expect(report.message).toBe(report.detail)
  })
})
