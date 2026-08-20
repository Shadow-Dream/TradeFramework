import { createContext, useContext, type ReactNode } from "react"

export interface TradeAccount {
  userId: string
  email: string
  role: "admin" | "user"
  expiresAt: string
}

export interface TradeAuthContextValue {
  account: TradeAccount
  tradeEngineUrl: string
  returnUrl: string
  build: string
}

const TradeAuthContext = createContext<TradeAuthContextValue | null>(null)

export function TradeAuthProvider({ value, children }: { value: TradeAuthContextValue; children: ReactNode }) {
  return <TradeAuthContext.Provider value={value}>{children}</TradeAuthContext.Provider>
}

export function useTradeAuth(): TradeAuthContextValue {
  const value = useContext(TradeAuthContext)
  if (!value) throw new Error("TradeAuthProvider is missing.")
  return value
}

export function useOptionalTradeAuth(): TradeAuthContextValue | null {
  return useContext(TradeAuthContext)
}

export function sanitizeTradeReturnPath(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/"
  return value
}
