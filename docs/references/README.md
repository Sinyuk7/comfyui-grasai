# 资料来源与本地快照

首次核查日期：2026-09-16。原始资料按获取时间存入独立目录，未经改写；项目自己的结论写在上层设计与排查笔记中。

当前设计依据：[2026-09-16T124326Z](snapshots/2026-09-16T124326Z/manifest.json)。该清单记录每份资料的原 URL、最终 URL、UTC 获取时间、Content-Type、字节数和 SHA-256。获取时间不是上游发布日期。

## GRSAI

初始保存用户指定的三个生成相关接口；随后追加用户提供的 API Key 余额接口，不引入视频或其他生成协议。

| 资料 | 原网页 | 本地原文 |
| --- | --- | --- |
| Nano Banana | [Apifox](https://qmy27nhsd9.apifox.cn/452392911e0) | [nano-banana.md](snapshots/2026-09-16T124326Z/grsai/nano-banana.md) |
| GPT Image | [Apifox](https://qmy27nhsd9.apifox.cn/452409160e0) | [gpt-image.md](snapshots/2026-09-16T124326Z/grsai/gpt-image.md) |
| 异步结果 | [Apifox](https://qmy27nhsd9.apifox.cn/452409577e0) | [result.md](snapshots/2026-09-16T124326Z/grsai/result.md) |

来源站的 [llms.txt](https://qmy27nhsd9.apifox.cn/llms.txt) 提供 Markdown 导出地址。原网页的文本抽取会漏掉字段说明，因此保存的是含 OpenAPI YAML 的 Markdown 导出，不能只依据网页中的请求示例实现。

余额追加资料见 [用户提供的接口原文](user-provided-api-key-credits.md)。[官方其他 API 网页快照](snapshots/2026-09-16T131710Z-balance/manifest.json) 用于证明接口路径和 Host；字段定义依据用户原文。此 HTML 快照单独保存，不由现有 Markdown 下载脚本更新。

## ComfyUI

| 资料 | 官方来源 | 本地原文 |
| --- | --- | --- |
| V3、动态选项、异步方法 | [V3 Migration](https://docs.comfy.org/custom-nodes/v3_migration) | [v3-migration.md](snapshots/2026-09-16T124326Z/comfyui/v3-migration.md) |
| 列表与批次 | [Data lists](https://docs.comfy.org/custom-nodes/backend/lists) | [data-lists.md](snapshots/2026-09-16T124326Z/comfyui/data-lists.md) |
| 图片张量与遮罩 | [Images and masks](https://docs.comfy.org/custom-nodes/backend/images_and_masks) | [images-and-masks.md](snapshots/2026-09-16T124326Z/comfyui/images-and-masks.md) |
| 节点属性与缓存 | [Properties](https://docs.comfy.org/custom-nodes/backend/server_overview) | [node-properties.md](snapshots/2026-09-16T124326Z/comfyui/node-properties.md) |
| 前端扩展 | [JavaScript Extensions](https://docs.comfy.org/custom-nodes/js/javascript_overview) | [javascript-extensions.md](snapshots/2026-09-16T124326Z/comfyui/javascript-extensions.md) |

## 更新资料

另存有用户提供的 [ComfyUI-GrsAI 参考代码快照](code/ComfyUI-GrsAI-94a6dda2d27f/manifest.json)，固定本地提交 `94a6dda2d27ffe1ec3224f06e7b87b32d530ab73`，仅包含核查所需源码、说明、项目元数据和 MIT LICENSE。参考代码使用旧版 API；结论见 [参考项目核查](../reference-project-review.md)。该快照不由下述公共文档下载脚本更新。

在项目根目录运行：

```sh
python3 scripts/snapshot_docs.py
```

脚本只需 Python 标准库，读取公共文档，不请求 API Key，不调用生成接口。每次创建新目录，不覆盖历史快照；全部下载成功后才开始写文件。更新后比较原文，再明确修改设计所引用的快照。

原文版权及许可归各来源所有；项目根目录的 LICENSE 不改变这些第三方资料的许可。此处保留的是研发查阅快照，不应据此宣称拥有上游文档版权。
