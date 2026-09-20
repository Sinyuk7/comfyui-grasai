# 批量节点实现验证

日期：2026-09-17。仅离线、localhost mock 与隔离 ComfyUI 测试，没有读取真实 Key 或调用 GRSAI 生成/余额接口。

## 环境

- ComfyUI Core：0.36.0，沿用本机隔离 checkout `/tmp/grsai-comfyui-v0.36.0`。
- ComfyUI Frontend：`comfyui-frontend-package 1.52.7`。
- comfy_api：Core 随附 `comfy_api.latest`，没有假设独立发行版本。
- Python 3.12.13，PyTorch 2.14.0，CPU；Chrome + Playwright headless。
- 插件在隔离宿主的 custom_nodes 中通过软链接加载，没有修改已有用户 ComfyUI 安装。

## 实现选择

- 新增 `GRSAILoadImagesFromFolder`、`GRSAIBatchImageGenerate`，不修改原节点 ID 或多图含义。
- Folder `references` 使用 `GRSAI_REFERENCES` 类型，轻量来源记录和 tensor 引用；`images` 输出同一图片数据的标准 list。目录每次重新加载，不依赖路径缓存。
- Reference 为 V3 MultiType + Autogrow TemplateNames，全列表执行一次。采用 TemplateNames 保证 reference_1 从 1 开始；本地数量保护默认 10，可配置 1–100。
- 目标前端断开中间 Autogrow 连接会压缩后续输入，因此插件仅对本 Batch 节点拦截这种断开处理，保留原编号空洞；后端再次检查连续性。尾部空口可保留，不开发额外加减 Dashboard。
- Prompt Variants 按 Base-major 展开；一个普通 STRING 或一个宿主包装的 STRING 数组均可规范化，未连接/空数组回退。未保证任意第三方自定义数组类型可连接。
- 全批共享两个无默认认证的 aiohttp session（API/CDN），每个客户端保存独立 task 状态；API Bearer 仅用于 API 请求。
- Task 生命周期占并发槽，默认 4、范围 2–10。查询/提交的可靠 Retry-After 可延迟后续提交，不重新 POST。明确 HTTP 401/403 停止后续提交；未编造 GRSAI 未公开的业务错误码映射。
- 远端 ID 回调先持久化再轮询。图片结果回调逐张保存，单次节点原有全部下载成功才输出规则不变。
- Manifest 由单一 BatchStore 持有，asyncio.Lock 覆盖状态更新和同步原子写入，无后台 writer、数据库或恢复系统。
- 全失败输出保留 manifest 路径，使用宿主 ExecutionBlocker 跳过图片分支；否则空 list 会导致目标宿主 Preview 映射报错。部分成功返回已保存的图片，不把失败项填成黑图。
- 内部 PNG 无工作流元数据；Key 在普通工作流输入中的既有风险仍然存在，下游 Save Image 仍可能嵌入它。manifest 额外清理来源信息中的 Key，提示词仅存指纹。

## 验证覆盖

- Python 全套回归：145 项通过，包含原单次节点回归、localhost aiohttp 模拟接口和实际 ComfyUI V3/PromptExecutor 集成（首轮 138 项，本次补充 7 项）。
- JavaScript 状态测试：4 项通过，包含计划 T=N×M 与运行进度分母。
- 原节点前端 smoke 与新 Batch 前端 smoke：连接类型、自然增长、断开中间连线不移位、模型切换、序列化重载、输出插口、API workflow 转换、状态不序列化；未入队生成。
- Ruff 与文档/差异检查通过。测试数量对应本轮最终验证，历史单次测试数见原验证记录。

重点断言：

1. 1/N 广播、长度不匹配、空序列、插口空洞、数字序号大于 9、异尺寸 list/batch 展开与来源对齐。
2. 自然排序、隐藏/非图片文件排除、不递归、损坏文件不跳过、重新读取变更文件、引用 tensor 不复制。
3. 非空 Prompts 覆盖、空数组回退、换行不拆分、重复 Variant 不去重、非法数组报错。
4. 10×4=40、10×10=100；Task 014=Base 004+Prompt 002；并发约束最终任务，不嵌套扩大。
5. Mock HTTP 乱序完成，输出和文件仍按任务编号；CDN 不接收 Key；ID 在第一次查询前已存在于 manifest。
6. 多结果中后续下载失败，之前文件保留；单任务失败不重排后续编号；全失败不生成 placeholder。
7. 丢失 POST 响应不重发、429 冷却不重发、HTTP 401/403 停止未提交项、取消保留已知 ID。
8. Manifest 并发更新不丢字段、独立批次目录不覆盖、存储失败停止后续任务并保留先前文件、保存的 PNG 不含 metadata。
9. 单图/同 Base 编码复用、结束释放编码缓存、UI 通知失败不影响保存、Key/明文 Prompt 不进入报告。
10. 真实宿主验证 MultiType 连线和 STRING list，整批只执行一次；连续两次 Queue 创建新批次与新请求；输入相同的节点缓存键不同；Preview/Save 消费有效 list，全失败图片分支跳过而报告保留。

## 补充验收测试

本次仅增加测试和文档，不修改生产代码。新增 7 项 pytest 用例，并扩展现有浏览器 smoke。以下证据只覆盖所列场景，不表示设计文档全部验收完成。

| 场景 | 新增证据 |
| --- | --- |
| 10 组 × 4 Prompts，最终并发 4 | `test_forty_tasks_hold_four_slots_until_saved` 分别阻塞下载、保存阶段，确认四项未保存前不提交第五项；仅释放任务 003 后立即补位，全部四十项的文件、图片顺序和 Base/Prompt/Task 映射均正确 |
| 慢 manifest 写入 | `test_slow_manifest_replaces_preserve_monotonic_task_state` 在真实同步 `os.replace` 边界注入延迟，核对 121 次快照，写入时持有 store 锁，旧 ID、输出及终态不会倒退；不将同步 I/O 测试称为异步磁盘性能测试 |
| 读取过程中删除文件 | `test_file_deleted_after_enumeration_fails_without_skipping` 在枚举完成后删除第二张，明确失败且不将第三张补到第二张位置 |
| 宿主缓存与目录变化 | `test_folder_content_changes_invalidate_real_executor_cache` 复用真实 PromptExecutor，连续修改图片、添加图片、删除图片；验证加载的像素尺寸、指纹和 Base 数量随之更新 |
| 提交冷却中的取消 | `test_cancel_during_submission_cooldown_closes_workers_and_sessions` 在明确 429/Retry-After 后取消，确认待提交项不发 POST、无遗留 Batch 协程且两个共享 session 已关闭 |
| 内部与下游 PNG 的 Key 边界 | `test_test_key_persistence_boundary_in_actual_saved_pngs` 用非空假 Key 驱动真实执行器及 SaveImage：内部 PNG、manifest、事件和输出报告无 Key；下游嵌入的 API workflow/界面 workflow 保留原凭据字段，不新增插件副本 |
| Prompts 连线重载与浏览器凭据路径 | `browser_batch_smoke.py` 连接原生 PrimitiveStringMultiline 到 Prompts，保存重载并转换为 API workflow，断开后默认 Prompt 保持不变；用非空假 Key 审计 `widgets_values`、`widgets_values_named`、`inputs.api_key`，新增属性不含 Key；脚本拦截 Queue POST，绝不执行生成 |

浏览器本轮验证的是原生 STRING 到 Prompts 的连接与重载，不替代任意第三方 Prompt Array 类型的兼容性测试。宿主对 STRING list/数组包装的验证仍由既有 Python 集成测试覆盖。

Key 测试明确证明现有风险：工作流凭据输入及下游 SaveImage 的嵌入元数据仍含 Key，不能因此宣称安全密钥存储。不得使用真实生产凭据运行这类测试。

尚待补齐的验收仍包括 Batch Plan 对 K/Prompt 来源的显式界面展示、部分更广泛的异常与上下游组合，以及需要授权的 Provider 真实行为；未勾选项不应仅凭总测试数自动视为完成。

## 复现

```sh
.venv/bin/python -m pytest -q
COMFYUI_PATH=/tmp/grsai-comfyui-v0.36.0 .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
node --test tests/test_web.mjs

# 另启隔离 ComfyUI 后运行；两个脚本均不入队生成。
.venv/bin/python tests/browser_smoke.py --url http://127.0.0.1:8197
.venv/bin/python tests/browser_batch_smoke.py --url http://127.0.0.1:8197
```

不设置 `COMFYUI_PATH` 时真实宿主测试明确跳过。pytest 的网络模拟只使用 localhost；测试文件夹和输出由 tmp_path 隔离。

## 尚未验证

- GRSAI 的批量实际吞吐、官方参考图数量/大小上限、账号业务错误码、真实 429/额度语义和任务保存期限；默认并发 4 不代表性能承诺。
- 新节点真实付费生成与余额接口；历史两次单次生成不能代替批量联调。
- 大图片/大批次内存压力、进程崩溃与断电级持久性；图片输出和输入快照仍驻留内存，落盘不等于释放 tensor。
- Subgraph、其他 Core/Frontend 版本、Vue 节点渲染、第三方 Prompt Array 自定义类型、其他图片后处理节点与多人公网部署。
- 不提供自动恢复、重新生成失败项或全局 rate limiter；重新 Queue 是新批次，可能再次计费。
