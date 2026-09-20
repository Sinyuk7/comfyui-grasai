export function selectedParameters(profile, previous = {}) {
  return Object.fromEntries(Object.entries(profile.parameters).map(([name, rule]) => [
    name,
    rule.options.some((option) => option.value === previous[name]) ? previous[name] : rule.default,
  ]));
}

export function acceptBalance(state, payload, token) {
  if (!token || payload.ui_token !== token || payload.sequence < state.sequence) return false;
  state.sequence = payload.sequence;
  return true;
}

export function balanceText(payload) {
  switch (payload.state) {
    case "ready":
      return `剩余积分：${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 8 }).format(payload.credits)} · ${new Date(payload.queried_at).toLocaleTimeString()}`;
    case "loading": return "剩余积分：查询中";
    case "stale": return "剩余积分：可能过期";
    case "error": return "余额查询失败";
    default: return "剩余积分：未查询";
  }
}

export function batchText(payload) {
  if (payload.stage === "planned") {
    return `计划：${payload.base_count} 组图片 × ${payload.prompt_count} 个提示词 = ${payload.total} 个任务 · 并发 ${payload.concurrency}`;
  }
  if (payload.stage === "running") return `已完成：${payload.completed} / ${payload.total}`;
  if (payload.stage === "interrupted") return `已中断：${payload.completed} / ${payload.total}`;
  return `结束：成功 ${payload.success} · 失败 ${payload.failed} · 已处理 ${payload.completed} / ${payload.total}`;
}
