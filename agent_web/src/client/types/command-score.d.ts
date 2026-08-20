declare module "command-score" {
  /**
   * Scores a candidate string against an abbreviation/query.
   * Returns a value in [0, 1]; 0 means no match.
   */
  export default function commandScore(candidate: string, query: string, aliases?: string[]): number
}
