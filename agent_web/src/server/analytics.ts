export interface AnalyticsReporter {
  track: (eventName: string, properties?: Record<string, unknown>) => void
}

export const NoopAnalyticsReporter: AnalyticsReporter = {
  track: () => {},
}
