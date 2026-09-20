import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { acceptBalance, balanceText, batchText, selectedParameters } from "./state.mjs";

let catalog;
const states = new WeakMap();
const widget = (node, name) => node.widgets?.find((item) => item.name === name);
const parameters = (node) => Object.fromEntries((node.widgets ?? [])
  .filter((item) => item.name.startsWith("model."))
  .map((item) => [item.name.slice(6), item.value]));

function resetBalance(node) {
  const state = states.get(node);
  if (!state) return;
  node.properties.grsai_ui_token = crypto.randomUUID?.()
    ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, "0")).join("");
  state.key = widget(node, "api_key")?.value;
  state.sequence = 0;
  state.balance.value = balanceText({});
  state.status.value = "";
  if (state.directory) state.directory.value = "";
  node.setDirtyCanvas(true, true);
}

function syncKey(node) {
  if (states.get(node)?.key !== widget(node, "api_key")?.value) resetBalance(node);
}

function decorateParameters(node) {
  const state = states.get(node);
  const profile = catalog.models[widget(node, "model")?.value];
  if (!profile) {
    state.status.value = "配置错误：模型已删除或禁用";
    return;
  }
  for (const [name, rule] of Object.entries(profile.parameters)) {
    const item = widget(node, `model.${name}`);
    if (!item) continue;
    item.disabled = rule.options.length === 1;
    item.label = name === "aspectRatio" ? (profile.family === "nano_banana" ? "图片比例" : "图片尺寸")
      : name === "imageSize" ? "分辨率" : "质量";
    const labels = Object.fromEntries(rule.options.map((option) => [option.value, option.label]));
    item.options = { ...item.options, disabled: item.disabled, getOptionLabel: (value) => labels[value] ?? value };
    // The canvas host hides disabled widget values by default. Keep fixed choices visible.
    Object.defineProperty(item, "_displayValue", {
      configurable: true,
      get() { return labels[this.value] ?? String(this.value); },
    });
    if (!item.grsaiWrapped) {
      const callback = item.callback;
      item.callback = function (...args) {
        const result = callback?.apply(this, args);
        state.previous = parameters(node);
        validateSelection(node, profile);
        return result;
      };
      item.grsaiWrapped = true;
    }
  }
  state.previous = parameters(node);
  validateSelection(node, profile);
  node.setDirtyCanvas(true, true);
}

function validateSelection(node, profile) {
  const invalid = Object.entries(profile.parameters).some(([name, rule]) =>
    !rule.options.some((option) => option.value === widget(node, `model.${name}`)?.value));
  const status = states.get(node).status;
  if (invalid) status.value = "配置错误：参数已删除或无效";
  else if (status.value.startsWith("配置错误")) status.value = "";
}

app.registerExtension({
  name: "grsai.image",
  async setup() {
    const response = await api.fetchApi("/grsai/config");
    if (!response.ok) throw new Error("GRSAI configuration unavailable");
    catalog = await response.json();
    for (const event of ["grsai.balance", "grsai.progress", "grsai.batch"]) {
      api.addEventListener(event, ({ detail }) => {
        // Tokens also isolate separate workflows that reuse numeric node IDs.
        const node = app.graph?.getNodeById(detail.node_id);
        const state = states.get(node);
        if (!state) return;
        syncKey(node);
        if (!acceptBalance(state, detail, node.properties.grsai_ui_token)) return;
        if (event === "grsai.balance") {
          state.balance.value = balanceText(detail);
          state.balance.options.tooltip = detail.message ?? "";
        } else if (event === "grsai.batch") {
          state.status.value = batchText(detail);
          state.status.options.tooltip = detail.directory ?? "";
          if (state.directory) state.directory.value = detail.directory ?? "";
        } else {
          state.status.value = ({ submitting: "提交中", running: "生成中", reconnecting: "正在重连",
            downloading: "下载中", succeeded: "已完成" })[detail.stage] ?? "";
        }
        node.setDirtyCanvas(true, true);
      });
    }
  },
  nodeCreated(node) {
    if (node.comfyClass === "GRSAILoadImagesFromFolder") {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "文件清单：未加载";
      const list = document.createElement("pre");
      list.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0;max-height:200px;overflow:auto";
      details.style.cssText = "padding:6px;color:var(--input-text);font-size:12px";
      details.append(summary, list);
      const filesWidget = node.addDOMWidget("grsai_files", "details", details, { serialize: false });
      filesWidget.serialize = false;
      const executed = node.onExecuted;
      node.onExecuted = function (message) {
        executed?.call(this, message);
        const files = message.grsai_files ?? [];
        summary.textContent = `文件清单：${files.length} 张`;
        list.textContent = files.map((name, i) => `${i + 1}. ${name}`).join("\n");
        node.setDirtyCanvas(true, true);
      };
      return;
    }
    if (!["GRSAIImageGenerate", "GRSAIBatchImageGenerate"].includes(node.comfyClass) || !catalog) return;
    node.properties ??= {};
    const balance = node.addWidget("text", "grsai_balance", "", () => {}, { serialize: false });
    const status = node.addWidget("text", "grsai_status", "", () => {}, { serialize: false });
    const directory = node.comfyClass === "GRSAIBatchImageGenerate"
      ? node.addWidget("text", "grsai_directory", "", () => {}, { serialize: false }) : null;
    for (const item of [balance, status, directory].filter(Boolean)) {
      item.serialize = false;
      item.disabled = true;
      item.options.readOnly = true;
      Object.defineProperty(item, "displayName", { configurable: true, get: () => "" });
      Object.defineProperty(item, "_displayValue", { configurable: true, get() { return String(this.value); } });
    }
    const state = { balance, status, directory, sequence: 0, previous: {}, configuring: false };
    states.set(node, state);
    const model = widget(node, "model");
    model.value = catalog.default_model;
    decorateParameters(node);
    resetBalance(node);

    if (directory) {
      const labelReferences = () => {
        for (const input of node.inputs ?? []) {
          const match = /^references\.reference_(\d+)$/.exec(input.name);
          if (match) input.label = `Reference ${match[1]}`;
        }
      };
      labelReferences();
      const connections = node.onConnectionsChange;
      node.onConnectionsChange = function (type, slot, connected, ...rest) {
        // Native Autogrow compacts disconnected middle inputs. Preserve image numbering instead.
        if (type === 1 && !connected && this.inputs?.[slot]?.name.startsWith("references.")) {
          this.setDirtyCanvas(true, true);
          return;
        }
        const result = connections?.call(this, type, slot, connected, ...rest);
        labelReferences();
        return result;
      };
    }

    const callback = model.callback;
    model.callback = function (...args) {
      const previous = state.previous;
      const result = callback?.apply(this, args);
      const profile = catalog.models[model.value];
      if (profile && !state.configuring) {
        for (const [name, value] of Object.entries(selectedParameters(profile, previous))) {
          const item = widget(node, `model.${name}`);
          if (item) item.value = value;
        }
        state.status.value = "";
      }
      decorateParameters(node);
      return result;
    };
    const key = widget(node, "api_key");
    const keyCallback = key.callback;
    key.callback = function (...args) {
      const result = keyCallback?.apply(this, args);
      syncKey(node);
      return result;
    };

    const configure = node.configure;
    node.configure = function (data) {
      state.configuring = true;
      try {
        const result = configure.call(this, data);
        const saved = data.properties?.grsai_selection;
        if (saved) {
          // Restore literal saved values, including invalid ones. Never silently migrate a workflow.
          model.value = saved.model;
          for (const [name, value] of Object.entries(saved.parameters ?? {})) {
            const item = widget(node, `model.${name}`);
            if (item) item.value = value;
          }
        }
        resetBalance(node);
        decorateParameters(node);
        return result;
      } finally {
        state.configuring = false;
      }
    };
    const serialize = node.onSerialize;
    node.onSerialize = function (data) {
      syncKey(node);
      serialize?.call(this, data);
      data.properties ??= {};
      data.properties.grsai_ui_token = node.properties.grsai_ui_token;
      data.properties.grsai_selection = { model: model.value, parameters: parameters(node) };
    };
  },
});
