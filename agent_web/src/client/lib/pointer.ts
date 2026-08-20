import { useEffect, useState } from "react"

/**
 * Whether the device has a hover-capable, precise pointer.
 *
 * Anything driven entirely by hover — the sidebar's chat hover card, the
 * transcript minimap — has nothing to offer on touch, where "hover" is a tap
 * that also does something else. Gate on this rather than on a breakpoint: a
 * narrow desktop window still has a mouse, and a large tablet still doesn't.
 *
 * Starts `false` so the first paint (and SSR) is the no-hover reading, then
 * upgrades in an effect — the safe direction, since the fallback is simply not
 * rendering an affordance that couldn't be used yet.
 */
export function useHasFinePointer() {
  const [hasFinePointer, setHasFinePointer] = useState(false)

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return
    const query = window.matchMedia("(hover: hover) and (pointer: fine)")
    const update = () => setHasFinePointer(query.matches)
    update()
    query.addEventListener("change", update)
    return () => query.removeEventListener("change", update)
  }, [])

  return hasFinePointer
}
