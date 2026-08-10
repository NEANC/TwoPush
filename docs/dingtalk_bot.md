# 钉钉自定义机器人 Webhook 推送能力验证

## 概述

本文档记录了使用 [OnePush](https://github.com/y1ndan/onepush) 及钉钉 Webhook API 直接发送消息的验证结果，覆盖消息类型、Markdown 语法支持、@ 功能、图片推送等核心能力。

测试环境：Python 3.12 + OnePush 1.3.0 + 钉钉自定义机器人（Webhook + 加签）

## 消息类型支持

钉钉自定义机器人通过 Webhook 方式支持的消息类型如下：

| 消息类型   | Webhook 支持 | OnePush 支持 | 说明                                                 |
| ---------- | :----------: | :----------: | ---------------------------------------------------- |
| text       |      ✅      |      ✅      | 纯文本消息                                           |
| markdown   |      ✅      |      ✅      | Markdown 格式消息，设置 `markdown=True`              |
| image      |      ❌      |      ❌      | Webhook 方式不支持，需走接口方式或 Markdown 内嵌图片 |
| link       |      ✅      |      ❌      | OnePush 未实现                                       |
| feedCard   |      ✅      |      ❌      | OnePush 未实现                                       |
| actionCard |      ✅      |      ❌      | OnePush 未实现                                       |

> 参考：[钉钉官方文档 - 消息发送与接收类型](https://open.dingtalk.com/document/development/robot-message-type)

## Markdown 语法支持

钉钉 Webhook 的 Markdown 消息支持以下语法（参考 [企业内部机器人实现群聊发送 Markdown 消息](https://open.dingtalk.com/document/isvapp/the-internal-robot-of-the-enterprise-realizes-group-chat-and)）：

| 语法     | 支持 | 示例           |
| -------- | :--: | -------------- |
| 标题     |  ✅  | `#` ~ `######` |
| 引用     |  ✅  | `>`            |
| 加粗     |  ✅  | `**text**`     |
| 斜体     |  ✅  | `*text*`       |
| 链接     |  ✅  | `[text](url)`  |
| 图片     |  ✅  | `![alt](url)`  |
| 无序列表 |  ✅  | `-` / `*`      |
| 有序列表 |  ✅  | `1.` `2.`      |
| 表格     |  ❌  | 不支持         |
| 代码块   |  ❌  | 不支持         |

## 图片推送

### 限制

钉钉自定义机器人 Webhook 方式**不支持** `msgtype: 'image'` 消息类型。这是钉钉 API 的限制，不是 OnePush 的问题。

`image` 消息类型仅在「接口方式」发送机器人消息时可用（需要企业内部应用 + `robotCode` + `accessToken`）。

### 变通方案

在 Markdown 消息中通过 `![图片](URL)` 语法嵌入图片，实测可行。图片 URL 必须能被钉钉客户端公开访问，不能使用本机路径、局域网地址或需要登录授权的地址。

**OnePush 方式：**

```python
from onepush import notify

notify(
    "dingtalk",
    token="完整的钉钉 Webhook URL",
    secret="你的加签密钥",
    title="图片推送",
    content="![示例图片](https://example.com/pic.png)",
    markdown=True,
)
```

**直接请求方式（绕过 OnePush，支持更多参数）：**

以下代码仅展示请求体结构。使用加签安全设置时，`webhook_url` 必须是已附加 `timestamp` 和 `sign` 查询参数的完整 Webhook URL；完整加签实现请参考「请求完整示例」。

```python
import requests

body = {
    "msgtype": "markdown",
    "markdown": {
        "title": "图片推送",
        "text": "![示例图片](https://example.com/pic.png)"
    }
}
requests.post(webhook_url, json=body)
```

## @ 功能

### 结论

text 和 markdown 两种消息类型**都支持** @指定人和 @所有人，但需要在消息体中正确构造 `at` 字段。

### @ 机制

@ 功能必须同时满足两个条件，缺一不可：

1. 消息 content/text 中包含 `@手机号` 或 `@所有人`
2. 请求体中 `at` 字段正确设置 `atMobiles` / `atUserIds` 或 `isAtAll`

### @所有人的正确数据结构

**text 消息：**

```json
{
  "msgtype": "text",
  "text": {
    "content": "【通知】@所有人 请查收。"
  },
  "at": {
    "isAtAll": true
  }
}
```

**markdown 消息：**

```json
{
  "msgtype": "markdown",
  "markdown": {
    "title": "通知标题",
    "text": "## 通知\n\n@所有人 请查收。\n\n> 重要通知"
  },
  "at": {
    "isAtAll": true
  }
}
```

### @指定人：推荐 atMobiles（手机号）方式

对于自定义 Webhook 机器人，**推荐使用 `atMobiles`**，不需要额外 API 调用获取 userId，只需知道用户的钉钉绑定手机号即可。

```json
{
  "msgtype": "markdown",
  "markdown": {
    "title": "提醒",
    "text": "## 通知\n\n@138xxxx1234 请查收。\n\n> 引用内容\n\n- 列表项"
  },
  "at": {
    "atMobiles": ["138xxxx1234"],
    "isAtAll": false
  }
}
```

实测验证：在 Markdown 消息中，以下样式均能成功触发 @ 通知：

- `@138xxxx1234` 开头纯文本
- `## 标题` 后紧接 `@138xxxx1234`
- 标题 + @ + 引用 + 列表 混合

### @指定人：atUserIds 方式（需真实 userId）

`atUserIds` 必须使用企业通讯录 API 返回的真实 userId，昵称或个人钉钉号无效。本文仅验证了请求体结构，尚未使用真实企业 userId 验证最终 @效果。

获取 userId 的方式：

- **简单方式：** 钉钉客户端 → 头像 → 我的信息 → 员工 ID
- **API 方式：** 调用通讯录 API [获取用户详情](https://open.dingtalk.com/document/orgapp/query-user-details)，需要企业内部应用的 AppKey / AppSecret

```json
{
  "msgtype": "text",
  "text": {
    "content": "【提醒】@YOUR_DINGTALK_USER_ID 请查收。"
  },
  "at": {
    "atUserIds": ["YOUR_DINGTALK_USER_ID"],
    "isAtAll": false
  }
}
```

### 注意事项

1. 正常业务不应无条件、短时间重复发送相同 @ 通知；如服务持续离线或其他支持问题需要重复提醒，应由告警升级、去重和重试策略控制
2. @ 用户必须已在群内，否则无法生效
3. 最多 @ 50 个成员
4. 每个机器人每分钟最多发送 20 条消息，超过会限流 10 分钟
5. Markdown 中使用 @ 不会高亮显示（钉钉限制）

## OnePush 的已知限制

### 不支持 at 字段

OnePush 的 `dingtalk._prepare_data` 方法不构造 `at` 字段：

```python
# OnePush 生成的数据结构（缺少 at）
{
    "msgtype": "markdown",
    "markdown": {
        "title": "标题",
        "text": "内容"
    }
}
```

如需 @ 功能，请绕过 OnePush 直接构造 JSON 请求体发送。

### 不支持的消息类型

OnePush 的 DingTalk 提供者仅支持 `text` 和 `markdown`，不支持 `link`、`feedCard`、`actionCard`。

### 不支持 DING

DING 功能需要调用企业内部应用机器人 API：

```
POST https://api.dingtalk.com/v1.0/robot/ding/send
```

需要 `robotCode` + `x-acs-dingtalk-access-token`（企业内部应用凭证），仅限钉钉专业版 / 专属版客户使用。

## Server酱 中转：钉钉通道

### 架构

[Server酱](https://sct.ftqq.com/) 是一个多通道消息中转服务。在 Server酱 后台配置钉钉群机器人通道后，消息流程为：

```
OnePush / API  →  Server酱 API  →  Server酱 服务端  →  钉钉 Webhook
```

### 在 Server酱 后台配置钉钉通道

1. 登录 [Server酱](https://sct.ftqq.com/)，进入「消息通道」页面
2. 添加「钉钉群机器人」通道（channel 值 = 2），填入钉钉 Webhook 地址；Server酱 钉钉通道不支持加签配置
3. 保存后，通过该 SendKey 发送的消息会同时推送至钉钉群

### OnePush 方式

```python
from onepush import notify

notify(
    "serverchan",
    sckey="你的 SendKey",
    title="推送标题",
    content="![图片](https://example.com/pic.png)",
)
```

### Server酱 API 直发方式

以下方式支持 `channel=2` 显式指定钉钉通道：

```python
import requests

requests.post(
    "https://sctapi.ftqq.com/你的SendKey.send",
    data={
        "title": "推送标题",
        "desp": "## 正文\n\n**加粗** | *斜体* | ![图片](url)",
        "channel": "2",
    },
)
```

> **注意：** OnePush 的 ServerChan 提供者使用旧版 API 端点 (`sc.ftqq.com`)，不支持 `channel` 参数。如需指定通道，请直接调用 Server酱 新版 API（`sctapi.ftqq.com`）。

### 实测结论

| 能力 | 直接调用钉钉 | 经 Server酱 中转 | 说明 |
|------|:-----------:|:----------------:|------|
| 文本推送 | ✅ | ✅ | |
| Markdown 推送 | ✅ | ✅ | `desp` 字段支持 Markdown |
| Markdown 图片 | ✅ | ✅ | `![img](url)` 语法 |
| 加签安全设置 | ✅ | ❌ | Server酱 钉钉通道不提供或不传递钉钉加签能力 |
| @所有人 | ✅ | ❌ 实测(3次) | Server酱 API 无 `at` 字段 |
| @指定人(手机号) | ✅ | ❌ 实测(1次) | 同上 |
| @指定人(userId) | 待实测 | ❌ | 同上 |

**总结：** 经本次人工发送确认，Server酱 中转可以传递 Markdown 文本和图片，但不能传递钉钉加签配置或 `at` 字段。OnePush 的 ServerChan 提供者也不接受 `secret` 参数；需要钉钉加签或 @ 功能时，必须直接调用钉钉 Webhook。自动化测试仅验证本地请求数据结构，不替代真实网络投递验证。

## 请求完整示例

### Markdown 全语法 + 图片 + @所有人

```python
import hashlib, hmac, base64, time, urllib.parse, requests

TOKEN = "你的 access_token"
SECRET = "你的加签密钥"

# 加签
ts = str(round(time.time() * 1000))
sign = urllib.parse.quote_plus(base64.b64encode(
    hmac.new(SECRET.encode(), f"{ts}\n{SECRET}".encode(), hashlib.sha256).digest()
))
url = f"https://oapi.dingtalk.com/robot/send?access_token={TOKEN}&timestamp={ts}&sign={sign}"

body = {
    "msgtype": "markdown",
    "markdown": {
        "title": "推送测试",
        "text": (
            "# 一级标题\n\n"
            "## 二级标题\n\n"
            "@所有人 请查收！\n\n"
            "### 文字效果\n\n"
            "**加粗** | *斜体* | **加粗 *嵌套* 斜体**\n\n"
            "### 引用\n\n"
            "> 这是一段引用文字。\n\n"
            "### 链接\n\n"
            "[钉钉开放平台](https://open.dingtalk.com/)\n\n"
            "### 图片\n\n"
            "![图片描述](https://example.com/pic.png)\n\n"
            "### 无序列表\n\n"
            "- 第一项\n"
            "- 第二项\n\n"
            "### 有序列表\n\n"
            "1. 第一步\n"
            "2. 第二步\n"
            "3. 第三步\n\n"
            "###### 测试完成"
        )
    },
    "at": {
        "isAtAll": True
    }
}

r = requests.post(url, json=body, headers={"Content-Type": "application/json"})
print(r.status_code, r.text)  # 200 {"errcode":0,"errmsg":"ok"}
```

### Markdown 图片 + @指定人（atMobiles）

```python
body = {
    "msgtype": "markdown",
    "markdown": {
        "title": "图片推送",
        "text": (
            "## 图片推送通知\n\n"
            "@138xxxx1234 请查看图片。\n\n"
            "![图片](https://example.com/pic.png)\n\n"
            "> 发送时间：2026-08-09"
        )
    },
    "at": {
        "atMobiles": ["138xxxx1234"],
        "isAtAll": False
    }
}
r = requests.post(url, json=body, headers={"Content-Type": "application/json"})
```

## TwoPush 钉钉增强配置

TwoPush 在 `provider: "dingtalk"` 中支持增强参数。当配置包含 `msgtype`、`at`、`at_mobiles`、`atMobiles`、`is_at_all` 或 `isAtAll` 时，TwoPush 会直接调用钉钉 Webhook；未包含这些参数时继续使用 OnePush 原逻辑。

注意：增强键只要存在即会触发直发增强路径（即使值为 `false`、空字符串或空数组），仅 `null` 值不会触发。增强路径未指定 `msgtype`（或指定了无效值）时默认发送 `markdown` 消息，与 OnePush 路径默认 `text`（标题与内容拼接）不同。

### token 写法

`token` 仅支持以下两种输入：

- 裸 access token：`xxx`。TwoPush 会先去除首尾空白，再将非空值拼接为标准 Webhook URL。
- 完整 HTTP(S) Webhook URL：`https://oapi.dingtalk.com/robot/send?access_token=xxx`。

完整 URL 必须同时满足以下条件：

- 协议为 `http` 或 `https`。
- 域名必须为钉钉官方域名 `oapi.dingtalk.com`，不接受内网地址、自定义转发域名或其他域名。
- 路径必须严格为 `/robot/send`，`/robot/send/` 等其他路径均不接受。
- 查询参数中至少有一个 `access_token` 的值去除首尾空白后非空；只有空值或空白值等同于缺少有效 `access_token`。

含 `://` 或 `=`、但不符合上述完整 URL 要求的字符串会被视为疑似 URL，并在发送前报错，不会回退为裸 token。空值、纯空白 token、非法完整 URL 等配置错误都会在发送前统一拦截，跳过该通道且不进入重试。

配置非空 `secret` 时，TwoPush 会根据本次发送时间重新生成 `timestamp` 与 `sign`。如果完整 URL 已包含这两个参数，原值会被移除并由新值覆盖，最终各保留一个。

### 多配置示例

```json
{
  "title": "每日报告",
  "content": "系统运行正常",
  "channels": [
    {
      "provider": "dingtalk",
      "token": "xxx",
      "secret": "SECxxx",
      "msgtype": "markdown",
      "at": ["13800138000"]
    },
    {
      "provider": "dingtalk",
      "token": "https://oapi.dingtalk.com/robot/send?access_token=yyy",
      "secret": "SECyyy",
      "msgtype": "text",
      "at": ["13900139000"]
    }
  ]
}
```

运行：

```powershell
python TwoPush.py -p .\test.json
```

### @ 规则

TwoPush 默认只支持手机号 @。`at` 可写为字符串或数组，程序会归一为 `atMobiles`，并自动在正文中补齐 `@手机号`。

`at` 支持以下写法：

- 字符串或数组：`"at": "13800138000"` 或 `"at": ["13800138000"]`，程序归一为 `atMobiles`
- 字典：`"at": {"atMobiles": ["13800138000"], "isAtAll": true}`
- 顶层键：`at_mobiles` / `atMobiles`（手机号数组）、`is_at_all` / `isAtAll`（@ 全员）

@ 全员（`isAtAll`）的布尔解析规则：布尔值原样生效；字符串 `true`、`1`、`yes`、`on`（大小写不敏感）视为真，其余字符串（含 `false`、`0`、`no`、`off`）视为假。`at` 字典内部的 `isAtAll` 与顶层 `is_at_all`/`isAtAll` 取或生效。

### 日志脱敏

TwoPush 日志会脱敏手机号、`token`（含 `access_token`）、`secret` 与 `sign`，避免敏感信息进入控制台或日志文件。

## 能力总结

| 能力              | 支持 | 方式                                |
| ----------------- | :--: | ----------------------------------- |
| 文本推送          |  ✅  | OnePush `markdown=False` / 直接请求 |
| Markdown 推送     |  ✅  | OnePush `markdown=True` / 直接请求  |
| Markdown 内嵌图片 |  ✅  | `![alt](url)`                       |
| image 消息类型    |  ❌  | Webhook 不支持                      |
| @所有人           |  ✅  | `at.isAtAll=True`（需直接构造请求） |
| @指定人（手机号） |  ✅  | `at.atMobiles`（需直接构造请求）    |
| @指定人（userId） | 待实测 | `at.atUserIds`（需真实企业 userId） |
| 链接消息          |  ✅  | 仅直接请求，OnePush 不支持          |
| FeedCard          |  ✅  | 仅直接请求，OnePush 不支持          |
| ActionCard        |  ✅  | 仅直接请求，OnePush 不支持          |
| DING              |  ❌  | 需企业内部应用 + 专业版             |
| 表格              |  ❌  | Markdown 不支持                     |
| 代码块            |  ❌  | Markdown 不支持                     |

## 参考链接

- [OnePush GitHub](https://github.com/y1ndan/onepush)
- [钉钉自定义机器人发送群消息](https://open.dingtalk.com/document/development/custom-robots-send-group-messages)
- [钉钉消息发送与接收类型](https://open.dingtalk.com/document/development/robot-message-type)
- [企业内部机器人实现群聊发送 Markdown 消息](https://open.dingtalk.com/document/isvapp/the-internal-robot-of-the-enterprise-realizes-group-chat-and)
- [钉钉发送 DING 消息](https://open.dingtalk.com/document/development/robot-sends-nail-message)
