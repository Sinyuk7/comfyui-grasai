# Batch Image Generate

按参考图组和提示词组合生成并保存图片。

- N 组参考图和 M 条 Prompts 会创建 N x M 个任务。
- 输出保存在 ComfyUI `output/image_api` 下的新批次目录。
- Manifest 记录任务映射、输出、远端 ID 和错误。
