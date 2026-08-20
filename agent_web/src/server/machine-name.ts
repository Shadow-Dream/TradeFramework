import { readFileSync } from "node:fs"
import { homedir, hostname } from "node:os"
import path from "node:path"
import process from "node:process"
import { spawnSync } from "node:child_process"
import { DATA_ROOT_NAME } from "../shared/branding"

function runAndRead(command: string, args: string[]) {
  const result = spawnSync(command, args, { encoding: "utf8" })
  if (result.status !== 0) return null
  const value = result.stdout.trim()
  return value || null
}

export function getMachineNameOverridePath() {
  return path.join(homedir(), DATA_ROOT_NAME, "machine-name")
}

/**
 * ~/.trade-agent/machine-name — explicit display-name override (first non-empty
 * line). Written by environments whose hostname is meaningless, e.g. deploy
 * previews name themselves after the ref they serve; users can set it too.
 */
export function readMachineNameOverride(overridePath = getMachineNameOverridePath()): string | null {
  try {
    const firstLine = readFileSync(overridePath, "utf8").split("\n")[0]?.trim() ?? ""
    return firstLine ? firstLine.slice(0, 80) : null
  } catch {
    return null
  }
}

export function getMachineDisplayName(_identityPath?: string, overridePath?: string) {
  const override = readMachineNameOverride(overridePath)
  if (override) {
    return override
  }

  if (process.platform === "darwin") {
    const computerName = runAndRead("scutil", ["--get", "ComputerName"])
    if (computerName) {
      return computerName
    }
  }

  const rawHostname = hostname().trim()
  return rawHostname.replace(/\.local$|\.lan$/i, "") || "This Machine"
}
