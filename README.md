> [!WARNING]
> 本项目使用 TRAE IDE 生成与迭代
> LOGO 使用 Seedream 4.5 模型生成

> [!CAUTION]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

---

<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="./IMG/logo.png" width="256" height="256" />
</p>

<div align="center">

# TwoPush

</div>

TwoPush 是基于 [OnePush](https://github.com/y1ndan/onepush) 再封装的命令行通知推送程序，适合在脚本、计划任务、自动化流程中调用。

## 快速开始

### 使用已发布的 Windows 程序

1. 将 `TwoPush.exe` 放到一个独立目录。
2. 在该目录运行一次程序，自动生成 `config.ini` 和 `TwoPush.templates.json`。
3. 编辑生成的 JSON 文件，填写推送标题、内容和通道密钥。
4. 执行推送：

```powershell
.\TwoPush.exe -p .\TwoPush.templates.json
```

首次运行如果检测到配置文件不存在，程序只生成示例文件并退出；请完成配置后再次运行。

### 使用 Python 源码运行

项目需要 Python 3.12 或更高版本。建议在 Windows PowerShell 中使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python TwoPush.py -p .\report.json
```

如果 PowerShell 阻止脚本执行，可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe TwoPush.py -p .\report.json
```

---

## 功能特性

- 失败自动重试
- 模板变量渲染
- 多推送通道支持，推送通道内置在 JSON 文件中，便于独立管理，并且支持并发推送
- 一个 JSON 文件对应一次推送任务
- JSON 级代理配置与 INI 全局代理配置
- 命令行调用指定 JSON 推送文件
- 钉钉内置增强推送路径，支持 `text`、`markdown` 和提醒配置

---

## 全局配置文件

> [!IMPORTANT]
> INI 只管理全局固定配置，不保存具体推送内容和推送通道密钥。

首次运行且默认 `config.ini` 不存在时，程序会生成 `config.ini` 和 `TwoPush.templates.json`，然后退出。请修改配置和 JSON 模板后重新运行。

```ini
[Network]
# 代理服务器地址（例如：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080），留空表示不使用代理
proxy =
# 是否对推送启用代理（仅当 JSON 模板未显式设置 proxy 时生效）
enable_proxy_for_push = false

[Push]
# 默认重试间隔（支持 1h / 15m / 30s）
retry_interval = 3s
# 默认最大重试次数
retry_max_count = 3

[Update]
# 是否启用自动更新检查
auto_check = true
# 更新通道: preview 包括预发布版本 (Alpha/Beta/RC) 或 stable 仅正式发布版本
channel = stable

[Logs]
# 是否保存日志到文件
save_enabled = true
# 最大日志文件保留数量
max_files = 15
```

---

## JSON 推送文件

每个 JSON 文件表示一次推送任务。

示例 `report.json`：

```json
{
  "title": "每日报告 - {host_name}",
  "content": "截止 {current_time}，系统运行正常",
  "proxy": "http://127.0.0.1:7890",
  "retry": {
    "interval": "5s",
    "max_count": 2
  },
  "channels": [
    { "provider": "serverchan", "sckey": "SCTxxxx" },
    { "provider": "qmsg", "key": "xxx", "qq": "xxx" },
    { "provider": "dingtalk", "token": "xxx", "secret": "xxx" },
    { "provider": "lark", "webhook": "xxx", "sign": "xxx" },
    {
      "provider": "smtp",
      "host": "xxx",
      "user": "xxx",
      "password": "xxx",
      "port": 587,
      "ssl": true
    }
  ]
}
```

可以通过命令行参数生成示例 JSON：

```powershell
# 在当前目录生成默认模板
.\TwoPush.exe --template

# 在指定路径生成模板
.\TwoPush.exe -T C:\custom.json

# 允许覆盖已有文件
.\TwoPush.exe --template-force C:\custom.json
```

### JSON 字段说明

| 字段       | 必填 | 说明                                                      |
| ---------- | ---- | --------------------------------------------------------- |
| `title`    | 是   | 通知标题，支持模板变量                                    |
| `content`  | 是   | 通知内容，支持模板变量                                    |
| `channels` | 是   | OnePush 推送通道列表                                      |
| `proxy`    | 否   | 当前 JSON 推送任务使用的代理；存在时优先于 INI            |
| `retry`    | 否   | 当前 JSON 推送任务使用的重试配置；不存在时使用 INI 默认值 |

### 支持的模板变量

| 变量                   | 说明                                   |
| ---------------------- | -------------------------------------- |
| `{host_name}`          | 当前主机名                             |
| `{current_time}`       | 当前时间，格式为 `YYYY/MM/DD HH:MM:SS` |
| `{short_current_time}` | 当前时间，格式为 `HH:MM:SS`            |

### JSON 配置规则与示例

配置规则：

- `config.ini` 保存全局配置，例如默认重试、日志和代理设置；不要在其中保存具体通道密钥。
- JSON 文件保存一次任务的标题、内容、推送通道和可选的任务级代理、重试配置。
- JSON 中显式设置 `proxy` 时优先使用 JSON 代理；未设置时，仅当 `enable_proxy_for_push = true` 才使用 INI 代理。
- `retry.interval` 支持秒、分钟和小时，例如 `30s`、`5m`、`1h`；重试次数至少为 1，间隔最大为 3600 秒。
- 多个通道会并发发送；推送完成后程序根据整体结果返回退出码。

### 钉钉增强推送

钉钉仅填写 `provider`、`token`、`secret` 时使用 OnePush 路径。需要 `markdown`、`atMobiles` 或 `isAtAll` 等增强字段时，使用 TwoPush 内置 Webhook 路径：

```json
{
  "provider": "dingtalk",
  "token": "你的 access_token",
  "secret": "你的加签密钥",
  "msgtype": "markdown",
  "atMobiles": ["13800138000"],
  "isAtAll": false
}
```

内置路径支持 `text` 和 `markdown`，并会根据提醒配置补齐正文中的手机号或 `@所有人`。Webhook、消息类型和提醒规则详见 [钉钉机器人说明](./docs/dingtalk_bot.md)。

> 安全提示：钉钉 `token`、`secret`、SMTP 密码、代理认证信息等均属于敏感信息。建议限制配置文件访问权限，并使用 `.gitignore` 排除本地配置文件。

---

> [!WARNING]
> 传递 `-p` / `--push` 参数时，指定的 JSON 文件不存在时，程序会直接报错退出，不会自动生成模板。

## 命令行参数

| 短参数 | 长参数                    | 说明                                                          |
| ------ | ------------------------- | ------------------------------------------------------------- |
| `-p`   | `--push --Push`           | 指定 JSON 推送文件路径                                        |
| `-c`   | `--config` / `--Config`   | 指定 INI 配置文件路径，默认 `config.ini`                      |
| `-T`   | `--template [path]`       | 生成 JSON 模板文件，未指定路径时生成 `TwoPush.templates.json` |
|        | `--template-force [path]` | 生成 JSON 模板文件并允许覆盖已有文件                          |
| `-h`   | `--help`                  | 查看帮助信息                                                  |
| `-v`   | `--version`               | 查看版本号                                                    |
|        | `--update`                | 手动检查并执行自我更新                                        |
|        | `--update-force`          | 强制检查并执行自我更新                                        |
| `-S`   | `--silent`                | 静默模式，不输出控制台日志                                    |

---

### 拖放使用

将 `.json` 推送文件直接拖动到 `TwoPush.exe` 上即可自动执行推送。推送完成后窗口会显示结果并等待按键退出。

如果同时使用 `-p` 参数，以 `-p` 指定的文件为准，且不会停顿等待按键。

### 退出码

| 退出码 | 含义                         |
| ------ | ---------------------------- |
| `0`    | 所有推送通道均发送成功       |
| `1`    | 至少一个推送通道发送失败     |
| `2`    | 输入、模板或配置存在错误     |

### 日志与故障排查

默认情况下，日志保存在当前目录的 `logs` 文件夹中。推送失败时，优先检查：

1. JSON 文件是否为有效 JSON，且 `title`、`content`、`channels` 字段类型正确。
2. 通道名称和密钥是否填写正确，钉钉机器人是否已配置对应的 IP 白名单。
3. 代理地址是否可访问；JSON 中的 `proxy` 会覆盖 INI 中的代理配置。
4. `logs` 目录中的最新日志，日志会自动隐藏常见密钥、密码和手机号。

### 常见问题

**为什么第一次运行没有发送通知？**

默认配置文件不存在时，程序会先生成 `config.ini` 和 `TwoPush.templates.json`，并在完成初始化后退出。填写通道配置后再次执行即可。

**为什么钉钉的 @ 没有生效？**

使用钉钉内置增强路径时，只需在通道配置中设置 `atMobiles` 或 `isAtAll`；如果正文缺少对应的 `@手机号` 或 `@所有人`，程序会自动补齐。OnePush 路径不处理这些增强字段，需要使用提醒功能时请改用内置增强路径。详细规则见 [钉钉机器人说明](./docs/dingtalk_bot.md)。

**推送失败会重试几次？**

默认使用 `config.ini` 中的 `retry_interval` 和 `retry_max_count`。也可以在 JSON 的 `retry` 字段中为单次任务覆盖默认值。

---

## 开发与测试

建议使用虚拟环境安装依赖并运行测试：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
```

如果 PowerShell 不允许激活脚本，可直接执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

---

## License

[WTFPL](./LICENSE)
