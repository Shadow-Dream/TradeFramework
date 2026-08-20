import { useState } from "react"
import {
  chatModeFromFlags,
  chatModeToFlags,
  type AgentProvider,
  type ChatMode,
} from "../../../shared/types"
import { AuthCard } from "../../components/auth/AuthCard"
import { ChatPreferenceControls } from "../../components/chat-ui/ChatPreferenceControls"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select"
import { useChatPreferencesStore } from "../../stores/chatPreferencesStore"
import { useProviderAuthStore } from "../../stores/providerAuthStore"
import type { KannaState } from "../useKannaState"
import { SettingsErrorBanner, SettingsRow } from "./shared"
import { SETTINGS_ROWS } from "./registry"

const ENABLED_PROVIDERS = new Set<AgentProvider>(["claude-deepseek", "codex-openai"])

export function ProvidersSection({
  state,
}: {
  state: Pick<KannaState, "socket" | "availableProviders" | "handleWriteAppSettings">
}) {
  const providerAuthSnapshot = useProviderAuthStore((store) => store.snapshot)
  const storedDefaultProvider = useChatPreferencesStore((store) => store.defaultProvider)
  const providerDefaults = useChatPreferencesStore((store) => store.providerDefaults)
  const setDefaultProvider = useChatPreferencesStore((store) => store.setDefaultProvider)
  const setProviderDefaultModel = useChatPreferencesStore((store) => store.setProviderDefaultModel)
  const setProviderDefaultModelOptions = useChatPreferencesStore((store) => store.setProviderDefaultModelOptions)
  const setProviderDefaultMode = useChatPreferencesStore((store) => store.setProviderDefaultMode)
  const [providersError, setProvidersError] = useState<string | null>(null)
  const availableProviders = state.availableProviders.filter((provider) => ENABLED_PROVIDERS.has(provider.id))
  const defaultProvider: AgentProvider = storedDefaultProvider === "codex-openai" ? "codex-openai" : "claude-deepseek"

  function save(patch: Parameters<typeof state.handleWriteAppSettings>[0]) {
    void state.handleWriteAppSettings(patch).catch((error) => {
      setProvidersError(error instanceof Error ? error.message : "Unable to save backend settings.")
    })
  }

  function handleDefaultProviderChange(provider: AgentProvider) {
    setDefaultProvider(provider)
    save({ defaultProvider: provider })
  }

  function handleProviderDefaultModelChange(provider: AgentProvider, model: string) {
    setProviderDefaultModel(provider, model)
    save({ providerDefaults: { [provider]: { model } } })
  }

  function handleProviderDefaultModelOptionsChange(
    provider: AgentProvider,
    modelOptions: Partial<typeof providerDefaults[typeof provider]["modelOptions"]>,
  ) {
    setProviderDefaultModelOptions(provider, modelOptions)
    save({ providerDefaults: { [provider]: { modelOptions } } })
  }

  function handleProviderDefaultModeChange(provider: AgentProvider, mode: ChatMode) {
    setProviderDefaultMode(provider, mode)
    save({ providerDefaults: { [provider]: chatModeToFlags(mode, providerDefaults[provider].autoPlan) } })
  }

  return (
    <>
      {providersError ? <SettingsErrorBanner message={providersError} /> : null}
      <div className="space-y-3 pb-6">
        {providerAuthSnapshot ? (
          providerAuthSnapshot.services
            .filter((service) => service.service === "claude" || service.service === "codex")
            .map((service) => <AuthCard key={service.service} service={service} socket={state.socket} />)
        ) : (
          <div className="rounded-2xl border border-border bg-card/40 px-5 py-6 text-sm text-muted-foreground">
            Checking backend sign-in status…
          </div>
        )}
      </div>

      <div className="border-b border-border">
        <SettingsRow def={SETTINGS_ROWS.defaultProvider} bordered={false}>
          <Select value={defaultProvider} onValueChange={(value) => handleDefaultProviderChange(value as AgentProvider)}>
            <SelectTrigger className="min-w-[220px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {availableProviders.map((provider) => (
                  <SelectItem key={provider.id} value={provider.id}>{provider.label}</SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.claudeDefaults} alignStart>
          <ChatPreferenceControls
            availableProviders={availableProviders}
            selectedProvider="claude-deepseek"
            showProviderPicker={false}
            providerLocked
            model={providerDefaults["claude-deepseek"].model}
            modelOptions={providerDefaults["claude-deepseek"].modelOptions}
            onModelChange={(_, model) => handleProviderDefaultModelChange("claude-deepseek", model)}
            onModelOptionChange={(change) => {
              if (change.type === "claudeReasoningEffort") {
                handleProviderDefaultModelOptionsChange("claude-deepseek", { reasoningEffort: change.effort })
              } else if (change.type === "contextWindow") {
                handleProviderDefaultModelOptionsChange("claude-deepseek", { contextWindow: change.contextWindow })
              } else if (change.type === "fastMode") {
                handleProviderDefaultModelOptionsChange("claude-deepseek", { fastMode: change.fastMode })
              }
            }}
            mode={chatModeFromFlags(providerDefaults["claude-deepseek"].planMode, providerDefaults["claude-deepseek"].autoPlan)}
            onModeChange={(mode) => handleProviderDefaultModeChange("claude-deepseek", mode)}
            includeMode
            className="justify-start flex-wrap"
          />
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.codexDefaults} alignStart>
          <ChatPreferenceControls
            availableProviders={availableProviders}
            selectedProvider="codex-openai"
            showProviderPicker={false}
            providerLocked
            model={providerDefaults["codex-openai"].model}
            modelOptions={providerDefaults["codex-openai"].modelOptions}
            onModelChange={(_, model) => handleProviderDefaultModelChange("codex-openai", model)}
            onModelOptionChange={(change) => {
              if (change.type === "codexReasoningEffort") {
                handleProviderDefaultModelOptionsChange("codex-openai", { reasoningEffort: change.effort })
              } else if (change.type === "fastMode") {
                handleProviderDefaultModelOptionsChange("codex-openai", { fastMode: change.fastMode })
              }
            }}
            mode={chatModeFromFlags(providerDefaults["codex-openai"].planMode, providerDefaults["codex-openai"].autoPlan)}
            onModeChange={(mode) => handleProviderDefaultModeChange("codex-openai", mode)}
            includeMode
            className="justify-start flex-wrap"
          />
        </SettingsRow>
      </div>
    </>
  )
}
