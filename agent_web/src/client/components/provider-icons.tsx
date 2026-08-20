import type { ComponentType, SVGProps } from "react"
import type { AgentProvider, AuthServiceId } from "../../shared/types"
import { cn } from "../lib/utils"

export type IconComponent = ComponentType<SVGProps<SVGSVGElement>>

export function AnthropicIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={cn("shrink-0", className)} {...props}>
      <path d="M4.2 19.5 10.4 4.5h3.2l6.2 15h-3.6l-1.4-3.7H9.1l-1.4 3.7H4.2Zm6-6.6h3.5L12 8.4l-1.8 4.5Z" fill="currentColor" />
    </svg>
  )
}

export function OpenAIIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={cn("shrink-0", className)} {...props}>
      <path d="M12 3.1a4.6 4.6 0 0 1 4.4 3.2 4.6 4.6 0 0 1 2.1 7.6 4.6 4.6 0 0 1-4.4 6.3 4.6 4.6 0 0 1-7.6-2.1 4.6 4.6 0 0 1-.9-8.8A4.6 4.6 0 0 1 12 3.1Zm0 3-4.9 2.8v5.7l4.9 2.8 4.9-2.8V8.9L12 6.1Z" fill="currentColor" />
    </svg>
  )
}

export const PROVIDER_ICONS: Record<AgentProvider, IconComponent> = {
  "claude-deepseek": AnthropicIcon,
  "codex-openai": OpenAIIcon,
}

export const AUTH_SERVICE_ICONS: Record<AuthServiceId, IconComponent> = {
  claude: AnthropicIcon,
  codex: OpenAIIcon,
}
