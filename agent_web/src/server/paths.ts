import path from "node:path"

/** Internal-only upload location beneath a server-approved Project cwd. */
export function getProjectUploadDir(projectPath: string) {
  return path.join(path.resolve(projectPath), ".trade-agent", "uploads")
}
