import type { JupyterFrontEnd, JupyterFrontEndPlugin } from "@jupyterlab/application"
import type { DocumentRegistry, DocumentWidget } from "@jupyterlab/docregistry"
import { DisposableDelegate, type IDisposable } from "@lumino/disposable"

type UiDocumentKind = "jupyter-text" | "jupyter-notebook"

interface DocumentState {
  documentId: string
  kind: UiDocumentKind
  label: string
  workspaceId: string
  relativePath: string
  language?: string
  revision: number
  savedRevision: number
  contentDigest: string
  dirty: boolean
  readOnly: boolean
}

interface UiSyncClient {
  tabId: string
  start(): Promise<void>
  stop(): void
  publishContext(context: unknown): Promise<unknown>
  openDocument(state: DocumentState, provider: unknown): Promise<void>
  updateDocument(state: DocumentState, baseRevision: number): Promise<void>
  closeDocument(documentId: string): Promise<void>
}

interface UiSyncClientConstructor {
  new(options: { clientKind: "jupyter"; tabIdKey: string; tabIdPrefix: string; capabilities: string[] }): UiSyncClient
  digestText(text: string): Promise<string>
}

declare global {
  interface Window {
    TradeUiSyncClient?: UiSyncClientConstructor
  }
}

const CLIENT_SCRIPT = "/ui_sync.js"
const TEXT_FACTORIES = ["Editor"]
const NOTEBOOK_FACTORIES = ["Notebook"]

function workspaceIdFromLocation() {
  const match = location.pathname.match(/\/jupyter\/w\/([^/]+)\//)
  return match?.[1] ? decodeURIComponent(match[1]) : "managed-workspace"
}

function normalizedRelativePath(path: string) {
  const normalized = String(path || "untitled").replaceAll("\\", "/").replace(/^\/+/, "")
  if (!normalized || normalized.split("/").some((part) => !part || part === "..")) {
    throw new Error("Jupyter document path is not a safe workspace-relative path.")
  }
  return normalized.slice(0, 512)
}

function hashLogicalPath(value: string) {
  const seeds = [0x811c9dc5, 0x9e3779b9, 0x85ebca6b, 0xc2b2ae35]
  return seeds.map((seed) => {
    let hash = seed >>> 0
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index)
      hash = Math.imul(hash, 0x01000193) >>> 0
    }
    return hash.toString(16).padStart(8, "0")
  }).join("")
}

function documentId(workspaceId: string, relativePath: string) {
  return `jupyter:${workspaceId}:${hashLogicalPath(relativePath)}`
}

async function loadUiSyncClient(): Promise<UiSyncClientConstructor> {
  if (window.TradeUiSyncClient) return window.TradeUiSyncClient
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script")
    script.src = CLIENT_SCRIPT
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error("TradeEngine UI sync client failed to load."))
    document.head.appendChild(script)
  })
  if (!window.TradeUiSyncClient) throw new Error("TradeEngine UI sync client is unavailable.")
  return window.TradeUiSyncClient
}

function languageForPath(relativePath: string) {
  const extension = relativePath.split(".").pop()?.toLowerCase() ?? ""
  const languages: Record<string, string> = {
    py: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
    ts: "typescript", tsx: "typescript", jsx: "javascript", json: "json",
    md: "markdown", yaml: "yaml", yml: "yaml", toml: "toml", sh: "shell",
    sql: "sql", css: "css", html: "html", txt: "text",
  }
  return languages[extension]
}

class ManagedDocument {
  private state: DocumentState | null = null
  private content = ""
  private changeTimer = 0
  private updateChain = Promise.resolve()
  private suppressChange = false
  private suppressSave = false
  private disposed = false

  constructor(
    private readonly widget: DocumentWidget,
    private readonly context: DocumentRegistry.IContext<DocumentRegistry.IModel>,
    private readonly client: UiSyncClient,
    private readonly clientClass: UiSyncClientConstructor,
    private readonly workspaceId: string,
    private readonly writable: boolean,
    private readonly publishActiveContext: () => void,
  ) {}

  async initialize() {
    await this.context.ready
    if (this.disposed) return
    await this.openCurrentPath()
    this.context.model.contentChanged.connect(this.onContentChanged)
    this.context.saveState.connect(this.onSaveState)
    this.context.pathChanged.connect(this.onPathChanged)
  }

  get widgetId() {
    return this.widget.id
  }

  get currentState() {
    return this.state
  }

  private publicState(): DocumentState {
    if (!this.state) throw new Error("Jupyter document is not initialized.")
    return { ...this.state }
  }

  private async openCurrentPath() {
    const relativePath = normalizedRelativePath(this.context.path)
    const content = this.context.model.toString()
    const digest = await this.clientClass.digestText(content)
    const oldDocumentId = this.state?.documentId
    this.content = content
    this.state = {
      documentId: documentId(this.workspaceId, relativePath),
      kind: this.writable ? "jupyter-text" : "jupyter-notebook",
      label: relativePath.split("/").pop() || relativePath,
      workspaceId: this.workspaceId,
      relativePath,
      ...(this.writable && languageForPath(relativePath) ? { language: languageForPath(relativePath) } : {}),
      revision: 0,
      savedRevision: 0,
      contentDigest: digest,
      dirty: Boolean(this.context.model.dirty),
      readOnly: !this.writable,
    }
    if (oldDocumentId && oldDocumentId !== this.state.documentId) {
      await this.client.closeDocument(oldDocumentId).catch(() => undefined)
    }
    await this.client.openDocument(this.publicState(), {
      getSnapshot: ({ includeContent }: { includeContent: boolean }) => this.getSnapshot(includeContent),
      applyPatch: (request: any) => this.applyPatch(request),
    })
    this.publishActiveContext()
  }

  private onContentChanged = () => {
    if (this.suppressChange || this.disposed || !this.state) return
    window.clearTimeout(this.changeTimer)
    this.changeTimer = window.setTimeout(() => { void this.flushLocalChange() }, 80)
  }

  private onSaveState = (_sender: unknown, state: string) => {
    if (this.suppressSave || this.disposed || state !== "completed") return
    void this.flushLocalChange().then(async () => {
      if (!this.state || this.disposed) return
      const baseRevision = this.state.revision
      this.state.revision += 1
      this.state.savedRevision = this.state.revision
      this.state.dirty = false
      await this.client.updateDocument(this.publicState(), baseRevision)
      this.publishActiveContext()
    }).catch(() => undefined)
  }

  private onPathChanged = () => {
    void this.openCurrentPath().catch(() => undefined)
  }

  private flushLocalChange() {
    window.clearTimeout(this.changeTimer)
    this.updateChain = this.updateChain.then(async () => {
      if (!this.state || this.disposed) return
      const content = this.context.model.toString()
      if (content === this.content) return
      const digest = await this.clientClass.digestText(content)
      if (!this.state || this.disposed) return
      const baseRevision = this.state.revision
      this.state.revision += 1
      this.state.contentDigest = digest
      this.state.dirty = true
      this.content = content
      await this.client.updateDocument(this.publicState(), baseRevision)
      this.publishActiveContext()
    })
    return this.updateChain
  }

  private async getSnapshot(includeContent: boolean) {
    if (!this.writable) {
      const error = new Error("Notebook content mutation is intentionally disabled in UI sync.") as Error & { code?: string }
      error.code = "unsupported_document_type"
      throw error
    }
    await this.flushLocalChange()
    if (!this.state) throw new Error("Jupyter document closed.")
    return {
      revision: this.state.revision,
      contentDigest: this.state.contentDigest,
      ...(includeContent ? { content: this.content } : {}),
    }
  }

  private async applyPatch(request: any) {
    if (!this.writable || !this.state) {
      const error = new Error("This Jupyter document is read-only for UI sync.") as Error & { code?: string }
      error.code = "read_only"
      throw error
    }
    await this.flushLocalChange()
    if (request.baseRevision !== this.state.revision || request.baseDigest !== this.state.contentDigest) {
      const error = new Error("The Jupyter editor changed; read it again before applying a patch.") as Error & { code?: string }
      error.code = "revision_conflict"
      throw error
    }
    const { start, end, text } = request.patch ?? {}
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start
        || end > this.content.length || typeof text !== "string") {
      const error = new Error("The Jupyter text replacement range is invalid.") as Error & { code?: string }
      error.code = "invalid_patch"
      throw error
    }
    const content = `${this.content.slice(0, start)}${text}${this.content.slice(end)}`
    const digest = await this.clientClass.digestText(content)
    if (request.baseRevision !== this.state.revision || request.baseDigest !== this.state.contentDigest) {
      const error = new Error("The Jupyter editor changed while applying the patch.") as Error & { code?: string }
      error.code = "revision_conflict"
      throw error
    }
    this.suppressChange = true
    try {
      this.context.model.fromString(content)
    } finally {
      this.suppressChange = false
    }
    this.state.revision += 1
    this.state.contentDigest = digest
    this.state.dirty = true
    this.content = content
    if (request.save === true) {
      this.suppressSave = true
      try {
        await this.context.save()
      } finally {
        this.suppressSave = false
      }
      this.state.savedRevision = this.state.revision
      this.state.dirty = false
    }
    this.publishActiveContext()
    return {
      revision: this.state.revision,
      savedRevision: this.state.savedRevision,
      contentDigest: this.state.contentDigest,
      dirty: this.state.dirty,
    }
  }

  async dispose() {
    if (this.disposed) return
    this.disposed = true
    window.clearTimeout(this.changeTimer)
    this.context.model.contentChanged.disconnect(this.onContentChanged)
    this.context.saveState.disconnect(this.onSaveState)
    this.context.pathChanged.disconnect(this.onPathChanged)
    if (this.state) await this.client.closeDocument(this.state.documentId).catch(() => undefined)
  }
}

class WidgetExtension implements DocumentRegistry.IWidgetExtension<DocumentWidget, DocumentRegistry.IModel> {
  constructor(private readonly create: (widget: DocumentWidget, context: DocumentRegistry.IContext<DocumentRegistry.IModel>) => IDisposable) {}

  createNew(widget: DocumentWidget, context: DocumentRegistry.IContext<DocumentRegistry.IModel>): IDisposable {
    return this.create(widget, context)
  }
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: "@trade-engine/jupyter-ui-sync:plugin",
  autoStart: true,
  activate: async (app: JupyterFrontEnd) => {
    const Client = await loadUiSyncClient()
    const workspaceId = workspaceIdFromLocation()
    const client = new Client({
      clientKind: "jupyter",
      tabIdKey: `trade.ui-sync.jupyter-tab.v1:${workspaceId}`,
      tabIdPrefix: "jupyter-tab",
      capabilities: ["presence", "context", "document-read", "document-write"],
    })
    await client.start()
    const documents = new Map<string, ManagedDocument>()

    const publishActiveContext = () => {
      const current = app.shell.currentWidget
      const managed = current ? documents.get(current.id) : undefined
      const state = managed?.currentState
      void client.publishContext({
        route: `${location.pathname}${location.search}`,
        view: "jupyter",
        subview: state?.kind === "jupyter-notebook" ? "notebook" : state ? "text-editor" : "workspace",
        projectId: workspaceId,
        resourceRefs: state ? [{ kind: "workspace-document", id: state.documentId, label: state.label }] : [],
        ...(state ? {
          selection: { kind: "workspace-document", id: state.documentId, label: state.label },
          documentId: state.documentId,
          documentRevision: state.revision,
        } : {}),
      }).catch(() => undefined)
    }

    const attach = (writable: boolean) => (widget: DocumentWidget, context: DocumentRegistry.IContext<DocumentRegistry.IModel>) => {
      const managed = new ManagedDocument(widget, context, client, Client, workspaceId, writable, publishActiveContext)
      documents.set(widget.id, managed)
      void managed.initialize().catch(() => undefined)
      return new DisposableDelegate(() => {
        documents.delete(widget.id)
        void managed.dispose()
        publishActiveContext()
      })
    }

    for (const factory of TEXT_FACTORIES) app.docRegistry.addWidgetExtension(factory, new WidgetExtension(attach(true)))
    for (const factory of NOTEBOOK_FACTORIES) app.docRegistry.addWidgetExtension(factory, new WidgetExtension(attach(false)))
    app.shell.currentChanged?.connect(publishActiveContext)
    publishActiveContext()
  },
}

export default plugin
