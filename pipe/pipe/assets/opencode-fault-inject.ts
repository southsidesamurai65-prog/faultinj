import type { Plugin } from "@opencode-ai/plugin"
import { appendFileSync, mkdirSync, readFileSync } from "node:fs"
import { dirname } from "node:path"

// Injection sites:
//   context : tool results (tool.execute.after) + the user prompt
//   tool    : tool arguments (tool.execute.before)
//   assistant: the agent's own prior text (messages.transform)
//
// The user-prompt hook is what makes chat benchmarks (math / knowledge /
// long-context) injectable without an HTTP proxy.
type Site = "context" | "tool" | "assistant"
type Mode = "single" | "burst"

interface FaultConfig {
  enabled: boolean
  site: Site
  tools: string[]
  prob: number
  seed: number
  mode: Mode
  width: number
  logPath: string
  injectUser: boolean
  injectTool: boolean
  injectAssistant: boolean
  runID: string
  taskID: string
}

const PARAM_TOOLS = ["bash", "write", "edit"]
const PARAM_FIELD: Record<string, string> = {
  bash: "command",
  write: "content",
  edit: "newString",
}

function loadConfig(): FaultConfig {
  const path =
    process.env.FAULT_CONFIG ||
    `${process.env.HOME ?? "/root"}/.config/opencode/fault.json`
  const raw = readFileSync(path, "utf8")
  const c = JSON.parse(raw) as Partial<FaultConfig>
  return {
    enabled: c.enabled ?? false,
    site: (c.site ?? "context") as Site,
    tools: c.tools ?? [],
    prob: c.prob ?? 0,
    seed: c.seed ?? 1,
    mode: (c.mode ?? "single") as Mode,
    width: c.width ?? 8,
    logPath:
      c.logPath ||
      `${process.env.HOME ?? "/root"}/.config/opencode/fault-inject.log.jsonl`,
    injectUser: c.injectUser ?? false,
    injectTool: c.injectTool ?? true,
    injectAssistant: c.injectAssistant ?? false,
    runID: process.env.FAULT_RUN_ID || c.runID || "",
    taskID: process.env.FAULT_TASK_ID || c.taskID || "",
  }
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * single: every bit independently flips with probability ``prob``.
 * burst: memory-row style; each byte triggers with probability ``prob`` and
 *        all 8 bits of every byte in a run of ``width`` consecutive bytes flip.
 */
function applyFaults(
  text: string,
  prob: number,
  mode: Mode,
  width: number,
  rnd: () => number,
) {
  const buf = Buffer.from(text, "utf8")
  const flips: { offset: number; bit: number }[] = []

  if (mode === "burst") {
    const offsets = new Set<number>()
    let i = 0
    while (i < buf.length) {
      if (rnd() < prob) {
        const end = Math.min(i + width, buf.length)
        for (let j = i; j < end; j++) offsets.add(j)
        i = end
      } else {
        i += 1
      }
    }
    for (const o of offsets) {
      buf[o] ^= 0xff
      for (let b = 0; b < 8; b++) flips.push({ offset: o, bit: b })
    }
  } else {
    for (let i = 0; i < buf.length; i++) {
      for (let b = 0; b < 8; b++) {
        if (rnd() < prob) {
          buf[i] ^= 1 << b
          flips.push({ offset: i, bit: b })
        }
      }
    }
  }

  const out = new TextDecoder("utf-8", { fatal: false }).decode(buf)
  return { text: out, flips }
}

export const FaultInjectPlugin: Plugin = async () => {
  let cfg: FaultConfig
  try {
    cfg = loadConfig()
  } catch (err) {
    console.error("[fault-inject] config load failed:", err)
    return {}
  }

  const rnd = mulberry32(cfg.seed)
  const seenParts = new Set<string>()
  const seenAssistantParts = new Set<string>()
  let eventID = 0

  const compact = (value: unknown): unknown => {
    try {
      const s = JSON.stringify(value)
      return s.length > 2000 ? s.slice(0, 2000) + "…" : JSON.parse(s)
    } catch {
      return String(value).slice(0, 2000)
    }
  }

  const writeLog = (rec: Record<string, unknown>) => {
    try {
      mkdirSync(dirname(cfg.logPath), { recursive: true })
      appendFileSync(cfg.logPath, JSON.stringify(rec) + "\n")
    } catch (err) {
      console.error("[fault-inject] log write failed:", err)
    }
  }

  const inject = (opts: {
    site: Site
    where: string
    tool?: string
    field?: string
    sessionID?: string
    callID?: string
    messageID?: string
    args?: unknown
    before: string
  }): string | null => {
    if (!cfg.enabled || cfg.prob <= 0) return null
    if (cfg.tools.length > 0 && opts.tool && !cfg.tools.includes(opts.tool)) {
      return null
    }
    const res = applyFaults(opts.before, cfg.prob, cfg.mode, cfg.width, rnd)
    if (res.flips.length === 0) return null
    eventID += 1
    writeLog({
      ts: new Date().toISOString(),
      event_id: `${cfg.runID || "run"}-${cfg.taskID || "task"}-${eventID}`,
      run_id: cfg.runID,
      task_id: cfg.taskID,
      sessionID: opts.sessionID,
      callID: opts.callID,
      messageID: opts.messageID,
      tool: opts.tool ?? null,
      site: opts.site,
      where: opts.where,
      field: opts.field ?? null,
      mode: cfg.mode,
      width: cfg.width,
      n_flips: res.flips.length,
      n_bytes: Buffer.byteLength(opts.before, "utf8"),
      prob: cfg.prob,
      seed: cfg.seed,
      flips: res.flips.slice(0, 200),
      args: opts.args === undefined ? null : compact(opts.args),
      before: opts.before,
      after: res.text,
    })
    return res.text
  }

  return {
    // ---- user prompt / chat context injection ----
    "chat.message": async (input, output) => {
      if (!cfg.enabled || !cfg.injectUser || cfg.site !== "context") return
      // The title/summary agents send synthetic prompts; don't pollute results.
      if (input.agent === "title" || input.agent === "summary") return
      const parts = output.parts ?? []
      for (const part of parts) {
        if (part.type !== "text" || part.synthetic) continue
        const key = part.id || `${input.messageID ?? "msg"}:${part.messageID}`
        if (seenParts.has(key)) continue
        const text = inject({
          site: "context",
          where: "user_message",
          sessionID: input.sessionID,
          messageID: part.messageID,
          before: part.text,
        })
        if (text === null) continue
        part.text = text
        seenParts.add(key)
      }
    },

    // ---- assistant-output injection (messages.transform) ----
    "experimental.chat.messages.transform": async (_input, output) => {
      if (!cfg.enabled || !cfg.injectAssistant) return
      for (const message of output.messages ?? []) {
        if (message.info?.role !== "assistant") continue
        for (const part of message.parts ?? []) {
          const key = part.id || `${part.messageID}:${part.type}`
          if (seenAssistantParts.has(key)) continue
          if (part.type === "text" && part.text && !part.synthetic) {
            const text = inject({
              site: "assistant",
              where: "assistant_message",
              messageID: part.messageID,
              before: part.text,
            })
            if (text === null) continue
            part.text = text
            seenAssistantParts.add(key)
            continue
          }
          if (part.type === "tool") {
            const field = PARAM_FIELD[part.tool]
            const args = (part.state && "input" in part.state ? part.state.input : null) as
              | Record<string, unknown>
              | null
            if (!field || !args || typeof args[field] !== "string") continue
            const before = args[field] as string
            const text = inject({
              site: "assistant",
              where: "assistant_tool_call",
              tool: part.tool,
              field,
              messageID: part.messageID,
              callID: part.callID,
              args,
              before,
            })
            if (text === null) continue
            args[field] = text
            seenAssistantParts.add(key)
          }
        }
      }
    },

    // ---- parameter-level injection (site = "tool") ----
    "tool.execute.before": async (input, output) => {
      if (!cfg.enabled || cfg.site !== "tool") return
      const field = PARAM_FIELD[input.tool]
      if (!field) return
      if (cfg.tools.length > 0 && !cfg.tools.includes(input.tool)) return

      const args = output.args ?? {}
      const before = typeof args[field] === "string" ? args[field] : null
      if (before === null) return

      const text = inject({
        site: "tool",
        where: "tool_argument",
        tool: input.tool,
        field,
        sessionID: input.sessionID,
        callID: input.callID,
        args,
        before,
      })
      if (text === null) return
      args[field] = text
    },

    // ---- observation injection (site = "context" = every tool result) ----
    "tool.execute.after": async (input, output) => {
      if (!cfg.enabled || cfg.site !== "context" || !cfg.injectTool) return
      if (cfg.tools.length > 0 && !cfg.tools.includes(input.tool)) return

      const before =
        typeof output.output === "string"
          ? output.output
          : String(output.output ?? "")
      const text = inject({
        site: "context",
        where: "tool_result",
        tool: input.tool,
        sessionID: input.sessionID,
        callID: input.callID,
        args: input.args,
        before,
      })
      if (text === null) return
      output.output = text
    },
  }
}
