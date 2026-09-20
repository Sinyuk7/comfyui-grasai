# 接口核查与排查笔记

核查日期：2026-09-16。证据为 [本地原始快照](references/README.md)。以下“确认”均指文档确认，尚未经真实 API 调用验证。

## 1. 请求约定

虽然文档有三页，实际只有两个 HTTP 路径：

| 用途 | 方法与路径 | 关键内容 |
| --- | --- | --- |
| Nano Banana / GPT Image 提交 | `POST /v1/api/generate` | JSON 请求体；`model`、`prompt`；可带 `images` 与模型参数 |
| 查询既有任务 | `GET /v1/api/result?id=<task_id>` | 查询参数名为 `id`，不是 JSON body |

认证：`Authorization: Bearer <api_key>`。全球地址 `https://grsaiapi.com`；国内地址 `https://grsai.dakka.com.cn`。

`replyType` 支持 `json`、`stream`、`async`；本项目选 `async`。`images` 是字符串数组，文档支持 base64 或 URL，无需另接图片上传接口。未连接图片时拟发 `images: []`。

原始定义：[Nano Banana](references/snapshots/2026-09-16T124326Z/grsai/nano-banana.md)、[GPT Image](references/snapshots/2026-09-16T124326Z/grsai/gpt-image.md)、[结果查询](references/snapshots/2026-09-16T124326Z/grsai/result.md)。

## 2. Nano Banana

文档列出的模型字符串：

```text
nano-banana
nano-banana-fast
nano-banana-2
nano-banana-2-cl
nano-banana-2-2k-cl
nano-banana-2-4k-cl
nano-banana-pro
nano-banana-pro-vt
nano-banana-pro-cl
nano-banana-pro-vip
nano-banana-pro-4k-vip
```

尺寸分成 `aspectRatio` 和 `imageSize` 两个字段；分辨率列为 `1K`、`2K`、`4K`。通用比例为 `auto`、`1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`、`5:4`、`4:5`、`21:9`；Nano Banana 2 系列另列 `1:4`、`4:1`、`1:8`、`8:1`。

文档没有逐个说明模型与分辨率的兼容矩阵，不能从 `-2k-` / `-4k-` 名称推断传参行为；配置中的组合是待验证的选择，不能称为全部模型实测支持。

本项目默认仅启用 Nano Banana 2、Pro 两个 Nano 型号；上面的完整列表是上游文档记录，不代表本项目默认模型名单。

## 3. GPT Image

| 模型字符串 | 文档尺寸规则 | 文档质量选项 | 透明背景 |
| --- | --- | --- | --- |
| `gpt-image-2` | 比例或 1K 像素值 | `auto` | 未列为支持 |
| `gpt-image-2.5` | 比例或 1K 像素值 | `auto` | 未列为支持 |
| `gpt-image-2-vip` | 1–4K 像素值，不支持比例 | `medium` | 支持 |
| `gpt-image-2.5-flare` | 1–4K 像素值，不支持比例 | `low`、`medium`、`high` | 支持 |
| `gpt-image-2.5-sunburst` | 1–4K 像素值，不支持比例 | `low`、`medium`、`high`、`xhigh`、`max` | 支持 |

实际字段仍叫 `aspectRatio`，即使值为 `2048x2048`；不要改发 `size`，也不要套用 Nano Banana 的 `imageSize`。

VIP 自定义像素值约束：两边均为 16 的倍数，最长边 ≤ 3840，长短边比 ≤ 3，面积范围 655,360–8,294,400。该约束段落写“仅限 vip 模型”，没有明确是否也强制适用于 flare/sunburst；第一版使用原文提供的固定像素值，不开放自由宽高输入。

“4K 方图”示例实际为 `2880x2880`，不能由标签自动生成 `4096x4096`。横竖图亦直接使用配置中的像素字符串，不根据比例自行计算。

## 4. 异步结果

提交异步任务的示例为 `{"id":"...","status":"running"}`；成功结果的 `results` 为数组，元素含 `url`。解析顶层结构，不预设 `data` 包装。

| status | 项目处理 |
| --- | --- |
| `running` | 等待后继续 GET 查询同一个 ID |
| `succeeded` | 校验非空 `results`，下载并解码图片 |
| `failed` | 停止，显示清理后的 `error` 与 task ID |
| `violation` | 停止，显示上游拒绝原因，不自动换词重提 |
| 未知值 | 报告协议不匹配，不无限等待 |

`progress` 为可选 0–100；不保证每次有值。HTTP 400 示例也有 `id`、`status`、`error`，错误解析应尽量保留这些诊断字段。HTTP 200 不能直接视为生成成功。

## 5. 原始文档中的矛盾与缺口

| 编号 | 发现 | 当前处理 |
| --- | --- | --- |
| D01 | 网页 cURL 出现 `https://v1/api/generate` 之类缺主机示例 | 以页面列出的完整基础地址拼接固定路径 |
| D02 | GPT 示例给普通 `gpt-image-2` 传 `background: transparent`，字段说明却只列 VIP/flare/sunburst | 按字段说明处理；第一版不发 background |
| D03 | OpenAPI 将 Authorization 和查询 ID 标 optional | 本项目提交和查询时要求有效 Key；查询要求 ID |
| D04 | 只说明支持 base64，未说明必须裸 base64 还是 Data URL | 参考项目实际代码使用裸 PNG base64，但它调用旧接口；新接口暂沿用，保留配置项并实测；失败不自动再次 POST |
| D05 | 无参考图数量、单图字节、总请求大小上限 | 不编造平台限制；本地保护阈值独立标注，可配置 |
| D06 | 无推荐轮询频率、任务寿命、结果 URL 有效期 | 使用可配置工程默认值；成功立即下载，不宣称链接长期有效 |
| D07 | 无 seed、生成张数、取消任务、幂等键契约 | 不发送这些字段；不承诺远端取消或请求幂等 |
| D08 | GPT 异步 schema 多出无说明字段 `01KQS7HP0FA36FTFVEPEF2D10R` | 忽略未知附加字段，不把它设为业务输入 |
| D09 | Nano Banana 未逐模型列出分辨率能力 | 保留原始模型清单，已发布配置需逐组验证 |
| D10 | VIP 一段说“不支持比例”，参考列表又包含 `auto` | 示例尺寸配置先用明确像素值，不依赖 VIP 的 auto 行为 |

这些缺口无需用户猜测；真实联调时记录模型、配置版本、HTTP 状态及任务 ID，即可逐项确认。

## 6. 后续排查顺序

1. 本地配置加载失败：检查 JSON、重复模型、尺寸引用和默认值，尚不应有 API 请求。
2. 提交失败：核对基础地址、认证、模型字符串、该模型允许的尺寸字段；日志只保留字段名与非敏感参数，不输出 Key/base64。
3. 网络断开且无 ID：任务是否已创建未知，不自动补发 POST；查看上游记录。
4. 已有 ID：只查询原任务，保留 `status`、`progress`、`error`；切勿用 POST 充当查询重试。
5. 生成成功但图片下载失败：重试原 URL 或查询原任务，不重新生成。
6. ComfyUI 未执行：确认该节点连到 Preview/Save 等执行分支；再检查是否正常命中缓存。
7. 多图触发多笔生成：检查全列表接收机制，避免 ComfyUI 默认逐元素调用生成方法。

目前没有真实服务返回记录；快照中的示例不能作为成功联调证据。

## 7. API Key 余额接口（追加范围）

用户补充了 `POST /client/openapi/getAPIKeyCredits`，请求 JSON 为 `{"apiKey":"..."}`；业务成功为 `code: 0`，余额读取 `data.credits`。这是 API Key 的积分余额，不是单次生成计费明细，也不是账户 token 余额接口。

官方“其他 API”页面确认全球/国内 Host 与生成接口所用 Host 相同；余额路径位于主机根目录。完整字段契约来自用户粘贴的文档，来源边界见 [原文记录](references/user-provided-api-key-credits.md)。尚未做带 Key 的真实调用。

用户选择生成结束后在节点内自动刷新。该查询使用独立短超时，失败不影响已生成图片；不能将 HTTP 200、缺失 credits 或业务失败误读为余额 0。
