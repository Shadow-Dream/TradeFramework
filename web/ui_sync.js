(() => {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const HEARTBEAT_MS = 15_000;
  const COMMAND_TIMEOUT_MS = 15_000;
  const RECONNECT_MAX_MS = 5_000;
  const TAB_ID_KEY = "trade.ui-sync.tab-id.v1";
  const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function rotateRight(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
  }

  function portableSha256(bytes) {
    const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    const bitLength = bytes.length * 8;
    view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
    view.setUint32(paddedLength - 4, bitLength >>> 0);
    const hash = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const words = new Uint32Array(64);
    for (let offset = 0; offset < paddedLength; offset += 64) {
      for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4);
      for (let index = 16; index < 64; index += 1) {
        const s0 = rotateRight(words[index - 15], 7) ^ rotateRight(words[index - 15], 18) ^ (words[index - 15] >>> 3);
        const s1 = rotateRight(words[index - 2], 17) ^ rotateRight(words[index - 2], 19) ^ (words[index - 2] >>> 10);
        words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        const choice = (e & f) ^ (~e & g);
        const temp1 = (h + sum1 + choice + SHA256_K[index] + words[index]) >>> 0;
        const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (sum0 + majority) >>> 0;
        h = g; g = f; f = e; e = (d + temp1) >>> 0;
        d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
      }
      [a, b, c, d, e, f, g, h].forEach((value, index) => { hash[index] = (hash[index] + value) >>> 0; });
    }
    return [...hash].map((value) => value.toString(16).padStart(8, "0")).join("");
  }

  function randomId(prefix = "ui") {
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
    const value = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${prefix}-${value}`;
  }

  function loadTabId(storageKey = TAB_ID_KEY, prefix = "engine-tab") {
    try {
      const existing = sessionStorage.getItem(storageKey);
      if (existing) return existing;
      const created = randomId(prefix);
      sessionStorage.setItem(storageKey, created);
      return created;
    } catch {
      return randomId(prefix);
    }
  }

  class UiSyncClient {
    constructor(options = {}) {
      this.clientKind = options.clientKind || "engine-spa";
      this.capabilities = options.capabilities || ["presence", "context", "document-read", "document-write", "resource-events", "operation-events"];
      this.tabId = loadTabId(options.tabIdKey || TAB_ID_KEY, options.tabIdPrefix || `${this.clientKind}-tab`);
      this.socket = null;
      this.started = false;
      this.ready = false;
      this.webSocketUrl = "";
      this.reconnectTimer = 0;
      this.reconnectDelay = 750;
      this.heartbeatTimer = 0;
      this.pending = new Map();
      this.stateListeners = new Set();
      this.resourceListeners = new Set();
      this.operationListeners = new Set();
      this.documentProviders = new Map();
      this.documents = new Map();
      this.lastContext = null;
      this.lastServerSeq = 0;
      this.snapshot = null;
      this.handleFocus = () => this.publishPresence({ focused: true, visible: document.visibilityState === "visible", interacted: true });
      this.handleBlur = () => this.publishPresence({ focused: false, visible: document.visibilityState === "visible" });
      this.handleVisibility = () => this.publishPresence({ focused: document.hasFocus(), visible: document.visibilityState === "visible" });
      this.handleInteraction = () => this.publishPresence({ focused: document.hasFocus(), visible: document.visibilityState === "visible", interacted: true });
    }

    async start() {
      if (this.started) return;
      this.started = true;
      const response = await fetch("/api/ui-sync/config", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`UI sync bootstrap failed (${response.status})`);
      const config = await response.json();
      if (config.protocolVersion !== PROTOCOL_VERSION
          || typeof config.webSocketUrl !== "string"
          || !/^wss?:\/\//.test(config.webSocketUrl)) {
        throw new Error("UI sync bootstrap returned an invalid contract");
      }
      this.webSocketUrl = config.webSocketUrl;
      window.addEventListener("focus", this.handleFocus);
      window.addEventListener("blur", this.handleBlur);
      document.addEventListener("visibilitychange", this.handleVisibility);
      window.addEventListener("pointerdown", this.handleInteraction, { passive: true });
      this.connect();
    }

    stop() {
      this.started = false;
      this.ready = false;
      clearTimeout(this.reconnectTimer);
      clearInterval(this.heartbeatTimer);
      window.removeEventListener("focus", this.handleFocus);
      window.removeEventListener("blur", this.handleBlur);
      document.removeEventListener("visibilitychange", this.handleVisibility);
      window.removeEventListener("pointerdown", this.handleInteraction);
      this.socket?.close();
      this.socket = null;
      this.rejectPending(new Error("UI sync stopped"));
    }

    connect() {
      if (!this.started || !this.webSocketUrl) return;
      clearTimeout(this.reconnectTimer);
      const socket = new WebSocket(this.webSocketUrl);
      this.socket = socket;
      socket.addEventListener("open", () => this.onOpen(socket));
      socket.addEventListener("message", (event) => this.onMessage(socket, event));
      socket.addEventListener("close", () => this.onClose(socket));
      socket.addEventListener("error", () => socket.close());
    }

    async onOpen(socket) {
      if (socket !== this.socket) return;
      this.reconnectDelay = 750;
      try {
        await this.command({
          type: "ui.register",
          tabId: this.tabId,
          clientKind: this.clientKind,
          capabilities: this.capabilities,
          lastServerSeq: this.lastServerSeq,
        }, { requireReady: false });
        if (socket !== this.socket) return;
        this.ready = true;
        this.send({ v: 1, type: "subscribe", id: "ui-state", topic: { type: "ui-state" } });
        this.send({ v: 1, type: "subscribe", id: "resource-events", topic: { type: "resource-events" } });
        this.send({ v: 1, type: "subscribe", id: "operation-events", topic: { type: "operation-events" } });
        await this.publishPresence({
          focused: document.hasFocus(),
          visible: document.visibilityState === "visible",
          interacted: true,
        });
        if (this.lastContext) await this.command({ type: "context.replace", context: this.lastContext });
        for (const documentState of this.documents.values()) {
          await this.command({ type: "document.open", document: documentState });
        }
        clearInterval(this.heartbeatTimer);
        this.heartbeatTimer = setInterval(() => {
          void this.command({ type: "system.ping" }).catch(() => this.socket?.close());
        }, HEARTBEAT_MS);
      } catch {
        socket.close();
      }
    }

    onClose(socket) {
      if (socket !== this.socket) return;
      this.ready = false;
      clearInterval(this.heartbeatTimer);
      this.rejectPending(new Error("UI sync disconnected"));
      if (!this.started) return;
      this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, RECONNECT_MAX_MS);
    }

    async onMessage(socket, event) {
      if (socket !== this.socket) return;
      let message;
      try {
        message = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (message?.v !== PROTOCOL_VERSION) return;
      if (message.type === "ack" || message.type === "error") {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.type === "ack") pending.resolve(message.result);
        else {
          const error = new Error(message.message || message.code || "UI sync failed");
          error.code = message.code;
          error.retryable = Boolean(message.retryable);
          pending.reject(error);
        }
        return;
      }
      if (message.type === "snapshot") {
        if (message.snapshot?.type === "ui-state") {
          this.snapshot = message.snapshot.data;
          this.lastServerSeq = Number(this.snapshot?.serverSeq || 0);
          this.stateListeners.forEach((listener) => listener(this.snapshot));
        } else if (message.snapshot?.type === "resource-events") {
          message.snapshot.data.forEach((change) => this.resourceListeners.forEach((listener) => listener(change)));
        } else if (message.snapshot?.type === "operation-events") {
          message.snapshot.data.forEach((operation) => this.operationListeners.forEach((listener) => listener(operation)));
        }
        return;
      }
      if (message.type === "event") {
        this.lastServerSeq = Math.max(this.lastServerSeq, Number(message.event?.serverSeq || 0));
        if (message.event?.type === "resource.changed") {
          this.resourceListeners.forEach((listener) => listener(message.event.change));
        } else if (message.event?.type === "operation.changed") {
          this.operationListeners.forEach((listener) => listener(message.event.operation));
        }
        return;
      }
      if (message.type === "request") await this.handleDocumentRequest(message);
    }

    send(envelope) {
      if (this.socket?.readyState !== WebSocket.OPEN) throw new Error("UI sync is disconnected");
      this.socket.send(JSON.stringify(envelope));
    }

    command(command, { requireReady = true } = {}) {
      if (requireReady && !this.ready) return Promise.reject(new Error("UI sync is not ready"));
      const id = randomId("request");
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending.delete(id);
          reject(new Error("UI sync command timed out"));
        }, COMMAND_TIMEOUT_MS);
        this.pending.set(id, { resolve, reject, timer });
        try {
          this.send({ v: 1, type: "command", id, command });
        } catch (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(error);
        }
      });
    }

    rejectPending(error) {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(error);
      }
      this.pending.clear();
    }

    publishPresence(presence) {
      if (!this.ready) return Promise.resolve();
      return this.command({ type: "presence.update", ...presence }).catch(() => undefined);
    }

    publishContext(context) {
      this.lastContext = structuredClone(context);
      if (!this.ready) return Promise.resolve();
      return this.command({ type: "context.replace", context: this.lastContext });
    }

    async openDocument(documentState, provider) {
      this.documents.set(documentState.documentId, structuredClone(documentState));
      if (provider) this.documentProviders.set(documentState.documentId, provider);
      if (!this.ready) return;
      await this.command({ type: "document.open", document: documentState });
    }

    async updateDocument(documentState, baseRevision) {
      this.documents.set(documentState.documentId, structuredClone(documentState));
      if (!this.ready) return;
      await this.command({ type: "document.update", document: documentState, baseRevision });
    }

    async closeDocument(documentId) {
      this.documents.delete(documentId);
      this.documentProviders.delete(documentId);
      if (!this.ready) return;
      await this.command({ type: "document.close", documentId });
    }

    async handleDocumentRequest(message) {
      const request = message.request;
      const provider = this.documentProviders.get(request?.documentId);
      if (!provider) {
        const type = request?.type === "document.patch.request" ? "document.patch.respond" : "document.snapshot.respond";
        await this.command({
          type,
          operationId: request?.operationId || message.id,
          documentId: request?.documentId || "unknown",
          ...(type === "document.patch.respond" ? { status: "rejected" } : { revision: 0, contentDigest: "unavailable" }),
          errorCode: "document_not_open",
        }).catch(() => undefined);
        return;
      }
      try {
        if (request.type === "document.snapshot.request") {
          const result = await provider.getSnapshot({ includeContent: request.includeContent });
          await this.command({
            type: "document.snapshot.respond",
            operationId: request.operationId,
            documentId: request.documentId,
            revision: result.revision,
            contentDigest: result.contentDigest,
            ...(request.includeContent ? { content: result.content } : {}),
          });
          return;
        }
        if (request.type === "document.patch.request") {
          const result = await provider.applyPatch(request);
          await this.command({
            type: "document.patch.respond",
            operationId: request.operationId,
            documentId: request.documentId,
            status: "applied",
            revision: result.revision,
            savedRevision: result.savedRevision,
            contentDigest: result.contentDigest,
            dirty: result.dirty,
          });
        }
      } catch (error) {
        const type = request.type === "document.patch.request" ? "document.patch.respond" : "document.snapshot.respond";
        await this.command({
          type,
          operationId: request.operationId,
          documentId: request.documentId,
          ...(type === "document.patch.respond" ? { status: "rejected" } : { revision: 0, contentDigest: "unavailable" }),
          errorCode: error?.code || "revision_conflict",
          ...(type === "document.patch.respond" ? { message: error?.message || "Document request failed" } : {}),
        }).catch(() => undefined);
      }
    }

    onState(listener) {
      this.stateListeners.add(listener);
      if (this.snapshot) listener(this.snapshot);
      return () => this.stateListeners.delete(listener);
    }

    onResourceChange(listener) {
      this.resourceListeners.add(listener);
      return () => this.resourceListeners.delete(listener);
    }

    onOperationChange(listener) {
      this.operationListeners.add(listener);
      return () => this.operationListeners.delete(listener);
    }

    publishResourceChange(change) {
      if (!this.ready) return Promise.resolve();
      return this.command({ type: "resource.changed", change });
    }

    publishOperation(operation) {
      if (!this.ready) return Promise.resolve();
      return this.command({ type: "operation.changed", operation });
    }

    static async digestText(text) {
      const bytes = new TextEncoder().encode(String(text));
      if (globalThis.crypto?.subtle) {
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
      }
      return portableSha256(bytes);
    }
  }

  window.TradeUiSyncClient = UiSyncClient;
  window.TradeUiSync = new UiSyncClient();
})();
