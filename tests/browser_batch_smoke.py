"""Real frontend schema/connection checks. Never queues paid generation."""

import argparse
import asyncio
import json

from playwright.async_api import async_playwright

TEST_KEY = "browser-fake-key-for-audit-only"


def key_paths(value, key, path=()):
    if isinstance(value, str):
        return [path] if key in value else []
    if isinstance(value, dict):
        return [p for name, child in value.items() for p in key_paths(child, key, (*path, name))]
    if isinstance(value, list):
        return [p for index, child in enumerate(value) for p in key_paths(child, key, (*path, index))]
    return []


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8197")
    parser.add_argument("--screenshot", default="/tmp/image-api-batch-smoke.png")
    args = parser.parse_args()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1560, "height": 1100})
        errors = []
        forbidden_posts = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def prevent_queue(route):
            if route.request.method == "POST":
                forbidden_posts.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/prompt", prevent_queue)
        await page.route("**/api/prompt", prevent_queue)
        await page.route("**/image-api/model-status", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"ok":true,"available":true,"error":""}',
        ))
        await page.goto(args.url)
        await page.wait_for_function(
            "Boolean(window.app?.graph && window.LiteGraph?.registered_node_types?.BatchImageGenerate)",
            timeout=60000,
        )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        result = await page.evaluate("""async (testKey) => {
          const app = window.app, make = name => {
            const node = window.LiteGraph.createNode(name); app.graph.add(node); return node;
          };
          app.graph.clear();
          const config = make('ImageAPIConfig');
          const folder = make('ImageAPILoadImagesFromFolder');
          const second = make('ImageAPILoadImagesFromFolder');
          const third = make('ImageAPILoadImagesFromFolder');
          const batch = make('BatchImageGenerate');
          config.connect(0, batch, batch.inputs.findIndex(i => i.name === 'api_config'));
          const promptsNode = make('PrimitiveStringMultiline');
          promptsNode.widgets.find(w => w.name === 'value').value = 'Standing fashion portrait';
          promptsNode.connect(0, batch, batch.inputs.findIndex(i => i.name === 'prompts'));
          promptsNode.pos = [40, 830]; promptsNode.size = [400, 150];
          folder.pos = [40, 110]; second.pos = [40, 350]; third.pos = [40, 590]; batch.pos = [620, 110];
          folder.size = [400, 170]; second.size = [400, 170]; third.size = [400, 170]; batch.size = [760, 720];
          folder.widgets.find(w => w.name === 'folder').value = '/photos/people';
          second.widgets.find(w => w.name === 'folder').value = '/photos/shirts';
          third.widgets.find(w => w.name === 'folder').value = '/photos/pants';
          const slot = number => batch.inputs.findIndex(i => i.name === `references.reference_${number}`);
          folder.connect(0, batch, slot(1));
          second.connect(1, batch, slot(2));
          third.connect(0, batch, slot(3));
          const connected = batch.inputs.map(i => ({name:i.name, type:i.type, link:i.link}));
          batch.disconnectInput(slot(2));
          await new Promise(r => setTimeout(r, 80));
          const hole = batch.inputs.map(i => ({name:i.name, link:i.link}));
          second.connect(1, batch, slot(2));
          const get = name => batch.widgets.find(w => w.name === name);
          const set = (name,value) => {const w=get(name); w.value=value;w.callback?.(value);};
          set('output_prefix', 'Clothes');
          config.widgets.find(w => w.name === 'api_key').value = testKey;
          set('prompt', 'Edit Image 1 using the clothing in Image 2 and Image 3.');
          set('model','gpt-image-2-vip');
          const quality = get('model.quality').value;
          set('model','nano-banana-2');
          const { api } = await import('/scripts/api.js');
          api.dispatchEvent(new CustomEvent('image-api.batch', {detail: {
            node_id:batch.id,ui_token:batch.properties.image_api_ui_token,sequence:100,
            stage:'planned',base_count:10,prompt_count:4,total:40,concurrency:4,
            directory:'output/image_api/example',completed:0,success:0,failed:0
          }}));
          const status=get('status').value;
          api.dispatchEvent(new CustomEvent('image-api.batch', {detail: {
            node_id:batch.id,ui_token:batch.properties.image_api_ui_token,sequence:101,
            stage:'running',base_count:10,prompt_count:4,total:40,concurrency:4,
            directory:'output/image_api/example',completed:17,success:16,failed:1,running:1,
            active:[{task_index:18,stage:'running',progress:42}]
          }}));
          const progressWidget=get('progress');
          const progressKnown={label:progressWidget.element.textContent,
            indeterminate:progressWidget.element.dataset.indeterminate,
            width:progressWidget.element.firstElementChild.firstElementChild.style.width};
          api.dispatchEvent(new CustomEvent('image-api.batch', {detail: {
            node_id:batch.id,ui_token:batch.properties.image_api_ui_token,sequence:102,
            stage:'running',base_count:1,prompt_count:1,total:1,concurrency:1,
            directory:'output/image_api/example',completed:0,success:0,failed:0,running:1,
            active:[{task_index:1,stage:'running',progress:null}]
          }}));
          const progressUnknown={label:progressWidget.element.textContent,
            indeterminate:progressWidget.element.dataset.indeterminate};
          folder.onExecuted({image_api_files:['001_person.png']});
          second.onExecuted({image_api_files:['001_shirt.png','002_shirt.png']});
          const widgets=batch.widgets.map(w=>({name:w.name,value:w.value}));
          const serialized=app.graph.serialize();
          await app.loadGraphData(serialized);
          const reloaded=app.graph._nodes.find(n=>n.comfyClass==='BatchImageGenerate');
          const reloadedInputs=reloaded.inputs.map(i=>({name:i.name,link:i.link}));
          const prefix=reloaded.widgets.find(w=>w.name==='output_prefix').value;
          const apiPrompt=await app.graphToPrompt();
          const batchPrompt=Object.values(apiPrompt.output).find(n=>n.class_type==='BatchImageGenerate');
          const reloadedConfig=app.graph._nodes.find(n=>n.comfyClass==='ImageAPIConfig');
          const reloadedKey=reloadedConfig.widgets.find(w=>w.name==='api_key').value;
          const reloadedFallback=reloaded.widgets.find(w=>w.name==='prompt').value;
          const promptsLink=batchPrompt.inputs.prompts;
          const connectedPrompt=apiPrompt.output[promptsLink[0]];
          reloaded.disconnectInput(reloaded.inputs.findIndex(i=>i.name==='prompts'));
          const disconnectedPrompt=Object.values((await app.graphToPrompt()).output)
            .find(n=>n.class_type==='BatchImageGenerate');
          const restoredPrompts=app.graph._nodes.find(n=>n.comfyClass==='PrimitiveStringMultiline');
          restoredPrompts.connect(0,reloaded,reloaded.inputs.findIndex(i=>i.name==='prompts'));
          const restoredKey=reloadedConfig.widgets.find(w=>w.name==='api_key');
          restoredKey.value=''; restoredKey.callback?.('');
          app.canvas.ds.scale=1;
          app.canvas.ds.offset=[0,0];
          reloaded.pos=[620,110];
          reloaded.size=[760,620];
          api.dispatchEvent(new CustomEvent('image-api.batch', {detail: {
            node_id:reloaded.id,ui_token:reloaded.properties.image_api_ui_token,sequence:101,
            stage:'planned',base_count:10,prompt_count:4,total:40,concurrency:4,
            directory:'output/image_api/example',completed:0,success:0,failed:0
          }}));
          for(const n of app.graph._nodes.filter(n=>n.comfyClass==='ImageAPILoadImagesFromFolder')) {
            n.onExecuted({image_api_files:['001.png','002.png']});
          }
          const setReloaded = (name,value) => {const w=reloaded.widgets.find(item=>item.name===name);
            w.value=value;w.callback?.(value);};
          setReloaded('model','gpt-image-2.5-sunburst');
          app.canvas.setDirty(true,true);
          await new Promise(requestAnimationFrame);
          await new Promise(requestAnimationFrame);
          return {connected,hole,quality,status,progressKnown,progressUnknown,widgets,serialized,reloadedInputs,prefix,batchPrompt,
            apiOutput:apiPrompt.output,reloadedKey,reloadedFallback,connectedPrompt,disconnectedPrompt,
            outputs:reloaded.outputs.map(o=>({name:o.name,type:o.type})),
            aspectPoint:[reloaded.pos[0]+reloaded.size[0]/2,
              reloaded.pos[1]+reloaded.widgets.find(w=>w.name==='model.aspectRatio').last_y+12]};
        }""", TEST_KEY)
        print(json.dumps(result, ensure_ascii=False, indent=2).replace(TEST_KEY, "[test-key]"))
        connected = {i["name"]: i for i in result["connected"]}
        assert all(connected[f"references.reference_{i}"]["link"] for i in (1, 2, 3))
        hole = {i["name"]: i for i in result["hole"]}
        assert hole["references.reference_2"]["link"] is None
        assert hole["references.reference_3"]["link"] == connected["references.reference_3"]["link"]
        assert result["quality"] == "medium"
        assert "40" in result["status"]
        assert "42%" in result["progressKnown"]["label"]
        assert result["progressKnown"]["indeterminate"] == "false"
        assert result["progressKnown"]["width"] == "43.55%"
        assert "Generating" in result["progressUnknown"]["label"]
        assert result["progressUnknown"]["indeterminate"] == "true"
        assert result["prefix"] == "Clothes"
        assert [o["name"] for o in result["outputs"]] == ["Images", "Manifest"]
        assert result["batchPrompt"]["inputs"]["max_concurrency"] == 4
        assert all(f"references.reference_{i}" in result["batchPrompt"]["inputs"] for i in (1, 2, 3))
        assert "status" not in result["batchPrompt"]["inputs"]
        assert not forbidden_posts
        assert result["reloadedKey"] == TEST_KEY
        assert result["connectedPrompt"]["class_type"] == "PrimitiveStringMultiline"
        assert result["connectedPrompt"]["inputs"]["value"] == "Standing fashion portrait"
        assert "prompts" not in result["disconnectedPrompt"]["inputs"]
        assert result["disconnectedPrompt"]["inputs"]["prompt"] == result["reloadedFallback"]
        assert key_paths(result["apiOutput"], TEST_KEY) == [
            (next(k for k, v in result["apiOutput"].items() if v["class_type"] == "ImageAPIConfig"),
             "inputs", "api_key")
        ]
        for key_path in key_paths(result["serialized"], TEST_KEY):
            assert key_path[0] == "nodes"
            assert result["serialized"]["nodes"][key_path[1]]["type"] == "ImageAPIConfig"
            assert key_path[2:] in [("widgets_values", 0), ("widgets_values_named", "api_key")]
        assert key_paths(result["serialized"], TEST_KEY)  # Confirm the documented workflow risk exists.
        for saved in result["serialized"]["nodes"]:
            if saved["type"] == "ImageAPILoadImagesFromFolder":
                assert "image_api_files" not in saved.get("widgets_values_named", {})
        await page.mouse.click(*result["aspectPoint"])
        expected_label = "1024x1024 (1:1, 1K)"
        option = page.get_by_role("menuitem", name=expected_label, exact=True)
        await option.wait_for()
        assert expected_label in await page.locator("body").inner_text()
        await option.click()
        prompt = await page.evaluate("app.graphToPrompt()")
        generated = next(value for value in prompt["output"].values()
                         if value["class_type"] == "BatchImageGenerate")
        assert generated["inputs"]["model.aspectRatio"] == "1024x1024"
        await page.evaluate("""async () => {
          const batch=app.graph._nodes.find(n=>n.comfyClass==='BatchImageGenerate');
          const {api}=await import('/scripts/api.js');
          api.dispatchEvent(new CustomEvent('image-api.batch', {detail: {
            node_id:batch.id,ui_token:batch.properties.image_api_ui_token,sequence:102,
            stage:'running',base_count:10,prompt_count:4,total:40,concurrency:4,
            directory:'output/image_api/example',completed:17,success:16,failed:1,running:1,
            active:[{task_index:18,stage:'running',progress:42}]
          }}));
        }""")
        await page.screenshot(path=args.screenshot, full_page=True)
        print("PAGE_ERRORS", errors)
        await browser.close()
        assert not errors


if __name__ == "__main__":
    asyncio.run(main())
