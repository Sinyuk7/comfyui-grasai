import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { acceptEvent, balanceText, batchProgress, batchText, selectedParameters, taskProgress } from "../web/state.mjs";

test("model switches retain only legal values", () => {
  const profile = { parameters: { quality: { default: "medium", options: [{ value: "medium" }] }, aspectRatio: { default: "auto", options: [{ value: "auto" }, { value: "1:1" }] } } };
  assert.deepEqual(selectedParameters(profile, { quality: "high", aspectRatio: "1:1" }), { quality: "medium", aspectRatio: "1:1" });
});

test("batch plan and progress count final tasks, not bases", () => {
  assert.match(batchText({ stage: "planned", base_count: 10, prompt_count: 4, total: 40, concurrency: 4 }), /10.*4.*40.*4/);
  assert.match(batchText({ stage: "running", completed: 17, total: 40 }), /17 \/ 40/);
  assert.match(batchText({ stage: "interrupted", completed: 3, total: 40 }), /3 \/ 40/);
});

test("single task progress is honest across generation and download", () => {
  assert.deepEqual(taskProgress({ stage: "running", progress: 42 }), {
    value: 42, indeterminate: false, text: "Generating: 42%",
  });
  assert.equal(taskProgress({ stage: "running", progress: undefined }).indeterminate, true);
  assert.deepEqual(taskProgress({ stage: "downloading", completed: 1, total: 2 }), {
    value: 50, indeterminate: false, text: "Downloading: 1 / 2",
  });
});

test("batch progress combines completed tasks only with reported child progress", () => {
  const reported = batchProgress({
    stage: "running", completed: 1, total: 4, running: 2,
    active: [{ stage: "running", progress: 50 }, { stage: "running", progress: null }],
  });
  assert.equal(reported.value, 37.5);
  assert.equal(reported.indeterminate, false);
  const unknown = batchProgress({
    stage: "running", completed: 0, total: 1, running: 1,
    active: [{ stage: "running", progress: null }],
  });
  assert.equal(unknown.indeterminate, true);
  assert.match(unknown.text, /Running: 1.*Generating/);
});

test("late responses and changed configs cannot overwrite state", () => {
  const state = { sequence: 2 };
  assert.equal(acceptEvent(state, { sequence: 1, ui_token: "current" }, "current"), false);
  assert.equal(acceptEvent(state, { sequence: 3, ui_token: "old" }, "current"), false);
  assert.equal(acceptEvent(state, { sequence: 3, ui_token: "current" }, "current"), true);
  assert.equal(state.sequence, 3);
});

test("zero balance is valid and status is not serialized", () => {
  assert.match(balanceText({ state: "ready", credits: 0, queried_at: "2026-09-16T00:00:00Z" }), /: 0/);
  assert.match(balanceText({ state: "error" }), /unavailable/);
  assert.match(balanceText({ state: "unavailable" }), /Token not set/);
  assert.equal(balanceText({ state: "unsupported" }), "Balance: Not supported");
  const source = readFileSync(new URL("../web/image_api.js", import.meta.url), "utf8");
  assert.match(source, /serialize: false/);
  assert.doesNotMatch(source, /queuePrompt|Authorization|apiKey/);
});

test("provider UI uses generic surfaces and preserves provider selection", () => {
  const source = readFileSync(new URL("../web/image_api.js", import.meta.url), "utf8");
  assert.match(source, /image-api\.providers/);
  assert.match(source, /\/image-api\/catalog/);
  assert.match(source, /image-api\.balance/);
  assert.match(source, /saved\?\.provider/);
  assert.match(source, /startsWith\("rh:"\)/);
  assert.match(source, /syncProvider\(this\)/);
  assert.doesNotMatch(source, /GRSAI(?:APIConfig|ImageGenerate|LoadImagesFromFolder|BatchImageGenerate)/);
  assert.doesNotMatch(source, /grsai_(?:ui_token|selection|files|manifest)/);
});
