/**
 * Copy text to the clipboard, with a fallback for the async clipboard API.
 *
 * iOS home-screen PWAs reject `navigator.clipboard.writeText` when the call
 * does not come straight from a user gesture, and the promise rejects rather
 * than resolving false. The legacy selection path still works there, so it
 * stays as the backup.
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through to the selection path below.
  }

  const helper = document.createElement("textarea")
  helper.value = text
  helper.setAttribute("readonly", "")
  helper.style.position = "fixed"
  helper.style.top = "0"
  helper.style.left = "0"
  helper.style.opacity = "0"
  document.body.appendChild(helper)

  try {
    helper.focus()
    helper.select()
    helper.setSelectionRange(0, text.length)
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    helper.remove()
  }
}
