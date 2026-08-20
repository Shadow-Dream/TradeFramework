import { useEffect, type RefObject } from "react"

/**
 * Publishes the width of `scrollRef`'s scrollbar gutter as a CSS custom
 * property on `hostRef`, so overlays inside the host can stop short of it.
 *
 * A native scrollbar has no place in the stacking order: it is painted with its
 * scroll container, above that container's own content but below any
 * later-painted positioned sibling. `z-index` on `::-webkit-scrollbar` does
 * nothing, so an overlay spanning the full width of a scroll area — a navbar
 * wash, a composer gradient — dims the scrollbar with no way to raise it back.
 *
 * Ending those overlays at the gutter costs nothing visually: the gutter lies
 * outside the scroll container's content box, so no content is ever underneath
 * the strip being given up, and a `from-background` wash composited over bare
 * background is invisible anyway.
 *
 * The width has to be measured rather than assumed. It follows our
 * `::-webkit-scrollbar` rule in Chromium and `scrollbar-width: thin` in
 * Firefox, and is 0 wherever scrollbars overlay the content (iOS, macOS
 * trackpad default) or the content simply doesn't overflow — in which case the
 * var is 0 and the overlays span the full width again with no special-casing.
 */
export function useScrollbarGutterVar(
  scrollRef: RefObject<HTMLElement | null>,
  hostRef: RefObject<HTMLElement | null> | undefined,
  varName: string
): void {
  // Passive, not layout: the host is typically an ancestor of the component
  // measuring, and React attaches host refs bottom-up during the layout pass —
  // a descendant's layout effect runs while the ancestor's ref is still null.
  // A layout effect here reads that null, and with only refs in its deps it
  // never runs again, so the var silently never exists. Passive effects run
  // after the whole commit, when every ref is attached.
  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller) return

    // Re-read on every sync rather than closing over it, so a host that
    // attaches later still gets written to instead of being missed forever.
    let host: HTMLElement | null = null
    let published = -1
    const sync = () => {
      const nextHost = hostRef?.current ?? null
      const width = Math.max(0, scroller.offsetWidth - scroller.clientWidth)
      if (nextHost === host && width === published) return
      if (host && host !== nextHost) host.style.removeProperty(varName)
      host = nextHost
      published = width
      host?.style.setProperty(varName, `${width}px`)
    }

    sync()
    // Content-box observation is exactly the signal we want and nothing more:
    // a gutter appearing or disappearing shrinks or grows the content box by
    // its own width. Content growing past the viewport height is the same
    // event, seen from the other side, so there is nothing else to watch.
    const observer = new ResizeObserver(sync)
    observer.observe(scroller)
    return () => {
      observer.disconnect()
      host?.style.removeProperty(varName)
    }
  }, [scrollRef, hostRef, varName])
}
