const $ = (id) => document.getElementById(id)

const state = {
  configs: [],
  job: null,
  logOffset: 0,
  logJobId: null,
  polling: false,
  wasRunning: false,
}

let toastTimer = null

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    data = null
  }
  if (!response.ok) {
    throw new Error((data && data.error) || `${response.status} ${response.statusText}`)
  }
  return data
}

function toast(message, isError = false) {
  const el = $("toast")
  el.textContent = message
  el.className = `toast show${isError ? " error" : ""}`
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    el.className = "toast"
  }, 3500)
}

function selectedConfig() {
  const name = $("config").value
  return state.configs.find((item) => item.name === name) || null
}

function addChip(label, value, className = "") {
  const chip = document.createElement("span")
  chip.className = `chip ${className}`.trim()
  chip.textContent = `${label} ${value}`
  $("configMeta").appendChild(chip)
}

function renderConfig() {
  const meta = $("configMeta")
  meta.textContent = ""
  const item = selectedConfig()
  if (!item) {
    addChip("config", "未选择")
    return
  }
  if (item.error) {
    addChip("config error", item.error, "error")
    return
  }

  const cfg = item.config
  addChip("backend", cfg.backend)
  addChip("model", cfg.model)
  addChip("benchmark", cfg.benchmark)
  addChip("prob", cfg.prob)
  addChip("site", cfg.site)
  addChip("limit", cfg.limit == null ? "all" : cfg.limit)
  addChip(
    "inject",
    `${cfg.inject_user ? "user" : "—"} / ${cfg.inject_tool ? "tool" : "—"} / ${cfg.inject_assistant ? "assistant" : "—"}`,
  )

  $("limit").placeholder = cfg.limit == null ? "继承" : String(cfg.limit)
  $("tasks").placeholder = cfg.tasks && cfg.tasks.length ? cfg.tasks.join(",") : "继承"
  $("model").placeholder = cfg.model || "继承"
  $("prob").placeholder = String(cfg.prob)
}

function collectOptions() {
  const options = {
    limit: $("limit").value || null,
    tasks: $("tasks").value || "",
    model: $("model").value || "",
    prob: $("prob").value || null,
    site: $("site").value,
    dry_run: $("dryRun").value === "1",
  }
  for (const el of document.querySelectorAll("[data-inject]")) {
    options[el.dataset.inject] = el.value
  }
  return options
}

async function loadConfigs() {
  try {
    const data = await api("/api/configs")
    state.configs = data.configs || []
    const select = $("config")
    const previous = select.value
    select.textContent = ""
    for (const item of state.configs) {
      const option = document.createElement("option")
      option.value = item.name
      option.textContent = item.name
      select.appendChild(option)
    }
    if (previous && state.configs.some((item) => item.name === previous)) {
      select.value = previous
    }
    renderConfig()
  } catch (error) {
    toast(`配置加载失败：${error.message}`, true)
  }
}

function setStatus(kind, text) {
  const pill = $("statusPill")
  pill.className = `status-pill ${kind}`.trim()
  $("statusText").textContent = text
}

function renderJob(job) {
  if (!job) {
    setStatus("", "idle")
    $("start").disabled = false
    $("stop").disabled = true
    $("runHint").textContent = ""
    return
  }

  const running = Boolean(job.running)
  if (running) {
    setStatus("running", `running · ${job.elapsed_sec}s · ${job.config}`)
  } else if (job.returncode === 0) {
    setStatus("done", `done · ${job.elapsed_sec}s`)
  } else {
    setStatus("failed", `failed · rc=${job.returncode}`)
  }
  $("start").disabled = running
  $("stop").disabled = !running
  $("runHint").textContent = job.command ? job.command.slice(2).join(" ") : ""
}

function appendLog(lines) {
  if (!lines || lines.length === 0) return
  const log = $("log")
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80
  if (log.textContent === "等待运行…") log.textContent = ""
  log.textContent += `${lines.join("\n")}\n`
  if (nearBottom) log.scrollTop = log.scrollHeight
}

async function poll() {
  if (state.polling) return
  state.polling = true
  try {
    const data = await api("/api/status")
    const job = data.job
    state.job = job
    renderJob(job)

    if (job) {
      if (state.logJobId !== job.id) {
        state.logJobId = job.id
        state.logOffset = 0
        $("log").textContent = ""
      }
      const log = await api(`/api/log?offset=${state.logOffset}`)
      if (log.job && log.job.id === state.logJobId) {
        if (log.offset < state.logOffset) {
          $("log").textContent = ""
        }
        if (log.lines && log.lines.length) appendLog(log.lines)
        state.logOffset = log.offset
      }
    }

    if (state.wasRunning && job && !job.running) {
      await loadRuns()
      toast(job.returncode === 0 ? "实验完成" : `实验失败，rc=${job.returncode}`)
    }
    state.wasRunning = Boolean(job && job.running)
  } catch (error) {
    setStatus("failed", "server error")
    toast(`状态读取失败：${error.message}`, true)
  } finally {
    state.polling = false
  }
}

async function startRun() {
  const config = $("config").value
  if (!config) {
    toast("请先选择一个配置", true)
    return
  }
  $("start").disabled = true
  $("runHint").textContent = "starting…"
  try {
    const data = await api("/api/start", {
      method: "POST",
      body: JSON.stringify({ config, ...collectOptions() }),
    })
    state.logOffset = 0
    state.logJobId = data.job.id
    state.wasRunning = true
    $("log").textContent = ""
    toast("已启动")
    await poll()
  } catch (error) {
    toast(`启动失败：${error.message}`, true)
    $("start").disabled = false
    $("runHint").textContent = ""
  }
}

async function stopRun() {
  $("stop").disabled = true
  try {
    const data = await api("/api/stop", { method: "POST", body: "{}" })
    toast(data.stopped ? "已发送停止信号" : "当前没有运行中的实验")
    await poll()
  } catch (error) {
    toast(`停止失败：${error.message}`, true)
  }
}

function cell(text, className = "") {
  const td = document.createElement("td")
  td.textContent = text == null ? "—" : String(text)
  if (className) td.className = className
  return td
}

function renderRuns(runs) {
  const tbody = $("runsBody")
  tbody.textContent = ""
  if (!runs || runs.length === 0) {
    const tr = document.createElement("tr")
    const td = cell("还没有结果", "empty")
    td.colSpan = 8
    tr.appendChild(td)
    tbody.appendChild(tr)
    return
  }
  for (const run of runs) {
    const tr = document.createElement("tr")
    tr.title = run.run_dir

    tr.appendChild(cell(run.suite, "suite"))
    tr.appendChild(cell(run.task, "task"))
    tr.appendChild(cell(run.model, "model"))

    const reward = Number(run.reward)
    const rewardCell = cell(Number.isFinite(reward) ? reward.toFixed(2) : "—", "reward")
    if (Number.isFinite(reward)) rewardCell.classList.add(reward >= 1 ? "good" : "bad")
    tr.appendChild(rewardCell)

    tr.appendChild(cell(run.injections ?? 0, "num"))
    tr.appendChild(cell(run.flips ?? 0, "num"))
    tr.appendChild(cell(run.site || "—", "num"))

    const reportCell = document.createElement("td")
    if (run.report_url) {
      const link = document.createElement("a")
      link.className = "report"
      link.href = run.report_url
      link.target = "_blank"
      link.rel = "noopener"
      link.textContent = "open"
      reportCell.appendChild(link)
    } else {
      reportCell.textContent = "—"
    }
    tr.appendChild(reportCell)
    tbody.appendChild(tr)
  }
}

async function loadRuns() {
  try {
    const data = await api("/api/runs?limit=40")
    renderRuns(data.runs || [])
  } catch (error) {
    toast(`结果加载失败：${error.message}`, true)
  }
}

$("config").addEventListener("change", renderConfig)
$("refreshConfigs").addEventListener("click", loadConfigs)
$("refreshRuns").addEventListener("click", loadRuns)
$("start").addEventListener("click", startRun)
$("stop").addEventListener("click", stopRun)
$("clearLog").addEventListener("click", () => {
  $("log").textContent = ""
})

loadConfigs()
loadRuns()
poll()
setInterval(poll, 1000)
