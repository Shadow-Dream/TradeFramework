import { afterEach, describe, expect, test } from "bun:test"
import { mkdir, rm, symlink } from "node:fs/promises"
import path from "node:path"
import { parseExternalAgentProjects, TradeProjectCatalog } from "./trade-project-catalog"

const roots: string[] = []

async function createRoot() {
  const root = path.join(process.env.TMPDIR ?? "/tmp", `trade-project-catalog-${crypto.randomUUID()}`)
  roots.push(root)
  await mkdir(root, { recursive: true })
  return root
}

async function createExternalProject(name = "momentum-lab") {
  const root = path.join(process.env.TMPDIR ?? "/tmp", `trade-agent-project-${crypto.randomUUID()}`)
  roots.push(root)
  const project = path.join(root, name)
  await mkdir(project, { recursive: true })
  return project
}

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await rm(root, { recursive: true, force: true })
  }
})

describe("TradeProjectCatalog", () => {
  test("publishes logical projects without paths", async () => {
    const root = await createRoot()
    const external = await createExternalProject()
    const catalog = new TradeProjectCatalog(root, [
      { projectId: "strategy:momentum-lab", label: "Momentum Lab", path: external },
    ])

    expect(await catalog.refresh()).toEqual([
      { projectId: "trade-engine", label: "TradeEngine", kind: "trade-engine" },
      { projectId: "strategy:momentum-lab", label: "Momentum Lab", kind: "strategy" },
    ])
    expect(await catalog.resolve("strategy:momentum-lab")).toBe(external)
    expect(JSON.stringify(catalog.list())).not.toContain(root)
    expect(JSON.stringify(catalog.list())).not.toContain(external)
  })

  test("does not discover strategy directories inside TradeEngine", async () => {
    const root = await createRoot()
    await mkdir(path.join(root, "strategies", "private-strategy"), { recursive: true })
    const catalog = new TradeProjectCatalog(root)

    const ids = (await catalog.refresh()).map((entry) => entry.projectId)
    expect(ids).toEqual(["trade-engine"])
  })

  test("rejects unknown project ids and a root symlink", async () => {
    const root = await createRoot()
    const catalog = new TradeProjectCatalog(root)
    await catalog.refresh()
    await expect(catalog.resolve("strategy:missing")).rejects.toThrow("not in the TradeEngine catalog")

    const linkedRoot = `${root}-link`
    roots.push(linkedRoot)
    await symlink(root, linkedRoot)
    await expect(new TradeProjectCatalog(linkedRoot).refresh()).rejects.toThrow("not an approved project directory")
  })

  test("rejects symlinked, overlapping and malformed external projects", async () => {
    const root = await createRoot()
    const external = await createExternalProject()
    const linked = `${external}-link`
    roots.push(linked)
    await symlink(external, linked)
    await expect(new TradeProjectCatalog(root, [
      { projectId: "strategy:linked", label: "Linked", path: linked },
    ]).refresh()).rejects.toThrow("not an approved project directory")
    await expect(new TradeProjectCatalog(root, [
      { projectId: "strategy:inside", label: "Inside", path: root },
    ]).refresh()).rejects.toThrow("outside TradeEngine")
    expect(() => parseExternalAgentProjects('[{"projectId":"trade-engine","label":"Bad","path":"/tmp/bad"}]'))
      .toThrow("fields are invalid")
  })

  test("parses exact external project configuration", () => {
    expect(parseExternalAgentProjects(
      '[{"projectId":"strategy:momentum-lab","label":"Momentum Lab","path":"/srv/strategies/momentum"}]',
    )).toEqual([
      { projectId: "strategy:momentum-lab", label: "Momentum Lab", path: "/srv/strategies/momentum" },
    ])
    expect(() => parseExternalAgentProjects('[{"projectId":"strategy:a","label":"A","path":"/a","extra":true}]'))
      .toThrow("fields are invalid")
  })
})
