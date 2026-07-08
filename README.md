> [!WARNING]
> 本项目使用 TRAE IDE 生成与迭代

> [!CAUTION]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

---

# Two Push

Two Push 是一个基于 [OnePush](https://github.com/y1ndan/onepush) 再封装的命令行通知推送程序。

- 使用 INI 管理全局固定配置
- 使用 JSON 文件管理每次推送的标题、内容、代理、重试和推送通道配置
- 适合在脚本、计划任务、CI 或自动化流程中调用

---

## 功能特性

- 失败自动重试
- 模板变量渲染
- 多推送通道支持，推送通道内置在 JSON 文件中，便于独立管理
- 支持多通道并发推送
- 一个 JSON 文件对应一次推送任务
- 支持 JSON 级代理配置与 INI 全局代理配置
- 支持命令行调用指定 JSON 推送文件

---

## 全局配置文件

> [!IMPORTANT]
> INI 只管理全局固定配置，不保存具体推送内容和推送通道密钥。

首次运行会生成默认配置文件 `TwoPush.ini`。

```ini
[Network]
# HTTP/HTTPS 代理地址（例如 http://127.0.0.1:7890）
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
# 更新通道：preview（含预发布）/ stable（仅正式版）
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
        {"provider": "serverchan", "sckey": "SCTxxxx"},
        {"provider": "qmsg", "key": "xxx", "qq": "xxx"},
        {"provider": "dingtalk", "token": "xxx", "secret": "xxx"},
        {"provider": "lark", "webhook": "xxx", "sign": "xxx"},
        {"provider": "smtp", "host": "xxx", "user": "xxx", "password": "xxx", "port": 587, "ssl": true}
    ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` | 是 | 通知标题，支持模板变量 |
| `content` | 是 | 通知内容，支持模板变量 |
| `channels` | 是 | OnePush 推送通道列表 |
| `proxy` | 否 | 当前 JSON 推送任务使用的代理；存在时优先于 INI |
| `retry` | 否 | 当前 JSON 推送任务使用的重试配置；不存在时使用 INI 默认值 |

支持的模板变量：

| 变量 | 说明 |
| --- | --- |
| `{host_name}` | 当前主机名 |
| `{current_time}` | 当前时间，格式为 `YYYY/MM/DD HH:MM:SS` |
| `{short_current_time}` | 当前时间，格式为 `HH:MM:SS` |

---

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| `-p` / `-P` / `-push` / `-Push` | 指定 JSON 推送文件路径 |
| `-c` / `-C` / `-config` / `-Config` | 指定 INI 配置文件路径，默认 `config.ini` |
| `-h` / `-H` / `-help` / `-Help` | 查看帮助信息 |
| `--version` | 查看版本号 |
| `--update` | 手动检查并执行自我更新 |
| `--update-force` | 强制检查并执行自我更新 |

---

## 开发与测试

建议使用虚拟环境安装依赖并运行测试：

```bash
python -m venv .venv
.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
```

---

## License

[WTFPL](./LICENSE)
