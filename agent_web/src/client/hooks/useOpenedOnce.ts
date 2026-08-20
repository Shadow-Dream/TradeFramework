import { useCallback, useState } from "react"

/**
 * Latch for a floating surface that should not be built until it has been asked
 * for once — a sidebar row's context menu, a sidebar row's hover card.
 *
 * Radix portals both and renders neither while closed, but their *children* are
 * still constructed on every render of the row that owns them: ten menu items
 * with icons, or a whole hover card body. Across a sidebar of chats that is a
 * few thousand wasted element allocations per second during a turn, for menus
 * nobody opened.
 *
 * Latching on — rather than tracking the live open state — is deliberate. It
 * keeps the exit animation: the content stays mounted after the first open, so
 * Radix still has something to animate away when it closes.
 *
 * Returns the latch and an `onOpenChange` handler to hand straight to the Radix
 * root. The content mounts in the same commit that opens it.
 */
export function useOpenedOnce(): [boolean, (open: boolean) => void] {
  const [opened, setOpened] = useState(false)
  const handleOpenChange = useCallback((open: boolean) => {
    if (open) setOpened(true)
  }, [])
  return [opened, handleOpenChange]
}
