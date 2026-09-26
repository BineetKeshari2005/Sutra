const PHASES = ["understand", "localize", "plan", "act", "verify", "reflect", "finalize"];
const GATE_ORDER = ["patch_applies", "builds", "target_test_passes", "regression_subset_passes"];

let EVENTS = [];
let currentIndex = 0;
let recoveryRange = null; // [startIdx, endIdx] inclusive, or null

const $ = (sel) => document.querySelector(sel);

async function boot() {
  const params = new URLSearchParams(location.search);
  const run = params.get("run");
  const url = run ? `/api/trajectory?run=${encodeURIComponent(run)}` : "/api/trajectory";

  const res = await fetch(url);
  if (!res.ok) {
    $("#run-status").textContent = "failed to load trajectory";
    return;
  }
  EVENTS = await res.json();
  recoveryRange = findRecoveryRange(EVENTS);

  buildPhaseRail();
  buildEventList();
  buildScrub();
  setScrub(EVENTS.length - 1); // start fully scrubbed-in so the demo opens "complete"
  updateRunStatus();

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
      const c = p.confidence_report || {};
      return `run ended — verified=${c.verified}, ${c.gates_passed}/${c.gates_total} gates, ${c.retries_used} retr${c.retries_used === 1 ? "y" : "ies"}`;
    }
    default:
      return event.type;
  }
}

function buildEventList() {
  const list = $("#event-list");
  list.innerHTML = "";

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
}

function updateRunStatus() {
  const runEnd = EVENTS.find((e) => e.type === "run_end");
  if (!runEnd) {
    $("#run-status").textContent = `${EVENTS.length} events`;
    return;
  }
  const c = runEnd.payload.confidence_report || {};
  $("#run-status").textContent = `verified: ${c.verified} · ${c.gates_passed}/${c.gates_total} gates · ${c.retries_used} retr${c.retries_used === 1 ? "y" : "ies"} · ${EVENTS.length} events`;
}

boot();
