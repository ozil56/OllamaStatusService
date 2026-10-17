# OSS 1.4.0：远端后台模型任务

1.4.0 起可直接通过 `/manage` 操作，沿用现有反代认证；详见 [网页管理](MANAGE.md)。

此版本让模型下载、删除由模型所在服务器的 OSS 服务执行。本地网页、管理器和管理电脑只负责提交与查询；远端确认接收后，本地退出、断网、关机都不会取消任务。模型所在的服务器需要保持运行和联网。

## 升级各台服务器

1. 等待现有旧版 `pull`、推理和排队任务结束。旧版长连接不会自动转成新任务。
2. 将完整的 `OllamaStatusService-Windows-x64-v1.4.0.zip` 复制到对应服务器，解压到新的目录。
3. 双击 **安装.exe**，选择升级并批准本机管理员提示。已有 Ollama、模型目录和配置由原升级流程保留。
4. 通过该服务器现有地址读取 `/help`，确认 `version` 为 `1.4.0`，且 `model_job_policy.execution` 为 `server_owned`。
5. 在 `/manage` 网页中刷新服务器。此时新下载会使用远端任务接口。

参与管理的 OSS 节点应分别升级。认证、域名和代理配置沿用现有部署；同机代理仍回源 `127.0.0.1:11436`。完整安装包见 GitHub Releases 的 v1.4.0。

## HTTP API

`POST /oss/model-jobs`，请求体示例：

```json
{"kind":"pull","model":"qwen3:8b","request_key":"unique-client-operation-0001"}
```

删除请求使用 `kind: "delete"`，还必须携带与 `model` 一致的 `confirm_model`。删除仍进入原来的本机优先、同来源 FIFO 队列，不越过正在执行的推理。

成功受理返回 HTTP 202 和任务对象，其中 `id` 是远端任务编号。`GET /oss/model-jobs/{id}` 查询单个任务，`GET /oss/model-jobs` 返回所有活动、暂停任务和最近 200 个终态任务。

`request_key` 必须是 16–128 位字母、数字、下划线或连字符。网络中断导致提交结果不确定时，仅能用**原编号和原操作参数**重试；服务器会返回同一任务。相同编号配不同参数返回 409。并发活动任务上限为 16，同一模型不能同时提交冲突任务。

进度包含 `state`、`phase`、`completed`、`total`、`percent`、`speed`、`error`、`created`、`updated`。`state` 为 queued、running、paused、success、failed 或 unknown。百分比只基于 Ollama 已报告的文件层；必须收到完成标记才记为成功。

任务记录保存在远端 `%ProgramData%\OllamaStatusService\data\model-jobs.sqlite3`。OSS 服务重启后，暂停任务仍保持暂停；其余未完成下载会重新连接 Ollama，使用 Ollama 自身的断点数据继续；未确认的删除记为 unknown，不自动重放。下载失败不会无限重试。

`/api/pull` 和 `/api/push` 仍是旧的兼容流接口。只有 `/oss/model-jobs` 提交的任务具备上述客户端独立性。旧 CLI 的行为不被静默改变。

## 暂停、继续和重试（1.3.1）

管理器的任务中心在受支持的下载旁显示“暂停”；暂停后显示“继续”，失败后显示“重试”。可用“已暂停”筛选。暂停操作不删除任何模型或分片。

- `POST /oss/model-jobs/{id}/pause`：持久化暂停意图、断开该任务的 Ollama 下载连接，返回 `state: paused`。可重复调用。
- `POST /oss/model-jobs/{id}/resume`：从 paused 或 failed 重新提交同一模型的 pull，沿用任务编号。Ollama 利用它保留的分片继续，管理器先保留上次进度，再以 Ollama 新报告的字节数为准。对正在执行的任务重复调用不新建连接。
- `/status` 的 `model_jobs.controls`、`/help` 的 `model_job_policy.controls` 和任务列表的 `controls` 均返回 `["pause", "resume"]`。旧版无此能力，网页显示升级提示。

暂停不等于删除，重连也不保证提速。若其他客户端同时请求同一模型，Ollama 可能继续为其他客户端下载。此接口只控制本 OSS 托管的任务；从其他 CMD 或旧 `/api/pull` 发起的下载不能被它接管。

Ollama 的分片恢复及连接取消行为以服务器所装版本为准，参考其 [下载实现](https://github.com/ollama/ollama/blob/main/server/download.go)。不要清理 Ollama 模型目录或临时分片。

## 验证

`runtime\python.exe tests\owned_model_jobs.py` 使用独立模拟 Ollama 验证：提交端断开后继续、实时进度、幂等去重、网关重启恢复、删除队列及错误处理。不会下载或删除真实服务器上的模型。

实际部署验收：提交一个用户需要的模型，确认拿到远端任务编号后关闭管理器；稍后重新打开，核对同一编号的进度和完成结果。不要把未收到 202 的网络超时当作已经受理。
