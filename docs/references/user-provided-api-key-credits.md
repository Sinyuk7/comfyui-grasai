# 获取 API Key 积分余额接口

来源：用户于 2026-09-16 在本次需求讨论中粘贴的接口文档。以下整理保留原始请求、响应示例与字段含义，不代表已完成真实调用验证。

## 用户提供的接口契约

方法：`POST`。

路径：`/client/openapi/getAPIKeyCredits`。

请求参数（JSON）：

```json
{
  "apiKey": "sk-xxxxxx"
}
```

响应方式：JSON。

```json
{
  "code": 0,
  "data": {
    "credits": 10000
  },
  "msg": "success"
}
```

| 字段 | 类型 | 含义 | 示例 |
| --- | --- | --- | --- |
| `code` | number | 接口状态码 | `0` |
| `msg` | string | 接口信息 | `success` |
| `data` | object | 接口数据 | `{}` |
| `data.credits` | number | 当前积分余额 | `10000` |

原文未提供积分到货币换算、每次任务费用、余额扣减时点或额外认证头要求。

## 补充核查（非用户原文）

2026-09-16 读取 [GRSAI 官方其他 API 页面](https://grsai.ai/zh/dashboard/documents/other)，确认页面列出此接口，并在 Host 输入框列出以下两个地址：

- 海外：`https://grsaiapi.com`
- 国内直连：`https://grsai.dakka.com.cn`

页面将此组标为“旧版 API 文档”；余额接口按其独立契约使用，不将该标签套用为本项目生成接口的协议版本。

完整公开 HTML 已保存至 [来源快照](snapshots/2026-09-16T131710Z-balance/manifest.json)，包含获取时间与 SHA-256。页面的静态内容没有展开完整请求响应定义，字段契约依据上述用户提供的原文。

本次只读取公共文档，未携带 API Key 请求余额接口。
