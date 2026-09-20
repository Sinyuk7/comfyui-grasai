import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { acceptBalance, balanceText, batchText, selectedParameters } from "../web/state.mjs";

test("model switches retain only legal values", () => {
  const profile = { parameters: { quality: { default: "medium", options: [{ value: "medium" }] }, aspectRatio: { default: "auto", options: [{ value: "auto" }, { value: "1:1" }] } } };
  assert.deepEqual(selectedParameters(profile, { quality: "high", aspectRatio: "1:1" }), { quality: "medium", aspectRatio: "1:1" });
});

test("batch plan and progress count final tasks, not bases", () => {
  assert.match(batchText({ stage: "planned", base_count: 10, prompt_count: 4, total: 40, concurrency: 4 }), /10.*4.*40.*4/);
  assert.match(batchText({ stage: "running", completed: 17, total: 40 }), /17 \/ 40/);
  assert.match(batchText({ stage: "interrupted", completed: 3, total: 40 }), /3 \/ 40/);
});

test("late responses and changed keys cannot overwrite state", () => {
  const state = { sequence: 2 };
  assert.equal(acceptBalance(state, { sequence: 1, ui_token: "current" }, "current"), false);
  assert.equal(acceptBalance(state, { sequence: 3, ui_token: "old" }, "current"), false);
  assert.equal(acceptBalance(state, { sequence: 3, ui_token: "current" }, "current"), true);
  assert.equal(state.sequence, 3);
});

test("zero balance is valid and status is not serialized", () => {
  assert.match(balanceText({ state: "ready", credits: 0, queried_at: "2026-09-16T00:00:00Z" }), /：0/);
  assert.match(balanceText({ state: "error" }), /失败/);
  const source = readFileSync(new URL("../web/grsai.js", import.meta.url), "utf8");
  assert.match(source, /serialize: false/);
  assert.doesNotMatch(source, /queuePrompt|Authorization|apiKey/);
});
