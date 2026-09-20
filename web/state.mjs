export function selectedParameters(profile, previous = {}) {
  return Object.fromEntries(Object.entries(profile.parameters).map(([name, rule]) => [
    name,
    rule.options.some((option) => option.value === previous[name]) ? previous[name] : rule.default,
  ]));
}

export function acceptEvent(state, payload, token) {
  if (!token || payload.ui_token !== token || payload.sequence < state.sequence) return false;
  state.sequence = payload.sequence;
  return true;
}

export function balanceText(payload) {
  switch (payload.state) {
    case "ready":
      return `Balance: ${new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(payload.credits)} · ${new Date(payload.queried_at).toLocaleTimeString()}`;
    case "loading": return "Checking balance...";
    case "stale": return "Balance may be outdated";
    case "error": return "Balance unavailable";
    case "unavailable": return "Balance: Token not set";
    default: return "Balance: Not checked";
  }
}

export function batchText(payload) {
  if (payload.stage === "planned") {
    return `Planned: ${payload.base_count} image sets × ${payload.prompt_count} prompts = ${payload.total} tasks · concurrency ${payload.concurrency}`;
  }
  if (payload.stage === "running") {
    const active = Array.isArray(payload.active) ? payload.active : [];
    const running = Number.isInteger(payload.running) ? payload.running : active.length;
    const base = `Completed: ${payload.completed} / ${payload.total}${running ? ` · Running: ${running}` : ""}`;
    if (active.length === 1) return `${base} · ${stageText(active[0])}`;
    const reported = active.map((item) => validPercent(item.progress)).filter((value) => value !== null);
    return reported.length ? `${base} · Progress reported: ${reported.length} / ${running}` : base;
  }
  if (payload.stage === "interrupted") return `Interrupted: ${payload.completed} / ${payload.total}`;
  return `Finished: ${payload.success} succeeded · ${payload.failed} failed · ${payload.completed} / ${payload.total}`;
}

function validPercent(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

function percentText(value) {
  return `${Math.round(value)}%`;
}

function stageText(payload) {
  const progress = validPercent(payload.progress);
  if (payload.stage === "running") return progress === null ? "Generating..." : `Generating: ${percentText(progress)}`;
  if (payload.stage === "reconnecting") return "Reconnecting...";
  if (payload.stage === "submitting") return "Submitting...";
  if (payload.stage === "downloading") {
    return Number.isInteger(payload.completed) && Number.isInteger(payload.total)
      ? `Downloading: ${payload.completed} / ${payload.total}` : "Downloading...";
  }
  if (payload.stage === "succeeded") return "Completed";
  return "Working...";
}

export function taskProgress(payload) {
  const progress = validPercent(payload.progress);
  if (payload.stage === "succeeded") return { value: 100, indeterminate: false, text: "Completed" };
  if (payload.stage === "running" && progress !== null) {
    return { value: progress, indeterminate: false, text: stageText(payload) };
  }
  if (payload.stage === "downloading" && Number.isInteger(payload.completed)
      && Number.isInteger(payload.total) && payload.total > 0) {
    return {
      value: payload.completed * 100 / payload.total,
      indeterminate: false,
      text: stageText(payload),
    };
  }
  return { value: null, indeterminate: true, text: stageText(payload) };
}

export function batchProgress(payload) {
  const total = Number(payload.total);
  const completed = Number(payload.completed);
  if (!(total > 0) || !Number.isFinite(completed)) {
    return { value: null, indeterminate: false, text: batchText(payload) };
  }
  if (payload.stage === "planned") return { value: 0, indeterminate: false, text: batchText(payload) };
  if (payload.stage !== "running") {
    return { value: Math.min(100, completed * 100 / total), indeterminate: false, text: batchText(payload) };
  }
  const active = Array.isArray(payload.active) ? payload.active : [];
  const fractions = active.map((item) => item.stage === "running" ? validPercent(item.progress) : null)
    .filter((value) => value !== null);
  if (completed === 0 && fractions.length === 0) {
    return { value: null, indeterminate: true, text: batchText(payload) };
  }
  const value = (completed + fractions.reduce((sum, value) => sum + value / 100, 0)) * 100 / total;
  return { value: Math.min(100, value), indeterminate: false, text: batchText(payload) };
}
