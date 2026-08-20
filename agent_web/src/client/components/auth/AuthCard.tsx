import { useState } from "react"
import { ArrowUpRight, Check, Loader2 } from "lucide-react"
import type { AuthServiceSnapshot } from "../../../shared/types"
import type { KannaSocket } from "../../app/socket"
import { cn } from "../../lib/utils"
import { AnthropicIcon, OpenAIIcon } from "../provider-icons"
import { Button } from "../ui/button"
import { CopyButton } from "../ui/copy-button"
import { Input } from "../ui/input"

/** "2.1.218" → "v2.1.218"; calendar/otherwise-shaped versions pass through. */
function displayVersion(version: string | null): string | null {
  if (!version) return null
  return /^\d/.test(version) && !version.startsWith("v") ? `v${version}` : version
}

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function ActionButton({
  children,
  onClick,
  busy = false,
}: {
  children: React.ReactNode
  onClick: () => void
  busy?: boolean
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      onClick={onClick}
      className="h-7 shrink-0 rounded-full px-3 text-xs font-semibold"
    >
      {busy ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : null}
      {children}
    </Button>
  )
}

export function LoginFlowPanel({
  service,
  socket,
}: {
  service: AuthServiceSnapshot
  socket: KannaSocket
}) {
  const login = service.login

  if (login.phase === "idle") return null

  const retry = () => {
    void socket.command({ type: "auth.login.start", service: service.service }).catch(() => undefined)
  }

  if (login.phase === "starting" || login.phase === "finishing") {
    return (
      <div className="mt-3 flex items-center gap-2 border-t border-border pt-3 text-sm text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span className="flex-1">{login.phase === "starting" ? "Starting sign-in…" : "Finishing sign-in…"}</span>
      </div>
    )
  }

  if (login.phase === "waiting_for_approval") {
    return (
      <div className="mt-3 space-y-2.5 border-t border-border pt-3">
        {login.userCode ? (
          <>
            <div className="text-sm text-muted-foreground">
              Enter this code at{" "}
              <a
                href={login.verificationUrl}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-foreground underline underline-offset-2"
              >
                {hostOf(login.verificationUrl)}
              </a>
            </div>
            <div className="flex items-center gap-2">
              <span className="rounded-lg border border-border bg-muted px-3 py-1.5 font-mono text-base font-semibold tracking-widest text-foreground">
                {login.userCode}
              </span>
              <CopyButton text={login.userCode} title="Copy code" className="h-7 w-7" />
              <a
                href={login.verificationUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-sm font-medium text-foreground underline-offset-2 hover:underline"
              >
                Open <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
            </div>
          </>
        ) : (
          <div className="text-sm text-muted-foreground">
            <a
              href={login.verificationUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-2"
            >
              Open {hostOf(login.verificationUrl)} <ArrowUpRight className="h-3.5 w-3.5" />
            </a>{" "}
            and approve the sign-in.
          </div>
        )}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          <span className="flex-1">Waiting for approval…</span>
        </div>
      </div>
    )
  }

  // error
  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3">
      <div className="text-sm text-destructive">{login.message}</div>
      {login.hint ? <div className="text-xs text-muted-foreground">{login.hint}</div> : null}
      <div className="flex items-center gap-3">
        <ActionButton onClick={retry}>Try Again</ActionButton>
      </div>
    </div>
  )
}

/**
 * Provider auth card, following the usage-card design:
 * `[ icon Service v1.2.3  <spacer>  Log In | Update to v1.2.4 | account ]`
 * with the live sign-in flow rendered inline below the header.
 */
export function AuthCard({
  service,
  socket,
  className,
}: {
  service: AuthServiceSnapshot
  socket: KannaSocket
  className?: string
}) {
  const Icon = service.service === "claude" ? AnthropicIcon : OpenAIIcon
  const version = displayVersion(service.version)
  const loginActive = service.login.phase !== "idle"
  const deepSeek = service.service === "claude"
  const [editingDeepSeekKey, setEditingDeepSeekKey] = useState(false)
  const [deepSeekApiKey, setDeepSeekApiKey] = useState("")
  const [savingDeepSeekKey, setSavingDeepSeekKey] = useState(false)
  const [deepSeekError, setDeepSeekError] = useState<string | null>(null)

  const startLogin = () => {
    void socket.command({ type: "auth.login.start", service: service.service }).catch(() => undefined)
  }
  const cancelLogin = () => {
    void socket.command({ type: "auth.login.cancel", service: service.service }).catch(() => undefined)
  }
  const saveDeepSeekKey = async () => {
    const apiKey = deepSeekApiKey.trim()
    if (!apiKey || savingDeepSeekKey) return
    setSavingDeepSeekKey(true)
    setDeepSeekError(null)
    try {
      await socket.command({ type: "auth.claude.deepseek.setApiKey", apiKey })
      setDeepSeekApiKey("")
      setEditingDeepSeekKey(false)
    } catch (error) {
      setDeepSeekApiKey("")
      setDeepSeekError(error instanceof Error ? error.message : String(error))
    } finally {
      setSavingDeepSeekKey(false)
    }
  }

  let action: React.ReactNode = null
  if (loginActive) {
    // While a sign-in flow runs (or errored), the header action is Cancel —
    // it kills the flow (and clears an error state) and restores Log In.
    action = (
      <button
        type="button"
        onClick={cancelLogin}
        className="shrink-0 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        Cancel
      </button>
    )
  } else if (!service.installed || service.authStatus === "not_installed") {
    action = <span className="text-xs text-destructive">Runtime unavailable</span>
  } else if (service.authStatus === "signed_out" && !loginActive && deepSeek) {
    action = (
      <ActionButton onClick={() => setEditingDeepSeekKey(true)}>
        Connect
      </ActionButton>
    )
  } else if (service.authStatus === "signed_out" && !loginActive) {
    action = (
      <ActionButton onClick={startLogin}>
        Log In
      </ActionButton>
    )
  } else if (service.authStatus === "signed_in" && deepSeek) {
    action = (
      <div className="flex shrink-0 items-center gap-2">
        <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
        <button
          type="button"
          onClick={() => setEditingDeepSeekKey((current) => !current)}
          className="text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          Replace key
        </button>
      </div>
    )
  } else if (service.authStatus === "signed_in") {
    action = (
      <span className="flex shrink-0 items-center pr-2" title={service.account ?? "Connected"}>
        <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
      </span>
    )
  } else if (service.authStatus === "outdated") {
    action = <span className="text-xs text-destructive">Runtime version is unsupported</span>
  } else if (service.authStatus === "error" && service.installed) {
    action = <span className="text-xs text-destructive">Runtime check failed</span>
  } else if (service.authStatus === "unknown") {
    action = <span className="shrink-0 text-xs text-muted-foreground">Checking…</span>
  }

  return (
    <div className={cn("rounded-2xl border border-border bg-card/40 px-3.5 py-3 text-left", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <Icon className="h-4 w-4 shrink-0 text-foreground" />
          <span className="truncate text-sm font-semibold text-foreground">{service.label}</span>
          {version ? (
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{version}</span>
          ) : null}
        </div>
        {action}
      </div>
      {service.installState === "error" && service.installError ? (
        <div className="mt-2 text-xs text-destructive">{service.installError}</div>
      ) : null}
      {(service.authStatus === "error" || service.authStatus === "outdated") && service.statusDetail ? (
        <div className="mt-2 text-xs text-muted-foreground">{service.statusDetail}</div>
      ) : null}
      {deepSeek && service.authStatus === "signed_in" && service.account ? (
        <div className="mt-2 text-xs text-muted-foreground">{service.account}</div>
      ) : null}
      {deepSeek && (editingDeepSeekKey || service.authStatus === "signed_out") ? (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          <div className="text-xs text-muted-foreground">
            DeepSeek API key. It is stored server-side and is never returned to the browser.
          </div>
          <div className="flex items-center gap-2">
            <Input
              type="password"
              autoComplete="off"
              value={deepSeekApiKey}
              onChange={(event) => setDeepSeekApiKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void saveDeepSeekKey()
              }}
              placeholder="DeepSeek API key"
              disabled={savingDeepSeekKey}
              className="h-8 flex-1 font-mono text-sm"
            />
            <ActionButton onClick={() => void saveDeepSeekKey()} busy={savingDeepSeekKey}>
              Save
            </ActionButton>
          </div>
          {deepSeekError ? <div className="text-xs text-destructive">{deepSeekError}</div> : null}
        </div>
      ) : null}
      <LoginFlowPanel service={service} socket={socket} />
    </div>
  )
}
