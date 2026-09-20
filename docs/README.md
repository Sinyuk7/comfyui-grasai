# 项目文档

当前阶段：第一版核心节点已实现，完成离线及目标 ComfyUI 集成验证；已完成 Nano Banana 2、GPT Image 2.5 各一次真实生成，余额 API 尚未真实验证。

2026-09-17 追加：批量节点与双输出 Folder Loader 已实现，完成离线、真实宿主 mock 集成与前端验证；批量吞吐和付费服务行为尚未经真实 API 验证。

- [项目设计](design.md)：范围、已确认决策、节点交互、配置契约、异步执行及验收要求。
- [批量图片任务设计](batch-design.md)：双输出 Folder Loader、动态 Reference、1/N 图片配对与 Prompt Variants、Base-major 编号、有界并发、及时保存和验收规则。
- [批量实现验证](batch-verification.md)：实现选择、离线与真实宿主/前端测试结果、复现方式和未验证边界。
- [接口核查与排查笔记](api-notes.md)：三个指定接口的差异、文档冲突与待实测项。
- [API Key 余额接口](references/user-provided-api-key-credits.md)：用户补充的契约、官方域名核查与自动刷新设计依据。
- [运行配置示例](../grsai_config.example.json)：运行时默认配置；`examples/` 内保留原设计样例。
- [资料来源与本地快照](references/README.md)：原始资料、获取时间及完整性校验。

查接口问题时，先看 `api-notes.md`，再对照快照内对应参数；接口变化后新增快照，保留旧版本用于比较。
