"""Opt-in real-frontend smoke check. Never queues a generation request."""

import argparse
import asyncio
import json

from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8197")
    parser.add_argument("--screenshot", default="/tmp/image-api-node-smoke.png")
    args = parser.parse_args()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        async def model_status(route):
            model = route.request.post_data_json.get("model")
            available = model != "nano-banana-pro"
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "available": available,
                                 "error": "Maintenance" if not available else ""}),
            )

        await page.route("**/image-api/model-status", model_status)
        await page.goto(args.url)
        await page.wait_for_function(
            "Boolean(window.app?.graph && window.LiteGraph?.registered_node_types?.SinyukImageAPIGenerate)",
            timeout=60000,
        )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(350)
        result = await page.evaluate("""async () => {
          const app = window.app;
          app.graph.clear();
          const config = window.LiteGraph.createNode('SinyukImageAPIConfig');
          const node = window.LiteGraph.createNode('SinyukImageAPIGenerate');
          app.graph.add(config); app.graph.add(node);
          config.connect(0, node, node.inputs.findIndex(i => i.name === 'api_config'));
          node.pos = [150, 150];
          node.size = [440, 420];
          const get = name => node.widgets.find(w => w.name === name);
          const set = (name, value) => { const w = get(name); w.value = value; w.callback?.(value); };
          const snapshot = () => node.widgets.map(w => ({name: w.name, value: w.value, disabled: w.disabled}));
          const initial = snapshot();
          set('model.aspectRatio', '16:9');
          set('model.imageSize', '2K');
          set('model', 'nano-banana-pro');
          await new Promise(r => setTimeout(r, 80));
          const modelWarning = document.body.innerText.includes('Model unavailable')
            || get('status').value.includes('Model unavailable');
          const retained = snapshot();
          set('model', 'gpt-image-2-vip');
          const vip = snapshot();
          const serialized = node.serialize();
          node.configure(serialized);
          const reloaded = snapshot();
          const { api } = await import('/scripts/api.js');
          const send = (payload) => api.dispatchEvent(new CustomEvent('image-api.balance', {detail: {node_id: node.id, ...payload}}));
          const token = node.properties.image_api_ui_token;
          send({ui_token: token, sequence: 10, state: 'ready', credits: 0, queried_at: '2026-09-16T00:00:00Z'});
          const zero = get('balance').value;
          send({ui_token: token, sequence: 9, state: 'error'});
          const afterOld = get('balance').value;
          const tokenWidget = config.widgets.find(w => w.name === 'token');
          tokenWidget.value = 'changed-token'; tokenWidget.callback?.('changed-token');
          send({ui_token: token, sequence: 11, state: 'ready', credits: 999, queried_at: '2026-09-16T00:00:00Z'});
          const afterConfigChange = get('balance').value;
          const invalid = node.serialize();
          invalid.properties.image_api_selection.parameters.aspectRatio = 'deleted-option';
          node.configure(invalid);
          const invalidParameter = get('model.aspectRatio').value;
          invalid.properties.image_api_selection.model = 'deleted-model';
          node.configure(invalid);
          const deletedModel = {model: get('model').value, status: get('status').value};
          const visibleError = get('progress').element.textContent;
          node.configure(serialized);
          const configGet = name => config.widgets.find(w => w.name === name);
          const configSet = (name, value) => { const w = configGet(name); w.value = value; w.callback?.(value); };
          configSet('base_url', 'https://grsai.example');
          configSet('provider', 'runninghub');
          const runningHubConfig = {
            baseUrl: configGet('base_url').value,
            tokenDisabled: configGet('token').disabled,
            tokenLabel: configGet('token').label,
            providerLabel: configGet('provider').options.getOptionLabel('runninghub'),
          };
          configSet('base_url', 'https://runninghub.example');
          configSet('provider', 'grsai');
          const restoredGrsaiBaseUrl = configGet('base_url').value;
          const providerConfigSerialized = JSON.parse(JSON.stringify(config.serialize()));
          node.configure(serialized);
          window.imageApiTestNode = node;
          app.canvas.ds.scale = 1;
          app.canvas.ds.offset = [0, 0];
          app.canvas.setDirty(true, true);
          await new Promise(requestAnimationFrame);
          await new Promise(requestAnimationFrame);
          return {initial, retained, vip, reloaded, serialized, zero, afterOld, afterConfigChange,
            invalidParameter, deletedModel, visibleError, modelWarning, runningHubConfig, restoredGrsaiBaseUrl,
            providerConfigSerialized,
            aspectPoint: [node.pos[0] + node.size[0] / 2,
              node.pos[1] + get('model.aspectRatio').last_y + 12]};
        }""")
        printable = {key: value for key, value in result.items() if key not in {"serialized", "providerConfigSerialized"}}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        print("PAGE_ERRORS", errors)

        def by_name(rows):
            return {row["name"]: row for row in rows}

        assert by_name(result["initial"])["model"]["value"] == "nano-banana-2"
        assert by_name(result["retained"])["model.aspectRatio"]["value"] == "16:9"
        assert by_name(result["retained"])["model.imageSize"]["value"] == "2K"
        assert result["modelWarning"] is True
        assert by_name(result["vip"])["model.quality"]["disabled"] is True
        assert "model.imageSize" not in by_name(result["vip"])
        assert by_name(result["reloaded"])["model.aspectRatio"]["value"] == "1024x1024"
        assert ": 0" in result["zero"] and result["afterOld"] == result["zero"]
        assert "Not checked" in result["afterConfigChange"]
        assert result["invalidParameter"] == "deleted-option"
        assert result["deletedModel"]["model"] == "deleted-model"
        assert "Configuration error" in result["deletedModel"]["status"]
        assert "Configuration error" in result["visibleError"]
        assert result["runningHubConfig"] == {
            "baseUrl": "", "tokenDisabled": True,
            "tokenLabel": "Token (GRSAI only)", "providerLabel": "RunningHub",
        }
        assert result["restoredGrsaiBaseUrl"] == "https://grsai.example"
        assert result["providerConfigSerialized"]["properties"]["image_api_base_urls"] == {
            "grsai": "https://grsai.example", "runninghub": "https://runninghub.example",
        }
        assert all("Balance:" not in str(value) for value in result["serialized"]["widgets_values"])
        await page.mouse.click(*result["aspectPoint"])
        expected_label = "1024x1024 (1:1, 1K)"
        option = page.get_by_role("menuitem", name=expected_label, exact=True)
        await option.wait_for()
        assert expected_label in await page.locator("body").inner_text()
        await option.click()
        prompt = await page.evaluate("app.graphToPrompt()")
        generated = next(value for value in prompt["output"].values()
                         if value["class_type"] == "SinyukImageAPIGenerate")
        assert generated["inputs"]["model.aspectRatio"] == "1024x1024"
        await page.screenshot(path=args.screenshot, full_page=True)
        await browser.close()
        assert not errors


if __name__ == "__main__":
    asyncio.run(main())
