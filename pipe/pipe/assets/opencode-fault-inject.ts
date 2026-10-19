import type { Plugin } from "@opencode-ai/plugin"
import { appendFileSync, readFileSync } from "node:fs"

// New definition:
//   context : every tool result (all command/return output)
//   tool    : tool arguments (parameter-level injection)
type Site = "context" | "tool"
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
}

// Tools whose arguments are injected (parameter-level).
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

  const writeLog = (rec: Record<string, unknown>) => {
    try {
      appendFileSync(cfg.logPath, JSON.stringify(rec) + "\n")
    } catch (err) {
      console.error("[fault-inject] log write failed:", err)
    }
  }

  return {
    // ---- parameter-level injection (site = "tool") ----
    "tool.execute.before": async (input, output) => {
      if (!cfg.enabled || cfg.site !== "tool") return
      const field = PARAM_FIELD[input.tool]
      if (!field) return
      if (cfg.tools.length > 0 && !cfg.tools.includes(input.tool)) return

      const args = output.args ?? {}
      const before = typeof args[field] === "string" ? args[field] : null
      if (before === null) return

      const res = applyFaults(before, cfg.prob, cfg.mode, cfg.width, rnd)
      if (res.flips.length === 0) return

      args[field] = res.text
      writeLog({
        ts: new Date().toISOString(),
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        site: "tool",
        field,
        mode: cfg.mode,
        width: cfg.width,
        n_flips: res.flips.length,
        n_bytes: Buffer.byteLength(before, "utf8"),
        prob: cfg.prob,
        seed: cfg.seed,
        flips: res.flips.slice(0, 200),
        before,
        after: res.text,
      })
    },

    // ---- observation injection (site = "context" = every tool result) ----
    "tool.execute.after": async (input, output) => {
      if (!cfg.enabled || cfg.site !== "context") return
      if (cfg.tools.length > 0 && !cfg.tools.includes(input.tool)) return

      const before =
        typeof output.output === "string"
          ? output.output
          : String(output.output ?? "")
      const res = applyFaults(before, cfg.prob, cfg.mode, cfg.width, rnd)
      if (res.flips.length === 0) return

      output.output = res.text
      writeLog({
        ts: new Date().toISOString(),
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        site: "context",
        mode: cfg.mode,
        width: cfg.width,
        n_flips: res.flips.length,
        n_bytes: Buffer.byteLength(before, "utf8"),
        prob: cfg.prob,
        seed: cfg.seed,
        flips: res.flips.slice(0, 200),
        before,
        after: res.text,
      })
    },
  }
}
