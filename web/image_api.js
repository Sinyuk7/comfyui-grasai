import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { acceptEvent, balanceText, batchProgress, batchText, selectedParameters, taskProgress } from "./state.mjs";

let catalog;
const states = new WeakMap();
const widget = (node, name) => node?.widgets?.find((item) => item.name === name);
const parameters = (node) => Object.fromEntries((node.widgets ?? [])
  .filter((item) => item.name.startsWith("model."))
  .map((item) => [item.name.slice(6), item.value]));

function progressElement() {
  const root = document.createElement("div");
  const track = document.createElement("div");
  const fill = document.createElement("div");
  const label = document.createElement("div");
  root.style.cssText = "box-sizing:border-box;width:100%;height:38px;padding:5px 0;color:var(--input-text);font-size:12px";
  track.style.cssText = "position:relative;height:8px;overflow:hidden;border-radius:3px;background:rgba(255,255,255,.12)";
  fill.style.cssText = "height:100%;width:0;background:#3b9cff;transition:width 180ms ease";
  label.style.cssText = "height:20px;line-height:20px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  track.append(fill);
  root.append(track, label);
  return { root, fill, label };
}

function renderProgress(state, view) {
  state.progress.label.textContent = view.text || "Idle";
  state.progress.root.dataset.indeterminate = String(view.indeterminate);
  state.progress.animation?.cancel();
  state.progress.animation = null;
  state.progress.fill.style.width = view.indeterminate ? "35%" : `${Math.max(0, Math.min(100, view.value ?? 0))}%`;
  state.progress.fill.style.opacity = view.indeterminate ? "0.55" : "1";
  state.progress.fill.style.transform = "none";
  if (view.indeterminate) {
    state.progress.animation = state.progress.fill.animate(
      [{ transform: "translateX(-110%)" }, { transform: "translateX(300%)" }],
      { duration: 1200, iterations: Infinity, easing: "ease-in-out" },
    );
  }
}

function resetRemoteState(node, provider = providerId(node)) {
  const state = states.get(node);
  if (!state) return;
  node.properties.image_api_ui_token = crypto.randomUUID?.()
    ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, "0")).join("");
  state.modelRequest += 1;
  state.modelAbort?.abort();
  state.modelAbort = null;
  state.sequence = 0;
  state.balance.value = provider === "runninghub" ? balanceText({ state: "unsupported" }) : balanceText({});
  state.status.value = "";
  renderProgress(state, { value: 0, indeterminate: false, text: "Idle" });
  if (state.directory) state.directory.value = "";
  node.setDirtyCanvas(true, true);
}

function connectedConfigNode(node) {
  const input = node.inputs?.find((item) => item.name === "api_config");
  const link = input?.link == null ? null : app.graph?.links?.[input.link];
  return link ? app.graph?.getNodeById(link.origin_id) : null;
}

function baseUrl(node) {
  return String(widget(connectedConfigNode(node), "base_url")?.value ?? "").trim() || activeCatalog(node).base_url;
}

function providerId(node) {
  const value = widget(connectedConfigNode(node), "provider")?.value;
  return value === "runninghub" ? "runninghub" : "grsai";
}

function activeCatalog(node, provider = providerId(node)) {
  return catalog.providers[provider] ?? catalog.providers.grsai;
}

function syncProvider(node, providerIdOverride = providerId(node)) {
  const provider = activeCatalog(node, providerIdOverride);
  const model = widget(node, "model");
  if (!model) return;
  const ids = Object.keys(provider.models);
  model.options.values = ids;
  model.options.getOptionLabel = (value) => provider.models[value]?.label ?? value;
  if (!ids.includes(model.value)) model.value = provider.default_model;
  decorateParameters(node, providerIdOverride);
  resetRemoteState(node, providerIdOverride);
}

function resetConfigConsumers(configNode) {
  for (const linkId of configNode.outputs?.[0]?.links ?? []) {
    const link = app.graph?.links?.[linkId];
    const target = link ? app.graph?.getNodeById(link.target_id) : null;
    if (states.has(target)) syncProvider(target);
  }
}

function modelWarning(node, message) {
  const detail = message || "The selected model is currently unavailable.";
  const toast = app.extensionManager?.toast;
  if (toast?.add) {
    toast.add({ severity: "warn", summary: "Model unavailable", detail, life: 6000 });
    return;
  }
  const state = states.get(node);
  state.status.value = `Model unavailable: ${detail}`;
  state.status.options.tooltip = detail;
  node.setDirtyCanvas(true, true);
}

async function checkModel(node, model) {
  if (providerId(node) !== "grsai") return;
  const state = states.get(node);
  const request = ++state.modelRequest;
  state.modelAbort?.abort();
  state.modelAbort = new AbortController();
  try {
    const response = await api.fetchApi("/image-api/model-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, base_url: baseUrl(node) }),
      signal: state.modelAbort.signal,
    });
    if (!response.ok || request !== state.modelRequest) return;
    const result = await response.json();
    if (result.ok && result.available === false) modelWarning(node, result.error);
  } catch (error) {
    if (error?.name !== "AbortError") console.debug("Model status check unavailable");
  }
}

function decorateParameters(node, provider = providerId(node)) {
  const state = states.get(node);
  const profile = activeCatalog(node, provider).models[widget(node, "model")?.value];
  if (!profile) {
    state.status.value = "Configuration error: model removed or disabled";
    return;
  }
  for (const [name, rule] of Object.entries(profile.parameters)) {
    const item = widget(node, `model.${name}`);
    if (!item) continue;
    item.disabled = rule.options.length === 1;
    item.label = name === "aspectRatio" ? (profile.family === "gpt_image" ? "Image Size" : "Aspect Ratio")
      : ["imageSize", "resolution"].includes(name) ? "Resolution" : "Quality";
    const labels = Object.fromEntries(rule.options.map((option) => [option.value, option.label]));
    // Keep the options object stable because mounted ComfyUI widgets may retain its reference.
    item.options.disabled = item.disabled;
    item.options.getOptionLabel = (value) => labels[value] ?? value;
    // The canvas host hides disabled widget values by default. Keep fixed choices visible.
    Object.defineProperty(item, "_displayValue", {
      configurable: true,
      get() { return labels[this.value] ?? String(this.value); },
    });
    if (!item.imageApiWrapped) {
      const callback = item.callback;
      item.callback = function (...args) {
        const result = callback?.apply(this, args);
        state.previous = parameters(node);
        validateSelection(node, profile);
        return result;
      };
      item.imageApiWrapped = true;
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
  if (invalid) status.value = "Configuration error: parameter removed or invalid";
  else if (status.value.startsWith("Configuration error")) status.value = "";
}

app.registerExtension({
  name: "image-api.providers",
  async setup() {
    const response = await api.fetchApi("/image-api/catalog");
    if (!response.ok) throw new Error("Image API configuration unavailable");
    catalog = await response.json();
    for (const event of ["image-api.balance", "image-api.progress", "image-api.batch"]) {
      api.addEventListener(event, ({ detail }) => {
        // Tokens also isolate separate workflows that reuse numeric node IDs.
        const node = app.graph?.getNodeById(detail.node_id);
        const state = states.get(node);
        if (!state) return;
        if (!acceptEvent(state, detail, node.properties.image_api_ui_token)) return;
        if (event === "image-api.balance") {
          state.balance.value = balanceText(detail);
          state.balance.options.tooltip = detail.message ?? "";
        } else if (event === "image-api.batch") {
          state.status.value = batchText(detail);
          renderProgress(state, batchProgress(detail));
          state.status.options.tooltip = detail.directory ?? "";
          if (state.directory) state.directory.value = detail.directory ?? "";
        } else {
          const view = taskProgress(detail);
          state.status.value = view.text;
          renderProgress(state, view);
        }
        node.setDirtyCanvas(true, true);
      });
    }
  },
  nodeCreated(node) {
    if (node.comfyClass === "ImageAPILoadImagesFromFolder") {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Files: Not loaded";
      const list = document.createElement("pre");
      list.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0;max-height:200px;overflow:auto";
      details.style.cssText = "padding:6px;color:var(--input-text);font-size:12px";
      details.append(summary, list);
      const filesWidget = node.addDOMWidget("image_api_files", "details", details, { serialize: false });
      filesWidget.serialize = false;
      const executed = node.onExecuted;
      node.onExecuted = function (message) {
        executed?.call(this, message);
        const files = message.image_api_files ?? [];
        summary.textContent = `Files: ${files.length}`;
        list.textContent = files.map((name, i) => `${i + 1}. ${name}`).join("\n");
        node.setDirtyCanvas(true, true);
      };
      return;
    }
    if (node.comfyClass === "ImageAPIConfig") {
      for (const name of ["api_key", "base_url", "token", "provider"]) {
        const item = widget(node, name);
        if (!item) continue;
        const callback = item.callback;
        item.callback = function (...args) {
          const result = callback?.apply(this, args);
          resetConfigConsumers(node);
          return result;
        };
      }
      return;
    }
    if (!["ImageGenerate", "BatchImageGenerate"].includes(node.comfyClass) || !catalog) return;
    node.properties ??= {};
    const balance = node.addWidget("text", "balance", "", () => {}, { serialize: false });
    const status = node.addWidget("text", "status", "", () => {}, { serialize: false });
    const progress = progressElement();
    const progressWidget = node.addDOMWidget("progress", "div", progress.root, { serialize: false });
    progressWidget.serialize = false;
    const directory = node.comfyClass === "BatchImageGenerate"
      ? node.addWidget("text", "output_directory", "", () => {}, { serialize: false }) : null;
    for (const item of [balance, status, directory].filter(Boolean)) {
      item.serialize = false;
      item.disabled = true;
      item.options.readOnly = true;
      Object.defineProperty(item, "displayName", { configurable: true, get: () => "" });
      Object.defineProperty(item, "_displayValue", { configurable: true, get() { return String(this.value); } });
    }
    // Progress DOM owns the visible execution status. Keep the legacy status
    // widget as a compatibility surface without wasting a blank row.
    status.type = "hidden";
    status.computeSize = () => [0, -4];
    status.draw = () => {};
    const state = { balance, status, progress, directory, sequence: 0, previous: {}, configuring: false,
      modelRequest: 0, modelAbort: null };
    states.set(node, state);
    const model = widget(node, "model");
    model.value = activeCatalog(node).default_model;
    model.options.values = Object.keys(activeCatalog(node).models);
    model.options.getOptionLabel = (value) => activeCatalog(node).models[value]?.label ?? value;
    decorateParameters(node);
    resetRemoteState(node);

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
        if (this.inputs?.[slot]?.name === "api_config") syncProvider(this);
        labelReferences();
        return result;
      };
    } else {
      const connections = node.onConnectionsChange;
      node.onConnectionsChange = function (type, slot, connected, ...rest) {
        const result = connections?.call(this, type, slot, connected, ...rest);
        if (this.inputs?.[slot]?.name === "api_config") syncProvider(this);
        return result;
      };
    }

    const callback = model.callback;
    model.callback = function (...args) {
      const previous = state.previous;
      const result = callback?.apply(this, args);
      const profile = activeCatalog(node).models[model.value];
      if (profile && !state.configuring) {
        for (const [name, value] of Object.entries(selectedParameters(profile, previous))) {
          const item = widget(node, `model.${name}`);
          if (item) item.value = value;
        }
        state.status.value = "";
        void checkModel(node, model.value);
      }
      decorateParameters(node);
      return result;
    };
    const configure = node.configure;
    node.configure = function (data) {
      state.configuring = true;
      try {
        const result = configure.call(this, data);
        const saved = data.properties?.image_api_selection;
        if (saved) {
          // Restore literal saved values, including invalid ones. Never silently migrate a workflow.
          model.value = saved.model;
          for (const [name, value] of Object.entries(saved.parameters ?? {})) {
            const item = widget(node, `model.${name}`);
            if (item) item.value = value;
          }
        }
        const savedProvider = saved?.provider
          ?? (saved?.model?.startsWith("rh:") ? "runninghub" : providerId(node));
        syncProvider(node, savedProvider);
        return result;
      } finally {
        state.configuring = false;
      }
    };
    const serialize = node.onSerialize;
    node.onSerialize = function (data) {
      serialize?.call(this, data);
      data.properties ??= {};
      data.properties.image_api_ui_token = node.properties.image_api_ui_token;
      data.properties.image_api_selection = {
        provider: providerId(node), model: model.value, parameters: parameters(node),
      };
    };
  },
});
