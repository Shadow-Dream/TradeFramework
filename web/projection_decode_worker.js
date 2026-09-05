"use strict";

self.addEventListener("message", (event) => {
  const requestId = event.data?.requestId;
  try {
    if (typeof requestId !== "string" || !requestId
        || !(event.data?.buffer instanceof ArrayBuffer)) {
      throw new Error("Projection decode request is invalid.");
    }
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(event.data.buffer));
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Projection response must be a JSON object.");
    }
    self.postMessage({ requestId, ok: true, value });
  } catch (error) {
    self.postMessage({
      requestId: typeof requestId === "string" ? requestId : "",
      ok: false,
      error: error?.message || "Projection JSON could not be decoded.",
    });
  }
});
