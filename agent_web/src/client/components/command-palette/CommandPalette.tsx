import { Bot, Boxes, MessageSquare, Settings } from "lucide-react"
import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import type { KannaState } from "../../app/useKannaState"
import { useSidebarData } from "../../stores/sidebarStore"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "../ui/command"

export const OPEN_COMMAND_PALETTE_EVENT = "kanna:open-command-palette"

export type CommandPaletteTargetPage = "new-thread" | "project-chats"

export function openCommandPalette(page?: CommandPaletteTargetPage) {
  window.dispatchEvent(new CustomEvent(OPEN_COMMAND_PALETTE_EVENT, {
    detail: page ? { page } : undefined,
  }))
}

/**
 * TradeEngine's palette deliberately exposes logical Projects and existing
 * Sessions only. It never accepts a host path, repository URL, or directory
 * selection from the browser.
 */
export function CommandPalette({ state }: { state: KannaState }) {
  const navigate = useNavigate()
  const projects = useSidebarData().projectGroups
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, show)
    return () => window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, show)
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setOpen((current) => !current)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  const select = (run: () => void) => {
    setOpen(false)
    run()
  }

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Search Projects and Agent Sessions…" />
      <CommandList>
        <CommandEmpty>No matching Project or Session.</CommandEmpty>
        <CommandGroup heading="New Agent Session">
          {projects.map((project) => {
            const Icon = project.kind === "strategy" ? Boxes : Bot
            return (
              <CommandItem
                key={`new:${project.groupKey}`}
                value={`new agent session ${project.title} ${project.groupKey}`}
                onSelect={() => select(() => void state.handleCreateChat(project.groupKey))}
              >
                <Icon className="mr-2 h-4 w-4" />
                <span>{project.title}</span>
                <span className="ml-auto text-xs text-muted-foreground">New session</span>
              </CommandItem>
            )
          })}
        </CommandGroup>
        <CommandGroup heading="Agent Sessions">
          {projects.flatMap((project) => project.chats.map((chat) => (
            <CommandItem
              key={chat.chatId}
              value={`${chat.title} ${project.title} ${chat.provider ?? ""} ${chat.model ?? ""}`}
              onSelect={() => select(() => navigate(`/chat/${chat.chatId}`))}
            >
              <MessageSquare className="mr-2 h-4 w-4" />
              <span className="truncate">{chat.title}</span>
              <span className="ml-auto truncate pl-3 text-xs text-muted-foreground">{project.title}</span>
            </CommandItem>
          )))}
        </CommandGroup>
        <CommandGroup heading="Navigation">
          <CommandItem value="backends models settings" onSelect={() => select(() => navigate("/settings/providers"))}>
            <Settings className="mr-2 h-4 w-4" />
            Backends &amp; Models
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
