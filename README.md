# ComfyUI Image API

面向图片 API 中转站的 ComfyUI V3 通用节点。目前支持 GRSAI 与 RunningHub，并为后续 Provider 保留统一的节点、界面和执行边界。功能包括基于参考图的异步生成、多图参考、文件夹批量任务、Prompt Variants、结果保存，以及 Provider 支持时的账户积分显示。

## 安装

把整个目录放到 ComfyUI 的 `custom_nodes/comfyui-grasai`，在本目录中使用 **ComfyUI 自身的 Python 环境**安装依赖并重启 ComfyUI：

```sh
python -m pip install -r requirements.txt
```

可选地复制 `grsai_config.example.json` 为 `grsai_config.json`，再修改 GRSAI 默认 `base_url`、模型和等待参数。用户配置优先，升级不会覆盖它。RunningHub 使用随插件发布并严格校验的 `runninghub_config.json` 固定目录，暂不支持在界面中任意扩展模型。

工作流中的 `API Config` 节点提供 `Provider`、API Key、可选 Base URL 和可选账户 Token。Provider 可选 GRSAI 或 RunningHub；每个 Provider 分别保留自己的 Base URL，留空时使用默认地址。Token 仅用于 GRSAI 余额查询，在 RunningHub 下自动禁用。

节点 ID 为 `SinyukImageAPIConfig`、`SinyukImageAPIGenerate`、`SinyukImageAPIBatchGenerate` 和 `SinyukImageAPILoadFolder`。项目不注册旧的 Provider 专属节点 ID；工作流统一使用通用节点和显式 Provider 配置。

`Image Generate` 应连接到 `Preview Image`、`Save Image` 等下游执行节点；`Batch Image Generate` 自带保存并注册为终端节点，无需额外连接 Save Image 才能执行。

## 批量工作流

节点位于 `Image API` 分类，界面不绑定特定域名或中转站名称：

- `API Config`：保存本工作流共用的 Provider 和连接信息。API Key 用于生成；Token 只用于 GRSAI 账户余额查询；Base URL 可指向所选 Provider 的兼容可信地址。
- `Load Images From Folder`：读取 **ComfyUI Server 所在机器**的本地目录，支持 PNG/JPG/JPEG/WEBP，按文件名自然排序，不递归，不缩放或补边。每次工作流执行重新加载；损坏、透明或多帧图片报错，不跳过导致配对错位。
- `references` 输出同时携带图片与来源文件信息，接入 Batch 的 `Reference 1/2/...`；`images` 是普通 IMAGE list，供 Preview、Upscale 等使用。两个输出共享图片数据。
- `Batch Image Generate` 的 Reference 同时接受上述集合或普通 IMAGE list/batch。每列长度只允许 1 或共同的 N；单张共享，其他按位置配对。Reference 必须连续连接，中间空洞报错。
- `Prompt` 为单一提示词。可选 `Prompts` 接收上游 STRING list，非空时覆盖 Prompt；空数组回退。支持一个宿主包装的字符串数组，不解析 JSON 文本、不按换行拆分、不适配任意自定义 Prompt Array 类型。
- N 组图片与 M 个提示词始终生成 **T=N×M 个任务**，即使 N=M 也不会 1:1 配对。顺序是先图片组、再提示词；例如 10 组 × 4 个提示词为 40 个任务。
- `Max Concurrency` 为 2–10，默认 4，限制最终生成任务数，包含提交、轮询、下载及保存；不是全局 Key 配额或 rate limiter。

例如人物一张、上衣十张、裤子十张，分别接 Reference 1、2、3。Prompt 不变则十个任务；接入四条 Prompts 则四十个任务。建议两套文件夹使用 `001_...`、`002_...` 等有序文件名；插件按排序后位置配对，不按文件名编号匹配，也不会检查服装语义是否配对正确。

`Output prefix = Clothes` 时，结果即时保存到：

```text
<ComfyUI output>/image_api/<UTC批次时间_唯一标识>/
  Clothes_001.png
  Clothes_002.png
  ...
  manifest.json
```

编号是 Base-major 展开后的任务号，不是完成顺序；多结果使用 `Clothes_014_01.png`、`Clothes_014_02.png`。失败留空号，不覆盖旧批次。manifest 记录 Base/Prompt/Task 索引、来源、Prompt 指纹、远端 ID、输出与错误，不记录 Key 或明文提示词。任务 ID 收到后立即持久化，manifest 单写者串行原子写入。

Batch 输出 `images`（成功图片按任务/结果顺序平铺）和 `manifest`（报告路径）。全失败时保留报告，并用宿主 ExecutionBlocker 跳过图片分支，避免空 IMAGE list 导致 Preview 报错；不返回占位图。部分成功正常输出已保存图片。完整状态以 manifest 为准，宿主流程完成不代表所有远端任务成功。

每次 Queue 都是新批次，**不会恢复旧批次或自动重生成失败项**。中断只停止本地等待，已经接受的远端任务可能继续计费。下游 Save Image 会额外保存副本，并可能嵌入含 Key 的工作流元数据；内部保存的 PNG 不附带工作流元数据。

两个 Provider 每次都必须提供 1–10 张参考图。GRSAI 继续使用本地传输保护：单张图片先以最高 PNG 压缩重编码，仍超过 10,000,000 bytes 时保持宽高比自动缩小到限制内；全部参考图总计不超过 50,000,000 bytes。RunningHub 通过独立的二进制上传接口发送 PNG，不套用未经其官方契约确认的 GRSAI 字节限制；上传大小不被服务接受时会返回 RunningHub 的实际错误。`batch_reference_limit` 即使配置得更高，生成请求也仍以 10 张为上限。

## 单次节点与通用注意事项

- GRSAI 保留原有七个模型。RunningHub 固定提供 Nano Banana 2、Nano Banana Pro、GPT Image 2.5 Flare、GPT Image 2.5 Sunburst，各含 Economy 与 Stable，共八个条目；默认是 `Nano Banana 2 - Stable`。
- GPT Image 2.5 Stable 条目提供 Aspect Ratio、Resolution、Quality，并固定发送 `background=opaque`、`outputFormat=png`。RunningHub 的具体模型由请求 path 决定，不在 payload 中发送模型名。
- RunningHub 条目与 path 依据官方 [ComfyUI_RH_OpenAPI 模型注册表](https://github.com/HM-RunningHub/ComfyUI_RH_OpenAPI/tree/8f9c858e7e631a1c1c49df0c4defd77fdd690dd4) 固定版本整理；上游变更时应审查后更新本地目录，不自动发现或无限扩展。
- 参数随模型切换；合法参数保留，无效参数切换到新模型默认值。加载工作流不会静默修复已删除的模型或参数。
- 必填 `images` 接收一个批次或图片列表，按列表顺序、批次顺序展开为同一次生成的 1–10 张参考图，不逐张生成。提示词只接受一个字符串，可以连上游字符串节点。
- 输出是 IMAGE list，每项形状 `[1,H,W,3]`，不缩放、不补边。所有结果下载成功后才输出；非不透明 alpha 明确报错。
- 每次实际执行都创建新任务。POST 不自动重试；查询与下载失败不会触发重新生成。取消只停止本地等待，不能取消远端任务或退款。
- 默认不设总任务截止时间；单次 HTTP 请求有独立超时。`task_timeout_seconds` 可配置正数，`null` 表示无总截止时间。
- GRSAI 提供 Token 时，生成结束后通过 `/client/openapi/getCredits` 独立查询账户积分；不提供 Token 时不查询。RunningHub 界面显示 `Balance: Not supported`。余额不阻塞下游、不预先拒绝生成，也不保存为工作流结果。
- GRSAI 切换模型时通过 `/client/common/getModelStatus` 做非阻塞状态检查。正常或查询失败时不显示；只有接口明确返回不可用时才提示，且不会阻止生成。RunningHub 暂无余额和模型状态检查。为避免打开工作流就访问任意地址，GRSAI 状态查询仅允许官方 Host 或服务器 JSON 中配置的默认 Base URL。
- **API Key 和 Token 都是普通工作流输入**，可能存在于工作流 JSON、历史及 Save Image 的 PNG 元数据中。分享前清理凭据；本插件不是安全密钥存储器。
- 不提供透明输出、遮罩、seed、上传服务、节点内生成按钮或跨版本兼容承诺。批量调度由独立 Batch 节点提供，不改变单次节点的多图共同参考语义。

更改配置后重启 ComfyUI 并刷新页面。新增配置组合仍需真实 API 验证，不能把示例选项视为上游能力保证。将 `base_url` 指向自己信任的 HTTPS 主机；本地测试可用 HTTP。

## 诊断日志

插件在 ComfyUI 用户目录的 `__image_api/logs/image_api.log` 保存生成、远端任务、重连和失败等关键事件。
日志达到 2 MiB 后自动轮转，保留 4 份旧文件，总量约 10 MiB。日志不记录 API Key、Token、
明文 Prompt、图片数据或完整请求体；批量任务的完整结果映射仍以各批次的 `manifest.json` 为准。

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

真实 ComfyUI V3 集成测试需要设置 `COMFYUI_PATH`。当前验证基于 ComfyUI `v0.36.0` 与前端 `1.52.7`；普通 pytest 不调用计费或生成服务。显式授权的真实测试入口见下节。

前端测试使用本机 Chrome、全新浏览器上下文，仅创建/切换测试节点，不入队生成。

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
