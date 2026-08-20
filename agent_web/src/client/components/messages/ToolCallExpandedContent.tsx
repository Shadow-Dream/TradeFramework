import { useMemo } from "react"
import type { ProcessedToolCall } from "./types"
import type { NormalizedToolCall, TranscriptEntry } from "../../../shared/types"
import { hydrateToolResult } from "../../../shared/tools"
import { MetaCodeBlock, VerticalLineContainer } from "./shared"
import { FileContentView } from "./FileContentView"
import { useToolPayload } from "./tool-payload-context"

/**
 * The body of an expanded tool call.
 *
 * Split out from `ToolCallMessage` so none of it runs while the row is
 * collapsed — which is almost always. Deriving it eagerly meant every tool call
 * in the transcript stringified its whole result on mount just to keep it ready
 * for an expansion that mostly never comes. `ExpandableRow` only mounts this
 * subtree once expanded, so the cost now follows the click.
 */

type ReadImageBlock = {
  type: "image"
  data: string
  mimeType?: string
}

function extractReadImageBlocks(value: unknown): ReadImageBlock[] {
  const blocks = (
    value
    && typeof value === "object"
    && "content" in value
    && Array.isArray((value as { content?: unknown }).content)
  )
    ? (value as { content: unknown[] }).content
    : Array.isArray(value)
      ? value
      : []

  return blocks.flatMap((block) => {
    if (!block || typeof block !== "object" || !("type" in block) || block.type !== "image") {
      return []
    }

    if ("data" in block && typeof block.data === "string") {
      return [{
        type: "image",
        data: block.data,
        mimeType: typeof block.mimeType === "string" ? block.mimeType : undefined,
      } satisfies ReadImageBlock]
    }

    if (
      "source" in block
      && block.source
      && typeof block.source === "object"
      && "type" in block.source
      && block.source.type === "base64"
      && "data" in block.source
      && typeof block.source.data === "string"
    ) {
      return [{
        type: "image",
        data: block.source.data,
        mimeType: typeof block.source.media_type === "string" ? block.source.media_type : undefined,
      } satisfies ReadImageBlock]
    }

    return []
  })
}

/**
 * Fold fetched entries back onto the row.
 *
 * The row carries what a collapsed header needs; the fetched entries carry the
 * bodies. Hydration of the result happens here rather than in the transcript
 * parse, because until now there was nothing to hydrate.
 */
function resolveToolCallPayloads(
  row: ProcessedToolCall,
  fetchedCall: TranscriptEntry | undefined,
  fetchedResult: TranscriptEntry | undefined
): ProcessedToolCall {
  const call = fetchedCall?.kind === "tool_call" ? fetchedCall.tool : undefined
  const resultContent = fetchedResult?.kind === "tool_result" ? fetchedResult.content : undefined
  if (!call && resultContent === undefined) return row

  const normalized = (call ?? row) as NormalizedToolCall
  return {
    ...row,
    ...(call ? { input: call.input as ProcessedToolCall["input"] } : {}),
    ...(resultContent !== undefined
      ? {
        result: hydrateToolResult(normalized, resultContent) as ProcessedToolCall["result"],
        rawResult: resultContent,
      }
      : {}),
  } as ProcessedToolCall
}

export function ReadResultImages({ images }: { images: ReadonlyArray<ReadImageBlock> }) {
  return (
    <div className="flex flex-col gap-3">
      {images.map((image, index) => {
        const mimeType = image.mimeType || "image/png"
        return (
          <div key={`${mimeType}:${index}`} className="overflow-hidden rounded-lg border border-border bg-muted/20">
            <img
              src={`data:${mimeType};base64,${image.data}`}
              alt={`Read result ${index + 1}`}
              className="max-h-[50vh] w-full object-contain bg-background"
            />
          </div>
        )
      })}
    </div>
  )
}

export function ToolCallExpandedContent({ message: row }: { message: ProcessedToolCall }) {
  // Mounting this component is the signal that the payloads are wanted; these
  // request them if the transcript arrived without them.
  const fetchedCall = useToolPayload(row.inputTrimmed ? row.id : undefined)
  const fetchedResult = useToolPayload(row.resultTrimmed ? row.resultEntryId : undefined)
  const message = useMemo(
    () => resolveToolCallPayloads(row, fetchedCall, fetchedResult),
    [row, fetchedCall, fetchedResult]
  )
  const isAwaitingPayload = (row.inputTrimmed && !fetchedCall)
    || (row.resultTrimmed && row.resultEntryId !== undefined && !fetchedResult)

  const hasResult = message.resultEntryId !== undefined
  const isBashTool = message.toolKind === "bash"
  const isWriteTool = message.toolKind === "write_file"
  const isEditTool = message.toolKind === "edit_file"
  const isDeleteTool = message.toolKind === "delete_file"
  const isReadTool = message.toolKind === "read_file"

  const resultText = useMemo(() => {
    if (typeof message.result === "string") return message.result
    if (!message.result) return ""
    if (typeof message.result === "object" && message.result !== null && "content" in message.result) {
      const content = (message.result as { content?: unknown }).content
      if (typeof content === "string") return content
    }
    return JSON.stringify(message.result, null, 2)
  }, [message.result])

  const readImages = useMemo(() => {
    if (!isReadTool) {
      return [] as ReadImageBlock[]
    }

    if (message.result && typeof message.result === "object" && "blocks" in message.result) {
      const blocks = (message.result as { blocks?: unknown }).blocks
      if (Array.isArray(blocks)) {
        const hydratedBlocks = extractReadImageBlocks(blocks)
        if (hydratedBlocks.length > 0) {
          return hydratedBlocks
        }
      }
    }

    return extractReadImageBlocks(message.rawResult)
  }, [isReadTool, message.rawResult, message.result])

  const inputText = useMemo(() => {
    switch (message.toolKind) {
      case "bash":
        return message.input.command
      case "write_file":
      case "delete_file":
        return message.input.content
      default:
        return JSON.stringify(message.input, null, 2)
    }
  }, [message])

  if (isAwaitingPayload) {
    // Reserving the row rather than rendering half a body: the fields are
    // in flight, and flashing empty code blocks first would reflow twice.
    return (
      <VerticalLineContainer className="my-4 text-sm">
        <span className="text-muted-foreground">Loading…</span>
      </VerticalLineContainer>
    )
  }

  return (
    <VerticalLineContainer className="my-4 text-sm">
      <div className="flex flex-col gap-2">
        {isEditTool ? (
          <FileContentView
            content=""
            isDiff
            oldString={message.input.oldString}
            newString={message.input.newString}
          />
        ) : isDeleteTool ? (
          <FileContentView
            content={message.input.content ?? ""}
          />
        ) : !isReadTool && !isWriteTool && (
          <MetaCodeBlock label={
            isBashTool ? (
              <span className="flex items-center gap-2 w-full">
                <span>Command</span>
                {!!message.input.timeoutMs && (
                  <span className="text-muted-foreground">timeout: {String(message.input.timeoutMs)}ms</span>
                )}
                {!!message.input.runInBackground && (
                  <span className="text-muted-foreground">background</span>
                )}
              </span>
            ) : isWriteTool ? "Contents" : "Input"
          } copyText={inputText}>
            {inputText}
          </MetaCodeBlock>
        )}
        {hasResult && isReadTool && !message.isError && (
          readImages.length > 0 ? (
            <div>
              <span className="font-medium text-muted-foreground">Image</span>
              <div className="mt-1">
                <ReadResultImages images={readImages} />
              </div>
            </div>
          ) : (
            <FileContentView
              content={resultText}
            />
          )
        )}
        {isWriteTool && !message.isError && (
          <FileContentView
            content={message.input.content ?? ""}
          />
        )}
        {hasResult && !isReadTool && !(isWriteTool && !message.isError) && !(isEditTool && !message.isError) && !(isDeleteTool && !message.isError) && (
          <MetaCodeBlock label={message.isError ? "Error" : "Result"} copyText={resultText}>
            {resultText}
          </MetaCodeBlock>
        )}
      </div>
    </VerticalLineContainer>
  )
}
