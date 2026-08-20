import type { AgentProvider } from "../../../shared/types"
import { authServiceForProvider } from "../../../shared/types"
import { useProviderAuthStore, selectAuthService } from "../../stores/providerAuthStore"
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../ui/dialog"
import { AuthCard } from "./AuthCard"

/**
 * Shown when the user picks a harness that isn't signed in. The switch is
 * blocked until sign-in completes — the caller watches the auth store and
 * applies the pending switch (closing this dialog) once the service flips to
 * signed_in.
 */
export function SignInDialog({
  provider,
  onOpenChange,
}: {
  /** The harness the user tried to switch to, or null when closed. */
  provider: AgentProvider | null
  onOpenChange: (open: boolean) => void
}) {
  const snapshot = useProviderAuthStore((store) => store.snapshot)
  const socket = useProviderAuthStore((store) => store.socket)
  const serviceId = provider ? authServiceForProvider(provider) : null
  const service = serviceId ? selectAuthService(snapshot, serviceId) : null

  return (
    <Dialog open={Boolean(provider && service && socket)} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Sign in to {service?.label ?? ""}</DialogTitle>
          <DialogDescription>
            {service && !service.installed
              ? `Install and sign in to ${service.label} to use it for this chat.`
              : `Sign in to ${service?.label ?? "this provider"} to use it for this chat. The switch applies automatically once you're signed in.`}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {service && socket ? <AuthCard service={service} socket={socket} /> : null}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
