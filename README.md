# ComfyUI GRSAI

ComfyUI V3 自定义节点，支持 Nano Banana 与 GPT Image 的异步图片生成、多图参考、文件夹批量任务、Prompt Variants、结果保存和 API Key 积分显示。单次规则见 [单次设计](docs/design.md)，批量规则见 [批量设计](docs/batch-design.md)。

## 安装

把整个目录放到 ComfyUI 的 `custom_nodes/comfyui-grasai`，在本目录中使用 **ComfyUI 自身的 Python 环境**安装依赖并重启 ComfyUI：

```sh
python -m pip install -r requirements.txt
```

可选地复制 `grsai_config.example.json` 为 `grsai_config.json`，再修改 `base_url`、模型和等待参数。用户配置优先，升级不会覆盖它。API Key 只在节点输入中提供，不写入配置文件。

`GRSAI Image` 应连接到 `Preview Image`、`Save Image` 等下游执行节点；`GRSAI Batch Image` 自带保存并注册为终端节点，无需额外连接 Save Image 才能执行。

## 批量工作流

新增节点位于 `GRSAI` 分类：

- `GRSAI Load Images From Folder`：读取 **ComfyUI Server 所在机器**的本地目录，支持 PNG/JPG/JPEG/WEBP，按文件名自然排序，不递归，不缩放或补边。每次工作流执行重新加载；损坏、透明或多帧图片报错，不跳过导致配对错位。
- `references` 输出同时携带图片与来源文件信息，接入 Batch 的 `Reference 1/2/...`；`images` 是普通 IMAGE list，供 Preview、Upscale 等使用。两个输出共享图片数据。
- `GRSAI Batch Image` 的 Reference 同时接受上述集合或普通 IMAGE list/batch。每列长度只允许 1 或共同的 N；单张共享，其他按位置配对。Reference 必须连续连接，中间空洞报错。
- `Prompt` 为单一提示词。可选 `Prompts` 接收上游 STRING list，非空时覆盖 Prompt；空数组回退。支持一个宿主包装的字符串数组，不解析 JSON 文本、不按换行拆分、不适配任意自定义 Prompt Array 类型。
- N 组图片与 M 个提示词始终生成 **T=N×M 个任务**，即使 N=M 也不会 1:1 配对。顺序是先图片组、再提示词；例如 10 组 × 4 个提示词为 40 个任务。
- `Max concurrency` 为 2–10，默认 4，限制最终生成任务数，包含提交、轮询、下载及保存；不是全局 Key 配额或 rate limiter。

例如人物一张、上衣十张、裤子十张，分别接 Reference 1、2、3。Prompt 不变则十个任务；接入四条 Prompts 则四十个任务。建议两套文件夹使用 `001_...`、`002_...` 等有序文件名；插件按排序后位置配对，不按文件名编号匹配，也不会检查服装语义是否配对正确。

`Output prefix = Clothes` 时，结果即时保存到：

```text
<ComfyUI output>/grsai/<UTC批次时间_唯一标识>/
  Clothes_001.png
  Clothes_002.png
  ...
  manifest.json
```

编号是 Base-major 展开后的任务号，不是完成顺序；多结果使用 `Clothes_014_01.png`、`Clothes_014_02.png`。失败留空号，不覆盖旧批次。manifest 记录 Base/Prompt/Task 索引、来源、Prompt 指纹、远端 ID、输出与错误，不记录 Key 或明文提示词。任务 ID 收到后立即持久化，manifest 单写者串行原子写入。

Batch 输出 `images`（成功图片按任务/结果顺序平铺）和 `manifest`（报告路径）。全失败时保留报告，并用宿主 ExecutionBlocker 跳过图片分支，避免空 IMAGE list 导致 Preview 报错；不返回占位图。部分成功正常输出已保存图片。完整状态以 manifest 为准，宿主流程完成不代表所有远端任务成功。

每次 Queue 都是新批次，**不会恢复旧批次或自动重生成失败项**。中断只停止本地等待，已经接受的远端任务可能继续计费。下游 Save Image 会额外保存副本，并可能嵌入含 Key 的工作流元数据；内部保存的 PNG 不附带工作流元数据。

本地 `batch_reference_limit` 默认 10，可在配置中设为 1–100（宿主 Autogrow 支持范围）；这是插件本地保护，不是 GRSAI 官方能力。profile 可增加 `max_reference_images` 和配套的 `reference_limit_source`，仅用于有可靠官方依据的上限。默认不编造模型官方上限。完整批量验证与未覆盖边界见 [批量验证记录](docs/batch-verification.md)。

## 单次节点与通用注意事项

- 默认包含设计确认的七个模型，参数随模型切换；合法参数保留，无效参数切换到新模型默认值。加载旧工作流不会静默修复已删除的模型或参数。
- 可选 `images` 接收一个批次或图片列表，按列表顺序、批次顺序展开为同一次生成的参考图，不逐张生成。提示词只接受一个字符串，可以连上游字符串节点。
- 输出是 IMAGE list，每项形状 `[1,H,W,3]`，不缩放、不补边。所有结果下载成功后才输出；非不透明 alpha 明确报错。
- 每次实际执行都创建新任务。POST 不自动重试；查询与下载失败不会触发重新生成。取消只停止本地等待，不能取消远端任务或退款。
- 默认不设总任务截止时间；单次 HTTP 请求有独立超时。`task_timeout_seconds` 可配置正数，`null` 表示无总截止时间。
- 生成结束后独立查询积分，不阻塞下游、不预先拒绝生成、不把余额差当作精确费用。界面余额不保存为工作流结果。
- **API Key 是普通工作流输入**，可能存在于工作流 JSON、历史及 Save Image 的 PNG 元数据中。分享前清理 Key；本插件不是安全密钥存储器。
- 不提供透明输出、遮罩、seed、上传服务、节点内生成按钮或跨版本兼容承诺。批量调度由独立 Batch 节点提供，不改变单次节点的多图共同参考语义。

更改配置后重启 ComfyUI 并刷新页面。新增配置组合仍需真实 API 验证，不能把示例选项视为上游能力保证。将 `base_url` 指向自己信任的 HTTPS 主机；本地测试可用 HTTP。

## 开发验证

```sh
python -m pip install pytest pytest-asyncio ruff
python -m pytest -q
node --test tests/test_web.mjs
python -m ruff check --config pyproject.toml .
COMFYUI_PATH=/path/to/ComfyUI python -m pytest -q

# 可选：先启动隔离的 ComfyUI，再运行前端测试。
python -m pip install playwright
python tests/browser_smoke.py --url http://127.0.0.1:8197
python tests/browser_batch_smoke.py --url http://127.0.0.1:8197
```

真实 ComfyUI V3 集成测试需要设置 `COMFYUI_PATH`。当前验证基于 ComfyUI `v0.36.0` 与前端 `1.52.7`；普通 pytest 不调用计费或生成服务。显式授权的真实测试入口见下节，首次结果见 [真实测试记录](docs/live-test-2026-09-16.md)。

前端测试使用本机 Chrome、全新浏览器上下文，仅创建/切换测试节点，不入队生成。验证详情见 [验证记录](docs/verification.md)。

## 真实 API 测试

`tests/live_case.json` 固定三张参考图的顺序、双语提示词和两个模型的 1K 参数。将对应图片放在 `tests/images/`，从项目根目录运行：

```sh
# 只验证输入和编码，不联网、不计费。
python tests/live_api.py --prepare-only

# 显式授权付费测试：隐藏输入 Key，每个模型只提交一次。
python tests/live_api.py --allow-paid --api-key-stdin
```

也支持从 `GRSAI_API_KEY` 环境变量读取凭据，以及 `--models nano-banana-2` / `--models gpt-image-2.5` 单独选择模型。普通 pytest 不执行真实生成。失败不会重发 POST，重新运行此命令会创建新的付费任务。

结果位于 `tests/outputs/<UTC时间>/`，包括无工作流元数据的 PNG 和 `report.json`。报告记录输入哈希、实际参数、任务 ID、请求次数、耗时和实际图片尺寸，不记录 Key、base64 或 CDN URL。输入图片和输出目录均被 Git 忽略。1K 是请求档位，不保证上游严格返回指定宽高；以报告中的实际尺寸为准。
