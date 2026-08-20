import type { ReactNode } from "react"
import { Archive, Pencil, PencilOff, RotateCcw, Split, SquarePen, Trash2 } from "lucide-react"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "../../ui/context-menu"
import { useOpenedOnce } from "../../../hooks/useOpenedOnce"

export function ChatRowMenu({
  canFork,
  archived,
  onNewChat,
  onRename,
  onFork,
  onArchive,
  onRestore,
  onClearDraft,
  onDelete,
  children,
}: {
  canFork?: boolean
  archived?: boolean
  onNewChat: () => void
  onRename: () => void
  onFork: () => void
  onArchive: () => void
  onRestore?: () => void
  onClearDraft?: () => void
  onDelete: () => void
  children: ReactNode
}) {
  const [menuOpened, handleMenuOpenChange] = useOpenedOnce()
  return (
    <ContextMenu onOpenChange={handleMenuOpenChange}>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      {!menuOpened ? null : (
        <ContextMenuContent>
          {onClearDraft ? (
            <>
              <ContextMenuItem onSelect={(event) => { event.preventDefault(); onClearDraft() }}>
                <PencilOff className="h-3.5 w-3.5" /><span className="text-xs font-medium">Clear Draft</span>
              </ContextMenuItem>
              <ContextMenuSeparator />
            </>
          ) : null}
          {archived && onRestore ? (
            <>
              <ContextMenuItem onSelect={(event) => { event.preventDefault(); onRestore() }}>
                <RotateCcw className="h-3.5 w-3.5" /><span className="text-xs font-medium">Restore</span>
              </ContextMenuItem>
              <ContextMenuSeparator />
            </>
          ) : null}
          <ContextMenuItem onSelect={(event) => { event.preventDefault(); onRename() }}>
            <Pencil className="h-3.5 w-3.5" /><span className="text-xs font-medium">Rename</span>
          </ContextMenuItem>
          <ContextMenuItem disabled={!canFork} onSelect={(event) => { event.preventDefault(); if (canFork) onFork() }}>
            <Split className="h-3.5 w-3.5" /><span className="text-xs font-medium">Fork Session</span>
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem onSelect={(event) => { event.preventDefault(); onNewChat() }}>
            <SquarePen className="h-3.5 w-3.5" /><span className="text-xs font-medium">New Session in Project</span>
          </ContextMenuItem>
          <ContextMenuSeparator />
          {!archived ? (
            <ContextMenuItem onSelect={(event) => { event.preventDefault(); onArchive() }}>
              <Archive className="h-3.5 w-3.5" /><span className="text-xs font-medium">Archive Session</span>
            </ContextMenuItem>
          ) : null}
          <ContextMenuItem className="text-destructive focus:text-destructive" onSelect={(event) => { event.preventDefault(); onDelete() }}>
            <Trash2 className="h-3.5 w-3.5" /><span className="text-xs font-medium">Delete Session</span>
          </ContextMenuItem>
        </ContextMenuContent>
      )}
    </ContextMenu>
  )
}
