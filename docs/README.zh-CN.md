[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**让共享 Ollama 的忙闲可见，让本机请求优先获得下一次执行机会。**

当前版本 **1.2.4**。1.2.4 让 `pull/push` 并发透传、不占推理队列、不受推理时限影响，并保留进度输出。活动传输单独显示在 `/status` 和看板中，令 `state=busy` 以提示维护等待；是否能开始推理请看 `can_start_now`。取消、续传由 Ollama 处理，`create/copy/delete` 等其他写接口仍使用原调度规则。

软件适用于 Windows x64。这是已有 Ollama 前面的 HTTP 网关，配有后台服务、图形安装器和托盘。默认 README 为英文；本页为简体中文说明。软件界面目前为简体中文，多语言 README 不代表软件界面已本地化。

[下载 1.2.4](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.2.4) · [更新记录](../CHANGELOG.md) · [源码构建](BUILD.md)

## 它解决什么问题

`/api/tags` 列出已有模型，`/api/ps` 列出已加载模型，都不能直接代表推理忙闲。本项目跟踪经过网关的请求生命周期，处理执行、等待、流式响应、断线与完成状态不确定的情况。绕过网关直接访问后端的任务无法被统计。

- `/status` 提供闲忙、执行和排队数量、来源及请求 ID；`/dashboard` 提供状态面板。
- 本机等待请求优先，同类请求按 FIFO；当前任务正常完成，不被插队中断。不会为尚未到来的批次保留位置。
- `/help`、服务发现入口和 `/openapi.json` 提供能力、限额、来源识别及安全换机规则。
- 托盘用绿色勾表示空闲、橙色钟表示忙碌、红色叉表示离线、紫色问号表示需要恢复。退出托盘不会停止服务。
- 安装器发现已有模型目录并显示路径来源，校验包完整性，升级前检查状态和端口冲突。
- SQLite 保存任务元数据及不确定状态，不保存完整提示词和生成结果。

## 端口与调度

| 端口 | 默认用途 | 来源分类 |
| --- | --- | --- |
| `11434` | IPv4/IPv6 直连网关 | 根据真实 Socket；本机建议使用 `127.0.0.1` |
| `11436` | 同机代理回源，仅监听 IPv4 回环 | 固定 `remote` |
| `11435` | 原有 Ollama 后端，仅监听 IPv4 回环 | 内部使用，不应绕过网关 |

两个网关入口共用一个进程、执行限制、队列和任务库。客户端请求头不能把代理请求提升为本机。本项目不是多机显存拼接，也不代替客户端选择服务器；基础选服示例见 [`examples/route_request.py`](../examples/route_request.py)。

## 安装与升级

需要 Windows 10/11 x64 或带桌面的 Windows Server、已有 Ollama 和本地模型、Windows PowerShell 5.1、.NET Framework 4.8、任务计划与防火墙管理组件，安装时需管理员权限。

1. 在 Release 下载 `OllamaStatusService-Windows-x64-v1.2.4.zip` 和 `.sha256.txt`，用 `Get-FileHash -Algorithm SHA256` 核对。GitHub 自动生成的 Source code 压缩包不能直接安装。
2. 暂停新请求，等执行与排队任务结束，把完整 ZIP 解压到新的文件夹，不要覆盖安装目录。
3. 双击 **`安装.exe`**，核对 Ollama 和模型路径，允许 UAC。已有版本也使用同一个安装器升级。
4. 打开托盘或 `http://127.0.0.1:11434/dashboard`。安装包自带运行时、依赖和服务包装器，安装不联网下载。
5. 每台机器的 FRP 等代理回源改为 `127.0.0.1:11436`，验证后再恢复客户端提交。

安装会改变 Ollama 的启动方式及后端端口，使用已有程序、模型和 GPU 库，不重新分发这些组件。正常调度不抢占，但安装、升级、停止或恢复服务可能中断任务，必须安排维护时段。忙碌、排队、unknown 或代理端口冲突会阻止升级；旧版已卸载后若新版安装失败，会明确报错并保留备份，不承诺自动回滚。

卸载运行 **`卸载.exe`**，保留原有 Ollama 和模型。详见[部署说明](../部署说明.txt)和 [AI 接入说明](../AI接入说明.txt)。

## FRP 必须改哪两项

每台服务器已有 Ollama 代理条目中修改：

```toml
localIP = "127.0.0.1"
localPort = 11436
```

INI 对应 `local_ip`、`local_port`。保持原名称、类型、公网端口、域名、认证和 TLS；外部客户端地址通常不变。同机 Nginx、Caddy、SSH 隧道也应转到 `11436`。异机代理需要明确配置可达监听与限制来源的防火墙。这里接收普通 HTTP，不解析 PROXY protocol。

分别访问 `http://127.0.0.1:11434/help`、`http://127.0.0.1:11436/help` 及原代理 URL 的 `/help`：版本应为 `1.2.4`，来源分别是 `local`、`remote`、`remote`。真实推理来源看 `/status.active_origin`。只升级而不迁移代理回源，旧的误分类问题仍会存在。

验收：运行远端 A → 先排远端 B → 再排本机 C；A 完整结束后应先执行 C，再执行 B。B/C 要使用正数排队期限且不超时。

## 排队、安全换机与恢复

请求头 `X-Ollama-Queue-Timeout: 0` 表示遇忙立即拒绝；正数表示最多等待的秒数；省略默认 15 秒。默认上限 3600 秒，实际以 `/help` 为准。它只限制开始前的排队，不会因排队期限在执行中到期而截断推理。调度器并发 1（不包括 pull/push）、最多排队 64 个、等待 JSON 总量 64 MiB。

本机优先要求请求实际入队；零等待在忙时也会被拒绝。连续本机任务可能让远端等待到超时。`can_start_now` 是快照，不是预留凭证。

只有 **HTTP 429/503、响应头 `X-Ollama-Forwarded: false`、JSON `forwarded: false` 同时成立**，才能据此安全换机。网络超时、断线和半截输出不能证明未执行，服务没有跨服务器恰好执行一次保证。

不能确认任务结束时进入 `unknown`，阻止新任务。托盘恢复会先停止并确认后端终止，再清理不确定记录；不要在后端仍运行时手动删除数据库。内存队列不会跨重启重放。

## 网络边界与验证

**网关没有新增身份认证。** 1.2.2 起，安装/升级会把本产品的 TCP `11434` 允许规则设为所有来源、所有本地地址、所有网络配置文件，限定随包 Python 程序；旧自定义范围仅备份，不自动沿用。升级后请核对规则，保留必要的认证、HTTPS 与网络访问控制。`11435`、`11436` 默认回环，不自动对外开放。

配置和历史位于 `%ProgramData%\OllamaStatusService`，程序位于 `%ProgramFiles%\OllamaStatusService`。没有远程写配置接口。

1.2.4 已通过随包运行时的 68 项 Python 测试、10 种隔离升级场景、安装包与配置检查，以及两个真实 Ollama CLI 进程连接模拟后端的并发验证。历史 1.2.3 还记录了 14 项 C# 路径发现检查和 4 种安装器界面场景。模拟后端测试不代表各生产机器已完成 GPU、公网链路或部署验收。公开包不包含原客户端/开发过程的 Word 与 Markdown 文档、生产配置或日志。

## 许可证

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

第三方组件继续遵循各自许可证，见 [第三方说明](../licenses/THIRD-PARTY-NOTICES.txt)。
