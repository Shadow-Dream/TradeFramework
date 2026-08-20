import * as React from "react"
import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
} from "@shadcn/react/message-scroller"

import { cn } from "../../lib/utils"

/**
 * Vendored from the shadcn registry (`message-scroller`), adapted for this repo:
 * our `cn`, and without the registry's `MessageScrollerButton` — the transcript
 * keeps its own scroll-to-bottom control, which carries app styling and marks
 * the jump as a deliberate read-position change. Registry utility classes that
 * do not exist in our Tailwind setup (`scroll-fade-b`, `scrollbar-*`) are
 * dropped rather than vendored as no-ops; the call site supplies its own.
 *
 * The behaviour we rely on lives in the primitive: scroll anchoring across
 * content growth, follow-the-end while the reader is at the end, and
 * `content-visibility: auto` on items so offscreen rows cost no layout or
 * paint while staying in the DOM (and therefore addressable, selectable, and
 * findable with the browser's own find-in-page).
 */

function MessageScrollerProvider(
  props: React.ComponentProps<typeof MessageScrollerPrimitive.Provider>
) {
  return <MessageScrollerPrimitive.Provider {...props} />
}

function MessageScroller({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Root>) {
  return (
    <MessageScrollerPrimitive.Root
      data-slot="message-scroller"
      className={cn(
        "group/message-scroller relative flex size-full min-h-0 flex-col overflow-hidden",
        className
      )}
      {...props}
    />
  )
}

function MessageScrollerViewport({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Viewport>) {
  return (
    <MessageScrollerPrimitive.Viewport
      data-slot="message-scroller-viewport"
      className={cn("size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain", className)}
      {...props}
    />
  )
}

function MessageScrollerContent({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Content>) {
  return (
    <MessageScrollerPrimitive.Content
      data-slot="message-scroller-content"
      className={cn("flex h-max min-h-full flex-col", className)}
      // The registry's trailing spacer, held at zero height.
      //
      // The primitive grows it on every `scrollToElement` by exactly the
      // shortfall between the scroll position asked for and the furthest the
      // content can actually scroll — buying the range to park a near-final
      // message at the top of the viewport. The cost is a stretch of empty
      // transcript below the last message that reads as overscroll, and that
      // outlives the jump: it is only recomputed on the next programmatic
      // scroll, so it sits there through everything the reader does by hand.
      //
      // Not worth what it buys. A jump near the end of a chat now scrolls as
      // far as the content allows and the message lands wherever that leaves
      // it, which is what every other transcript does — and the jump flash
      // already says which message it was, so nothing depends on the message
      // being at a fixed height on screen.
      //
      // `display: none` rather than a zero height because the primitive writes
      // `height` inline and would win any contest over that property. It sets
      // `hidden` too, but only ever toggles it off, so the class is what holds.
      spacerClassName="hidden"
      {...props}
    />
  )
}

function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Item>) {
  return (
    <MessageScrollerPrimitive.Item
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      // `contain-intrinsic-size` is the placeholder height an offscreen row
      // reports. The `auto` keyword makes the browser remember each row's real
      // size once rendered, so this default only governs rows never yet seen —
      // call sites override it per row with a kind-aware estimate.
      className={cn(
        "min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]",
        className
      )}
      {...props}
    />
  )
}

export {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
}
