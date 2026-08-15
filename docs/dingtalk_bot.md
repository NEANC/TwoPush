# 钉钉自定义机器人 Webhook 推送能力验证

## 概述

本文档记录了使用 [OnePush](https://github.com/y1ndan/onepush) 及钉钉 Webhook API 直接发送消息的验证结果，覆盖消息类型、Markdown 语法支持、@ 功能、图片推送等核心能力。

原始人工能力验证环境：钉钉自定义机器人（Webhook + 加签）；Python 与 OnePush 的具体版本未留存可核实证据。

本文档核对环境（2026-08-11）：Python 3.12.7，OnePush 1.9.0。

```powershell
.\.venv\Scripts\python.exe -c "import sys, importlib.metadata as m; print(sys.version.split()[0], m.version('onepush'))"
```

`requirements.txt` 中的 `onepush>=1.2.0` 是最低版本约束，未锁定实际版本。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_onepush_dingtalk_image.py tests\test_notification.py
```

上述测试核对了裸 token URL 构造、HTTPS 完整 URL 复用、HTTP 完整 URL 拒绝、secret 加签，以及仅含 token 时选择 `dingtalk(onepush)`、含 `msgtype` 等增强参数时选择 `dingtalk(builtin)` 并在汇总与失败日志中使用对应路由标识。
OnePush 1.9.0 自身对完整 HTTP URL 的处理事实仍可由 `onepush.providers.dingtalk.DingTalk._prepare_url` 实现确认，但 TwoPush 的统一发送前校验会直接拒绝该输入，不会交给 OnePush 发送。

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
    token="你的 access_token",
    secret="你的加签密钥",
    title="图片推送",
    content="![示例图片](https://example.com/pic.png)",
    markdown=True,
)
```

**请求体片段（需配合已生成的 Webhook URL）：**

以下代码展示请求体结构，并使用未加签 Webhook URL 占位。使用加签安全设置时，`webhook_url` 必须替换为已附加 `timestamp` 和 `sign` 查询参数的完整 Webhook URL；完整加签实现请参考「请求完整示例」。

```python
import json
import requests


def check_dingtalk_response(response):
    """检查钉钉 Webhook 的业务响应。"""
    MAX_RESPONSE_BYTES = 1 * 1024 * 1024

    try:
        response.raise_for_status()
    except requests.exceptions.RequestException:
        raise RuntimeError("钉钉 Webhook HTTP 请求失败") from None

    content_encoding = response.headers.get("Content-Encoding", "")
    if content_encoding.strip().lower() not in ("", "identity"):
        raise RuntimeError("钉钉 Webhook 返回了不支持的压缩响应")

    content_length = response.headers.get("Content-Length")
    if (
        isinstance(content_length, str)
        and content_length.isdigit()
        and int(content_length) > MAX_RESPONSE_BYTES
    ):
        raise RuntimeError("钉钉 Webhook 响应体过大")

    response.raw.decode_content = False
    try:
        body = response.raw.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        raise RuntimeError("钉钉 Webhook 响应读取失败") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("钉钉 Webhook 响应体过大")

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("钉钉 Webhook 返回了非 JSON 响应") from None

    if not isinstance(data, dict) or "errcode" not in data:
        raise RuntimeError("钉钉 Webhook 响应格式异常")

    errcode = data["errcode"]
    success = (
        type(errcode) is int and errcode == 0
    ) or (
        isinstance(errcode, str) and errcode.strip() == "0"
    )
    if success:
        return

    raise RuntimeError("钉钉 Webhook 请求失败")


webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
body = {
    "msgtype": "markdown",
    "markdown": {
        "title": "图片推送",
        "text": "![示例图片](https://example.com/pic.png)"
    }
}
with requests.post(
    webhook_url,
    json=body,
    headers={"Accept-Encoding": "identity"},
    timeout=10,
    stream=True,
) as response:
    check_dingtalk_response(response)
```

`Accept-Encoding: identity` 配合 `raw.read`，限制的是进入 UTF-8/JSON 解析的响应实体字节数；不承诺限制 HTTP 传输开销、chunk 扩展或总下载成本。`timeout=10` 是连接和相邻读取活动超时，并非请求总时限；持续缓慢传输可能耗时更久。

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

- **管理后台方式：** 钉钉管理后台 → 通讯录 → 成员管理 → 点击成员查看「员工 UserID」。客户端「我的信息」中展示的「员工 ID」不保证与 Webhook `atUserIds` 所需的企业 userId 一致，请勿混用
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


def check_serverchan_response(response):
    """检查 Server酱 的业务响应。"""
    try:
        response.raise_for_status()
    except requests.exceptions.RequestException:
        raise RuntimeError("Server酱 HTTP 请求失败") from None

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Server酱 返回了非 JSON 响应") from None

    if not isinstance(data, dict) or "code" not in data:
        raise RuntimeError("Server酱 响应格式异常")

    code = data["code"]
    if type(code) is int and code == 0:
        return

    raise RuntimeError("Server酱 请求失败")


response = requests.post(
    "https://sctapi.ftqq.com/你的SendKey.send",
    data={
        "title": "推送标题",
        "desp": "## 正文\n\n**加粗** | *斜体* | ![图片](url)",
        "channel": "2",
    },
    timeout=10,
)
check_serverchan_response(response)
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
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import requests


def check_dingtalk_response(response):
    """检查钉钉 Webhook 的业务响应。"""
    MAX_RESPONSE_BYTES = 1 * 1024 * 1024

    try:
        response.raise_for_status()
    except requests.exceptions.RequestException:
        raise RuntimeError("钉钉 Webhook HTTP 请求失败") from None

    content_encoding = response.headers.get("Content-Encoding", "")
    if content_encoding.strip().lower() not in ("", "identity"):
        raise RuntimeError("钉钉 Webhook 返回了不支持的压缩响应")

    content_length = response.headers.get("Content-Length")
    if (
        isinstance(content_length, str)
        and content_length.isdigit()
        and int(content_length) > MAX_RESPONSE_BYTES
    ):
        raise RuntimeError("钉钉 Webhook 响应体过大")

    response.raw.decode_content = False
    try:
        body = response.raw.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        raise RuntimeError("钉钉 Webhook 响应读取失败") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("钉钉 Webhook 响应体过大")

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("钉钉 Webhook 返回了非 JSON 响应") from None

    if not isinstance(data, dict) or "errcode" not in data:
        raise RuntimeError("钉钉 Webhook 响应格式异常")

    errcode = data["errcode"]
    success = (
        type(errcode) is int and errcode == 0
    ) or (
        isinstance(errcode, str) and errcode.strip() == "0"
    )
    if success:
        return

    raise RuntimeError("钉钉 Webhook 请求失败")


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

with requests.post(
    url,
    json=body,
    headers={
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    },
    timeout=10,
    stream=True,
) as response:
    check_dingtalk_response(response)
    print(response.status_code)
```

`Accept-Encoding: identity` 配合 `raw.read`，限制的是进入 UTF-8/JSON 解析的响应实体字节数；不承诺限制 HTTP 传输开销、chunk 扩展或总下载成本。`timeout=10` 是连接和相邻读取活动超时，并非请求总时限；持续缓慢传输可能耗时更久。

### Markdown 图片 + @指定人（基于上一完整示例的追加请求体片段）

以下代码复用上一完整示例已导入的 `requests`、已生成的 `url` 和 `check_dingtalk_response`，仅替换请求体并发送。

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
with requests.post(
    url,
    json=body,
    headers={
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    },
    timeout=10,
    stream=True,
) as response:
    check_dingtalk_response(response)
```

## TwoPush 钉钉增强配置

TwoPush 在 `provider: "dingtalk"` 中支持增强参数。当配置包含 `msgtype`、`at`、`at_mobiles`、`atMobiles`、`is_at_all` 或 `isAtAll` 时，TwoPush 会直接调用钉钉 Webhook；未包含这些参数时继续使用 OnePush 原逻辑。

注意：增强键只要存在即会触发直发增强路径（即使值为 `false`、空字符串或空数组），仅 `null` 值不会触发。增强路径未指定 `msgtype`（或指定了无效值）时默认发送 `markdown` 消息，与 OnePush 路径默认 `text`（标题与内容拼接）不同。

### token 写法

`token` 的处理取决于通道实际使用的路由，不能将两条路径的行为混用。

**包含增强键：`dingtalk(builtin)`**

配置中存在任一增强键时，由 TwoPush 内置实现发送。此路径支持：

- 裸 access token：`xxx`。
- 完整 HTTPS Webhook URL：`https://oapi.dingtalk.com/robot/send?access_token=xxx`。

TwoPush 仅去除 `token` 首尾普通空格 U+0020，不会使用无参数 `strip()` 清理其他空白。原始值中不得包含 Unicode `Cc`（包括 ASCII C0、C1 与 DEL）、`Cf` 或 `Cs`（代理字符）类别字符，首尾也不得包含 NBSP、全角空格、行分隔符等非 U+0020 空白；裸 token 遵循相同限制，长度上限为 4096 个字符，合法值仍会进行 URL 编码。最终 URL 必须可编码为 ASCII，且 `urlencode`、`urlunsplit` 规范输出后的 ASCII 长度不得超过 8192 个字符；不满足时作为配置错误拒绝。

完整 URL 长度上限为 8192 个字符，必须明确以大小写不敏感的 `https://` 开头，authority 仅接受官方域名 `oapi.dingtalk.com`，不得包含 userinfo；端口只能省略或使用原始十进制字符串 `443`，空端口、`00443`、`+443` 及其他端口均拒绝。路径必须严格为 `/robot/send`，只要存在 `#` fragment 分隔符即拒绝，包括空 fragment。query 最多包含 100 个用户提供 query 参数，所有百分号转义必须为合法 `%HH`，并以严格 UTF-8 解析；非法或截断的 UTF-8 字节序列会作为配置错误拒绝。解析后会再次检查每个键和值，拒绝其中的 Unicode `Cc`、`Cf` 或 `Cs` 字符，再重新编码；该过程仅按一次 URL 解析语义处理，双编码 `%2500` 解码一层所得的字面 `%00` 可以接受。输出会将 scheme 与官方 hostname 规范为小写并移除显式默认端口，同时保留 query 的重复键、空值和原始顺序，并要求至少一个解码后去除首尾空白仍非空的 `access_token`。HTTP URL、`/robot/send/`、其他域名、缺少有效 `access_token`，以及含 `://` 或 `=` 但不是合法完整 URL 的值均不接受。

配置非空 `secret` 时，TwoPush 会在每次发送尝试中按当次时间重新生成 `timestamp` 与 `sign`；完整 URL 中已有的同名参数会先被移除，再由新值覆盖，最终各保留一个。用户最多可提供 100 个 query 参数；内置加签替换或追加 `timestamp` 与 `sign` 后，最终最多包含 102 个参数，但最终 URL 仍受 8192 字节 ASCII 长度限制。

**未包含增强键：`dingtalk(onepush)`**

未配置增强键时，TwoPush 仍调用已安装的 OnePush 钉钉 provider，但不会把原始钉钉凭据对象直接交给 OnePush。TwoPush 会创建发送参数副本，以统一构造并校验后的最终 HTTPS Webhook URL 替换副本中的 `token`，同时移除已被消费的 `secret`；原始通道参数对象保持不变。

**当前 OnePush 1.9.0 的实现事实：** 小写 `https://` 开头的完整 URL 会原样复用；未提供 `secret` 时不会追加签名。因此 OnePush transport 使用的 URL 与 TwoPush 构造并校验的最终 URL 完全相同。如果绕过 TwoPush 直接调用 OnePush，裸 access token 会拼接到 OnePush 的基础 URL，完整 HTTP URL 会被当作 token 再套入基础 URL；配置非空 `secret` 时，OnePush 会直接追加新的 `timestamp` 与 `sign`，不会去重或覆盖原有同名参数。

**推荐用法：** 此路径始终使用裸 access token：

```json
{
  "provider": "dingtalk",
  "token": "xxx",
  "secret": "SECxxx"
}
```

TwoPush 依赖 OnePush 对小写 HTTPS 完整 URL 的复用行为。本文现有测试与实现核对限定为 OnePush 1.9.0；`requirements.txt` 允许的旧版本 `>=1.2.0` 是否保持该行为未获保证。完整 HTTP URL 会被 TwoPush 直接拒绝。`secret` 由 TwoPush 消费并完成唯一一次加签，最终 URL 中已有的 `timestamp`、`sign` 会被替换且各保留一个，OnePush 不会收到 `secret`，因而不会二次签名。

**禁止自动重定向：** `dingtalk(onepush)` 路由同样禁止自动跟随重定向。TwoPush 会在发送前于 OnePush 钉钉实例上覆盖底层 `request`，在转发时强制将 `allow_redirects` 置为 `False`，即使上游未来显式传入 `allow_redirects=True` 也不会重新开启。该保证依赖 OnePush 支持实例级覆盖 `request`：`Provider.request` 为 `@staticmethod`，经实例访问得到不绑定 `self` 的底层函数，且 `Provider` 未声明 `__slots__`，故实例可安全覆盖；该结构在 OnePush 1.2.0~1.9.0 保持一致。跨版本的真正差异是 `Provider.request` 的签名：`proxies` 位置参数在 1.6.0 才加入，1.2.0~1.5.0 为 `def request(method, url, **kwargs)`，1.6.0 起为 `def request(method, url, proxies, **kwargs)`（1.6.0 的 `proxies` 为必填位置参数，1.7.0 起 `proxies` 才带默认值 `None`）。TwoPush 的转发包装以 `*args` 透传位置参数，因此同时兼容 1.2.0~1.9.0 的两种 request 签名。若实例缺少可调用的 `request`，TwoPush 会按配置错误拒绝发送（fail-closed），而不是回退到可能自动跟随重定向的不安全发送。

**统一发送前校验**

无论最终走哪条路径，TwoPush 都会先确认 `token` 非空，并在重试循环外构造一次不带 `secret` 的规范基础 URL，完成静态配置校验。因此，原始值以及完整 URL query 解码后的键和值中，Unicode `Cc`、`Cf` 或 `Cs` 类别字符都会被拒绝，首尾非普通空格的空白也会被拒绝；裸 token 与完整 URL 分别受 4096 和 8192 个字符的输入长度限制，最终 ASCII URL 另受 8192 字节长度限制。完整 URL 仅接受明确的 HTTPS 前缀，并会在发送前检查官方域名、禁止 userinfo、严格校验默认端口原始语法、拒绝 fragment 分隔符、检查严格路径、合法 `%HH` 转义、严格 UTF-8、最多 100 个用户提供 query 参数和解码后有效的 `access_token`。query 参数数量在解析前按非空 query 的 `&` 数量加一确定，空 query 为零；超限错误不依赖解析器异常文本，其他 `ValueError` 统一报告为 query 解析错误，`UnicodeDecodeError` 单独报告为非法 UTF-8。

配置非空 `secret` 时，TwoPush 还会在循环外按当前时间戳位数且至少 13 位的 `timestamp`，以及 32 字节 HMAC-SHA256 摘要对应的合法最坏规范 Base64 结构（42 个 `/`、`8` 与结尾 `=`）做确定性的 URL 编码容量预算；预算前会移除基础 URL 中旧的 `timestamp` 与 `sign`，再追加最坏情况值。最坏最终 URL 超过 8192 字节时按静态配置错误拒绝，不发送也不休眠，且容量判断不调用随机动态签名。

进入重试循环后，带 `secret` 的通道会为每次尝试从同一规范基础 URL 重新签名并只构造一次当次最终 URL；`dingtalk(builtin)` 会在 HTTP 请求边界再次复用同一 URL 校验链，但不会重新签名，并明确禁止自动跟随重定向，避免请求离开已校验的官方 Webhook 目标；`dingtalk(onepush)` 将同一对象作为参数副本中的 `token` 交给 OnePush 并移除 `secret`。下一次重试使用新的 `timestamp` 与 `sign`。未配置 `secret` 时直接复用循环外的规范基础 URL，无需逐次构造。

空值、纯空白 token 或未通过上述预校验的疑似/完整 URL 属于配置错误：TwoPush 会跳过该通道且不重试。

### 多配置示例

以下通道按 `channels` 数组顺序路由：第一个仅含 OnePush 标准参数，走 `dingtalk(onepush)`；第二个包含增强键，走 `dingtalk(builtin)`，并由内置实现处理完整 HTTPS Webhook URL。

```json
{
  "title": "每日报告",
  "content": "系统运行正常",
  "channels": [
    {
      "provider": "dingtalk",
      "token": "xxx",
      "secret": "SECxxx"
    },
    {
      "provider": "dingtalk",
      "token": "https://oapi.dingtalk.com/robot/send?access_token=yyy",
      "secret": "SECyyy",
      "msgtype": "markdown",
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

下表中“钉钉 Webhook 支持”指“直接调用钉钉 Webhook（手工请求）”；“TwoPush 内置支持”指 `dingtalk(builtin)` 路由。

| 能力 | 钉钉 Webhook 支持 | TwoPush 内置支持 | 方式或说明 |
| --- | :---: | :---: | --- |
| 文本推送 | ✅ | ✅ | 内置：`msgtype=text`；OnePush 可用 |
| Markdown 推送 | ✅ | ✅ | 内置：默认或 `msgtype=markdown`；OnePush 可用 |
| Markdown 内嵌图片 | ✅ | ✅ | 正文用 `![alt](url)`；OnePush 可用 |
| image 消息类型 | ❌ | ❌ | 自定义机器人 Webhook 不支持 |
| @所有人 | ✅ | ✅ | 内置：`isAtAll`；OnePush 不支持 |
| @指定人（手机号） | ✅ | ✅ | 内置：`atMobiles`；OnePush 不支持 |
| @指定人（userId） | ✅ | ❌ | 手工请求，见表后说明 |
| 链接消息 | ✅ | ❌ | 手工请求；OnePush 不支持 |
| `feedCard` | ✅ | ❌ | 手工请求；OnePush 不支持 |
| `actionCard` | ✅ | ❌ | 手工请求；OnePush 不支持 |
| DING | ❌ | ❌ | 需企业内部应用及钉钉专业版或专属版 |
| 表格 | ❌ | ❌ | 钉钉 Markdown 不支持 |
| 代码块 | ❌ | ❌ | 钉钉 Markdown 不支持 |

钉钉官方支持通过 `atUserIds` @指定人，但本文未使用真实企业 userId 验证最终 @效果。
`dingtalk(builtin)` 与 OnePush 均不支持 `atUserIds`，需手工构造请求。

## 参考链接

- [OnePush GitHub](https://github.com/y1ndan/onepush)
- [钉钉自定义机器人发送群消息](https://open.dingtalk.com/document/development/custom-robots-send-group-messages)
- [钉钉消息发送与接收类型](https://open.dingtalk.com/document/development/robot-message-type)
- [企业内部机器人实现群聊发送 Markdown 消息](https://open.dingtalk.com/document/isvapp/the-internal-robot-of-the-enterprise-realizes-group-chat-and)
- [钉钉发送 DING 消息](https://open.dingtalk.com/document/development/robot-sends-nail-message)
