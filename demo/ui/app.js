const PHASES = ["understand", "localize", "plan", "act", "verify", "reflect", "finalize"];
const GATE_ORDER = ["patch_applies", "builds", "target_test_passes", "regression_subset_passes"];
const COMPARISON_RUNS = [
  { key: "sutra", run: "more-itertools-trajectory", label: "Sutra" },
  { key: "naive", run: "more-itertools-naive-baseline", label: "Naive baseline" },
];
const MEMORY_COMPARISON_RUNS = [
  { key: "issue1", run: "more-itertools-issue1-trajectory", label: "Issue #1" },
  { key: "issue2", run: "more-itertools-issue2-trajectory", label: "Issue #2" },
];
const DEFAULT_RUN = "more-itertools-issue1-trajectory";

let EVENTS = [];
let currentIndex = 0;
let recoveryRange = null; // [startIdx, endIdx] inclusive, or null
let memoryHitIndex = null; // index of a "memory_read" event that actually found something, or null

const $ = (sel) => document.querySelector(sel);

async function boot() {
  const params = new URLSearchParams(location.search);
  const run = params.get("run") || DEFAULT_RUN;
  const picker = $("#run-picker");
  if ([...picker.options].some((o) => o.value === run)) picker.value = run;

  const res = await fetch(`/api/trajectory?run=${encodeURIComponent(run)}`);
  if (!res.ok) {
    $("#run-status").textContent = "failed to load trajectory";
    return;
  }
  EVENTS = await res.json();
  recoveryRange = findRecoveryRange(EVENTS);
  memoryHitIndex = EVENTS.findIndex((e) => e.type === "memory_read" && e.payload.hit);
  if (memoryHitIndex === -1) memoryHitIndex = null;

  buildPhaseRail();
  buildEventList();
  buildScrub();
  setScrub(EVENTS.length - 1); // start fully scrubbed-in so the demo opens "complete"
  updateRunStatus();
  loadComparisonChart();
  loadMemoryComparison();

  picker.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("run", picker.value);
    location.href = url.toString();
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight") setScrub(Math.min(currentIndex + 1, EVENTS.length - 1));
    if (e.key === "ArrowLeft") setScrub(Math.max(currentIndex - 1, 0));
  });
}

// ---- recovery-sequence detection --------------------------------------

function findRecoveryRange(events) {
  let failIdx = null;
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.type !== "verify_gate") continue;
    if (e.payload.verified === false && failIdx === null) {
      failIdx = i;
    } else if (e.payload.verified === true && failIdx !== null) {
      return [failIdx, i];
    }
  }
  return null;
}

// ---- phase rail ---------------------------------------------------------

function buildPhaseRail() {
  const rail = $("#phase-rail");
  rail.innerHTML = "";
  for (const phase of PHASES) {
    const li = document.createElement("li");
    li.textContent = phase;
    li.dataset.phase = phase;
    rail.appendChild(li);
  }
}

function updatePhaseRail() {
  const seen = new Set(EVENTS.slice(0, currentIndex + 1).map((e) => e.phase));
  const activePhase = EVENTS[currentIndex]?.phase;
  for (const li of $("#phase-rail").children) {
    li.classList.toggle("active", li.dataset.phase === activePhase);
    li.classList.toggle("visited", seen.has(li.dataset.phase) && li.dataset.phase !== activePhase);
  }
}

// ---- event list -----------------------------------------------------------

function iconFor(event) {
  switch (event.type) {
    case "phase_start":
      return { cls: "icon-phase_start", glyph: "▸" };
    case "model_call":
      return { cls: "icon-model_call", glyph: "M" };
    case "tool_call":
      return { cls: "icon-tool_call", glyph: "T" };
    case "observation": {
      const ok = event.payload?.result?.ok !== false;
      return ok ? { cls: "icon-observation-ok", glyph: "✓" } : { cls: "icon-observation-fail", glyph: "✕" };
    }
    case "checkpoint":
      return { cls: "icon-checkpoint", glyph: "⬤" };
    case "verify_gate":
      return event.payload?.verified
        ? { cls: "icon-verify_gate-pass", glyph: "✓" }
        : { cls: "icon-verify_gate-fail", glyph: "✕" };
    case "run_end":
      return { cls: "icon-run_end", glyph: "⚑" };
    case "triage":
      return { cls: "icon-triage", glyph: "◈" };
    case "budget_enforced":
      return { cls: "icon-budget_enforced", glyph: "⚠" };
    case "memory_read":
      return { cls: "icon-memory_read", glyph: "↙" };
    case "memory_write":
      return { cls: "icon-memory_write", glyph: "↗" };
    case "confidence_report":
      return { cls: "icon-confidence_report", glyph: "◆" }; // diamond, not a pass/fail check-or-cross: honest uncertainty, not failure
    case "adversarial_review": {
      const v = event.payload?.verdict;
      const glyph = v === "red_flag" ? "!" : v === "no_concerns" ? "◎" : "~";
      return { cls: `icon-adversarial_review-${v || "minor_concerns"}`, glyph };
    }
    default:
      return { cls: "icon-phase_start", glyph: "?" };
  }
}

function truncate(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function summaryFor(event) {
  const p = event.payload || {};
  switch (event.type) {
    case "phase_start":
      return `phase started — ${truncate(p.instruction, 70)}`;
    case "model_call":
      return truncate(p.content, 90) || "(no content)";
    case "tool_call": {
      const args = Object.entries(p.arguments || {})
        .map(([k, v]) => `${k}=${truncate(JSON.stringify(v), 28)}`)
        .join(", ");
      return `${p.name}(${args})`;
    }
    case "observation": {
      const r = p.result || {};
      if (p.name === "run_tests") return `→ ${r.summary || (r.passed ? "passed" : "failed")}`;
      if (r.ok === false) return `→ error: ${truncate(r.error, 70)}`;
      if (r.hint) return `→ ok (${truncate(r.hint, 60)})`;
      return "→ ok";
    }
    case "checkpoint":
      return p.committed ? `git commit ${truncate(p.commit, 10)}` : "no changes to checkpoint";
    case "verify_gate": {
      const gates = p.gates || {};
      const passed = Object.values(gates).filter((g) => g.passed).length;
      return `verified=${p.verified} (${passed}/${Object.keys(gates).length} gates passed)`;
    }
    case "run_end": {
      const c = p.run_summary || {};
      return `run ended — verified=${c.verified}, ${c.gates_passed}/${c.gates_total} gates, ${c.retries_used} retr${c.retries_used === 1 ? "y" : "ies"}`;
    }
    case "triage":
      return `tier=${p.tier} — ${truncate(p.justification, 70)}`;
    case "budget_enforced":
      return `⚠ forced phase transition — ${truncate(p.reason, 70)}`;
    case "memory_read": {
      if (!p.hit) return "repo memory queried — no relevant notes or fresh cache yet";
      const nEntries = (p.entries || []).length;
      const cacheFiles = Object.keys(p.cache_hits || {});
      const parts = [];
      if (nEntries) parts.push(`${nEntries} prior note${nEntries === 1 ? "" : "s"}`);
      if (cacheFiles.length) parts.push(`cached index for ${cacheFiles.join(", ")}`);
      return `↙ repo memory hit — ${parts.join(", ")}`;
    }
    case "memory_write": {
      const counts = Object.entries(p.entries_written || {}).map(([cat, items]) => `${items.length} ${cat}`);
      const indexed = Object.keys(p.symbol_index_updated || {});
      const bits = [...counts];
      if (indexed.length) bits.push(`indexed ${indexed.join(", ")}`);
      return `↗ wrote to repo memory — ${bits.join(", ") || "nothing new"}`;
    }
    case "confidence_report": {
      const cr = p.confidence_report || {};
      return `◆ unresolved — best checkpoint ${truncate(p.best_checkpoint, 10)}, confidence: ${cr.root_cause_confidence}`;
    }
    case "adversarial_review":
      return `review: ${p.verdict} — ${truncate(p.notes, 70)}`;
    default:
      return event.type;
  }
}

function buildEventList() {
  const list = $("#event-list");
  list.innerHTML = "";

  const memoryRange = memoryHitIndex === null ? null : [memoryHitIndex, Math.min(memoryHitIndex + 1, EVENTS.length - 1)];

  let openBlock = null;
  EVENTS.forEach((event, idx) => {
    if (recoveryRange && idx === recoveryRange[0]) {
      openBlock = document.createElement("div");
      openBlock.className = "recovery-block";
      const label = document.createElement("div");
      label.className = "recovery-label";
      label.textContent = "↻ Self-correction: verification failed → reflected → retried → passed";
      openBlock.appendChild(label);
      list.appendChild(openBlock);
    }
    if (memoryRange && idx === memoryRange[0]) {
      openBlock = document.createElement("div");
      openBlock.className = "memory-block";
      const label = document.createElement("div");
      label.className = "memory-label";
      label.textContent = "↙ Repo memory used: prior-run notes/cache read and acted on here";
      openBlock.appendChild(label);
      list.appendChild(openBlock);
    }

    const row = document.createElement("div");
    row.className = "event-row";
    row.dataset.idx = String(idx);

    const { cls, glyph } = iconFor(event);
    const icon = document.createElement("div");
    icon.className = `icon ${cls}`;
    icon.textContent = glyph;

    const body = document.createElement("div");
    body.className = "body";
    const summaryLine = document.createElement("div");
    summaryLine.className = "summary-line";
    const phaseTag = document.createElement("span");
    phaseTag.className = "phase-tag";
    phaseTag.textContent = event.phase;
    const summary = document.createElement("span");
    summary.className = "summary";
    summary.textContent = summaryFor(event);
    summaryLine.append(phaseTag, summary);

    const detail = document.createElement("div");
    detail.className = "detail";
    detail.textContent = truncate(JSON.stringify(event.payload, null, 2), 6000);

    body.append(summaryLine, detail);
    row.append(icon, body);
    row.addEventListener("click", () => row.classList.toggle("expanded"));

    (openBlock || list).appendChild(row);

    if (recoveryRange && idx === recoveryRange[1]) openBlock = null;
    if (memoryRange && idx === memoryRange[1]) openBlock = null;
  });
}

function updateEventFadeState() {
  for (const row of document.querySelectorAll(".event-row")) {
    row.classList.toggle("future", Number(row.dataset.idx) > currentIndex);
  }
}

// ---- scrub bar --------------------------------------------------------

function buildScrub() {
  const scrub = $("#scrub");
  scrub.max = String(Math.max(0, EVENTS.length - 1));
  scrub.addEventListener("input", (e) => setScrub(Number(e.target.value)));
}

function setScrub(idx) {
  currentIndex = idx;
  $("#scrub").value = String(idx);
  $("#scrub-label").textContent = `event ${idx + 1} / ${EVENTS.length}`;
  updatePhaseRail();
  updateEventFadeState();
  updateSidebar();
}

// ---- sidebar ------------------------------------------------------------

function updateSidebar() {
  const upTo = EVENTS.slice(0, currentIndex + 1);
  const tokens = upTo.reduce((sum, e) => sum + (e.tokens_used || 0), 0);
  const toolCalls = upTo.filter((e) => e.type === "tool_call").length;

  $("#stat-tokens").textContent = tokens.toLocaleString();
  $("#stat-tools").textContent = String(toolCalls);

  const lastGateEvent = [...upTo].reverse().find((e) => e.type === "verify_gate");
  const gateList = $("#gate-list");
  gateList.innerHTML = "";
  const gates = lastGateEvent ? lastGateEvent.payload.gates : {};
  for (const name of GATE_ORDER) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = name.replace(/_/g, " ");
    const mark = document.createElement("span");
    if (gates[name] === undefined) {
      mark.className = "gate-mark pending";
      mark.textContent = "–";
    } else {
      mark.className = `gate-mark ${gates[name].passed ? "pass" : "fail"}`;
      mark.textContent = gates[name].passed ? "✓" : "✕";
    }
    li.append(label, mark);
    gateList.appendChild(li);
  }

  const triageEvent = upTo.find((e) => e.type === "triage");
  $("#stat-tier").textContent = triageEvent ? triageEvent.payload.tier : "–";
  $("#stat-tier-justification").textContent = triageEvent ? triageEvent.payload.justification : "";

  const reviewEvent = upTo.find((e) => e.type === "adversarial_review");
  const reviewBadge = $("#review-badge");
  if (reviewEvent) {
    reviewBadge.style.display = "inline-block";
    reviewBadge.className = `review-badge ${reviewEvent.payload.verdict}`;
    reviewBadge.textContent = `review: ${reviewEvent.payload.verdict.replace(/_/g, " ")}`;
    reviewBadge.title = reviewEvent.payload.notes || "";
  } else {
    reviewBadge.style.display = "none";
  }

  const confidenceEvent = upTo.find((e) => e.type === "confidence_report");
  const unresolvedBlock = $("#unresolved-block");
  if (confidenceEvent) {
    unresolvedBlock.style.display = "block";
    const cr = confidenceEvent.payload.confidence_report || {};
    $("#unresolved-confidence").textContent = cr.root_cause_confidence || "?";
    $("#unresolved-summary").textContent = cr.root_cause_summary || "";
    $("#unresolved-issue").textContent = cr.unresolved_issue || "";
    $("#unresolved-attempts").textContent = cr.attempts_made ?? "?";
  } else {
    unresolvedBlock.style.display = "none";
  }
}

// ---- cost comparison chart ------------------------------------------------

async function loadComparisonChart() {
  const canvas = $("#cost-chart");
  const caption = $("#cost-chart-caption");
  const ctx = canvas.getContext("2d");

  let bars;
  try {
    const results = await Promise.all(
      COMPARISON_RUNS.map(async (r) => {
        const res = await fetch(`/api/trajectory?run=${encodeURIComponent(r.run)}`);
        if (!res.ok) throw new Error(`missing fixture: ${r.run}`);
        const events = await res.json();
        const tokens = events.reduce((sum, e) => sum + (e.tokens_used || 0), 0);
        return { ...r, tokens };
      })
    );
    bars = results;
  } catch (err) {
    caption.textContent = "comparison fixtures not available";
    return;
  }

  drawBarChart(ctx, canvas.width, canvas.height, bars);

  const [sutra, naive] = bars;
  const multiplier = naive.tokens && sutra.tokens ? (naive.tokens / sutra.tokens).toFixed(1) : "?";
  caption.textContent = `${sutra.label}: ${sutra.tokens.toLocaleString()} tokens · ${naive.label}: ${naive.tokens.toLocaleString()} tokens — ${multiplier}× more without the budget router, same verified fix.`;
}

function drawBarChart(ctx, width, height, bars) {
  ctx.clearRect(0, 0, width, height);
  const maxVal = Math.max(...bars.map((b) => b.tokens), 1);
  const padding = { top: 20, bottom: 30, left: 10, right: 10 };
  const plotHeight = height - padding.top - padding.bottom;
  const gap = 28;
  const barWidth = (width - padding.left - padding.right - gap) / bars.length;
  const colors = ["#5b8cff", "#ffb454"];

  bars.forEach((bar, i) => {
    const barHeight = Math.max(2, (bar.tokens / maxVal) * plotHeight);
    const x = padding.left + i * (barWidth + gap);
    const y = padding.top + (plotHeight - barHeight);

    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(x, y, barWidth, barHeight);

    ctx.fillStyle = "#e6e8ec";
    ctx.font = "11px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(bar.tokens.toLocaleString(), x + barWidth / 2, y - 4);

    ctx.fillStyle = "#8b93a3";
    ctx.fillText(bar.label, x + barWidth / 2, height - 10);
  });
}

// ---- issue #1 vs #2 repo-memory comparison --------------------------------

async function loadMemoryComparison() {
  const table = $("#memory-compare-table");
  const caption = $("#memory-compare-caption");
  const entriesList = $("#memory-entries-list");
  const canvas = $("#memory-chart");
  const ctx = canvas.getContext("2d");

  let runs;
  try {
    runs = await Promise.all(
      MEMORY_COMPARISON_RUNS.map(async (r) => {
        const res = await fetch(`/api/trajectory?run=${encodeURIComponent(r.run)}`);
        if (!res.ok) throw new Error(`missing fixture: ${r.run}`);
        const events = await res.json();
        return { ...r, events };
      })
    );
  } catch (err) {
    caption.textContent = "issue #1/#2 fixtures not available";
    return;
  }

  const stats = runs.map((r) => ({
    label: r.label,
    tokens: r.events.reduce((sum, e) => sum + (e.tokens_used || 0), 0),
    toolCalls: r.events.filter((e) => e.type === "tool_call").length,
    localizeToolCalls: r.events.filter((e) => e.phase === "localize" && e.type === "tool_call").length,
  }));

  drawBarChart(ctx, canvas.width, canvas.height, stats);

  table.innerHTML = `
    <tr><th>metric</th><th>${stats[0].label}</th><th>${stats[1].label}</th></tr>
    <tr><td>tokens</td><td>${stats[0].tokens.toLocaleString()}</td><td>${stats[1].tokens.toLocaleString()}</td></tr>
    <tr><td>tool calls</td><td>${stats[0].toolCalls}</td><td>${stats[1].toolCalls}</td></tr>
    <tr><td>localize tool calls</td><td>${stats[0].localizeToolCalls}</td><td>${stats[1].localizeToolCalls}</td></tr>
  `;

  const tokenSaved = stats[0].tokens ? (1 - stats[1].tokens / stats[0].tokens) * 100 : 0;
  caption.textContent = `Issue #2 used repo memory from issue #1: ${stats[1].localizeToolCalls} localize tool call(s) vs. ${stats[0].localizeToolCalls} on issue #1, and ${Math.round(tokenSaved)}% fewer tokens overall.`;

  const issue2 = runs.find((r) => r.key === "issue2");
  const memoryReadEvent = issue2 && issue2.events.find((e) => e.type === "memory_read" && e.payload.hit);
  entriesList.innerHTML = "";
  if (memoryReadEvent) {
    for (const entry of memoryReadEvent.payload.entries || []) {
      const li = document.createElement("li");
      const cat = document.createElement("span");
      cat.className = "entry-category";
      cat.textContent = entry.category;
      li.appendChild(cat);
      li.appendChild(document.createTextNode(entry.note || entry.pattern || ""));
      entriesList.appendChild(li);
    }
    for (const path of Object.keys(memoryReadEvent.payload.cache_hits || {})) {
      const li = document.createElement("li");
      const cat = document.createElement("span");
      cat.className = "entry-category";
      cat.textContent = "cache";
      li.appendChild(cat);
      li.appendChild(document.createTextNode(`fresh symbol index for ${path}`));
      entriesList.appendChild(li);
    }
  } else {
    entriesList.innerHTML = "<li>no memory hit recorded for issue #2</li>";
  }
}

function updateRunStatus() {
  const runEnd = EVENTS.find((e) => e.type === "run_end");
  if (!runEnd) {
    $("#run-status").textContent = `${EVENTS.length} events`;
    return;
  }
  const c = runEnd.payload.run_summary || {};
  $("#run-status").textContent = `verified: ${c.verified} · ${c.gates_passed}/${c.gates_total} gates · ${c.retries_used} retr${c.retries_used === 1 ? "y" : "ies"} · ${EVENTS.length} events`;
}

boot();
