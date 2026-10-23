[English](README.md) | [简体中文](docs/README.zh-CN.md) | [繁體中文](docs/README.zh-TW.md) | [日本語](docs/README.ja.md) | [한국어](docs/README.ko.md) | [Deutsch](docs/README.de.md) | [Français](docs/README.fr.md) | [Español](docs/README.es.md)

<!-- readme:b00 -->

# Ollama Status Service

<!-- readme:b01 -->

**Know when your Ollama machine can accept work — and give local users the next turn.**

<!-- readme:b02 -->

A Windows gateway, background service and system tray application for an existing Ollama installation. It tracks requests as they execute, exposes machine-readable status and help, and schedules local and remote work through one shared queue.

<!-- readme:b03 -->

**Current build: 1.6.1 · Windows x64 · Offline installer**

<!-- readme:b04 -->

Version 1.6.1 fixes mixed languages in live version summaries, saved update history and installer completion messages. It includes eight UI languages with automatic matching and saved selection, automatic proxy discovery and a built-in loopback download relay for TUN/Fake-IP networks, fixes stale Ollama upgrade offers and removes command-window flashes from tray actions. See [download networking][link1]. It also includes official Ollama update checks and upgrades to the system tray. It checks stable releases every six hours, verifies the installer hash and publisher signature, refuses to interrupt active or uncertain work, and restores OSS ownership after validating the original models and configuration. See [Ollama updates][link2]. The downloadable Windows package is linked below.

<!-- readme:b05 -->

Version 1.4.0 embeds a multi-server web manager at `/manage`: add/import nodes, download/delete models, and pause/resume durable server-owned pulls. It uses the existing reverse proxy authentication; no separate account, port or manager process is added. See [the manager guide][link3].

<!-- readme:b06 -->

Version 1.2.5 fixes empty model lists caused by missing LocalService permissions. Installation and upgrade repair model-tree permissions, verify access using the actual service account, and check backend/gateway model inventories before reporting success. The default C-drive user-profile model directory is supported. The concurrent `pull`/`push` fix from 1.2.4 is retained.

<!-- readme:b07 -->

[Release 1.6.1][link4] · [Windows ZIP][link5] · [SHA256][link6] · [Release notes][link7] · [Build from source][link8]

<!-- readme:b08 -->

> Download `OllamaStatusService-Windows-x64-v1.6.1.zip` and its checksum from the release above, or build it from source. GitHub's automatic “Source code” archives are not installable packages. The installer, tray, /manage and /dashboard support Simplified Chinese, Traditional Chinese, English, Japanese, Korean, German, French and Spanish. Automatic language matching and saved manual selection are available; /status and /help localize display text while preserving protocol keys. See [languages][link9].

<!-- readme:b09 -->

Model `pull`/`push` transfers bypass the inference queue and its timeouts, can run concurrently and continue streaming progress. `/status` counts them separately: they make maintenance state `busy` without consuming an inference slot. Use `can_start_now` for inference readiness.

<!-- readme:b10 -->

## Why a gateway?

<!-- readme:b11 -->

An installed or loaded model is not necessarily busy. `/api/tags` lists models and `/api/ps` lists loaded models; neither is a request scheduler. This service observes the requests it forwards, including streaming responses, disconnects and uncertain completion.

<!-- readme:b12 -->

All managed requests must pass through the gateway. Direct access to the underlying Ollama port is outside its accounting. This project is independent of Ollama and does not bundle Ollama, models, GPU libraries or drivers.

<!-- readme:b13 -->

## Highlights

<!-- readme:b14 -->

- **Reliable state:** `idle`, `busy`, `offline`, `unknown` and `stopping`; active and queued request counts, origin and IDs.
- **Local priority without preemption:** an active request finishes first; waiting local requests precede waiting remote requests, with FIFO within each class. No reservation for future batches.
- **Dedicated proxy ingress:** FRP, SSH tunnels and reverse proxies use `11436`, always classified as `remote`. Client-supplied headers cannot promote a request to `local`.
- **Self-describing API:** `/help`, `/.well-known/ollama-status-service` and `/openapi.json` describe capabilities, queue limits and retry rules.
- **Useful tray feedback:** colored symbols, queue counts, dashboard, logs and service controls. Closing the tray does not stop the background services.
- **Practical installer:** model-path discovery with provenance, package integrity checks, upgrade preflight and a single progress window.
- **Conservative recovery:** uncertain execution is persisted in SQLite and blocks new work until recovery. Full prompts and generated responses are not stored in the request database.

<!-- readme:b15 -->

| Tray | Meaning |
| --- | --- |
| ![Idle][link10] | Idle |
| ![Busy][link11] | Busy / requests queued |
| ![Offline][link12] | Backend unavailable |
| ![Unknown][link13] | Completion uncertain; recovery required |

<!-- readme:b16 -->

## Architecture

<!-- readme:b17 -->

```mermaid
flowchart LR
    L[Local clients] --> D[11434: socket-based origin]
    N[Direct network clients] --> D
    R[Remote clients] --> P[FRP / reverse proxy]
    P --> I[127.0.0.1:11436: always remote]
    D --> Q[One shared scheduler and request store]
    D --> T[Parallel pull/push transfers]
    I --> Q
    I --> T
    T --> O
    Q --> O[Existing Ollama on 127.0.0.1:11435]
```

<!-- readme:b18 -->

| Port | Default role | Origin |
| --- | --- | --- |
| `11434` | IPv4/IPv6 direct gateway | Real socket peer; use loopback for local clients |
| `11436` | IPv4 loopback proxy ingress | Always `remote` |
| `11435` | IPv4 loopback Ollama backend | Internal only; do not bypass the gateway |

<!-- readme:b19 -->

The two gateway listeners share one process, lifecycle, execution limit and queue. This is request-level scheduling, not distributed GPU memory. Multi-machine selection remains the client's responsibility; a standard-library example is in [`examples/route_request.py`][link14].

<!-- readme:b20 -->

## Install or upgrade

<!-- readme:b21 -->

Requirements: Windows 10/11 x64 or Windows Server with a desktop, an existing Ollama installation and local models, Windows PowerShell 5.1, .NET Framework 4.8, and administrator access for installation. Task Scheduler and Windows Firewall management must be available.

<!-- readme:b22 -->

1. Download the release ZIP and its `.sha256.txt`. Verify the ZIP with `Get-FileHash -Algorithm SHA256` and compare it with the supplied checksum.
2. Pause new submissions and let running and queued work finish. Extract the **whole** archive to a new folder, outside the installed program directory.
3. Run **`Install.exe`**. Check the detected Ollama executable and model directory, then approve the Windows UAC prompt. For an existing installation, the same executable offers the upgrade flow.
4. Open the tray icon, or visit `http://127.0.0.1:11434/dashboard`. The runtime, dependencies and service wrapper are bundled; installation does not download them.
5. For FRP or another same-machine proxy, migrate its local destination to `127.0.0.1:11436`, then verify origin before resuming clients.

<!-- readme:b23 -->

The installer reuses your existing Ollama and models, changes the backend to `11435`, and installs gateway/backend services. Installation, upgrades, recovery and stopping services can interrupt work: **non-preemption applies to normal scheduling, not maintenance**. Upgrade checks reject busy/queued/unknown state and proxy-port conflicts before uninstalling. If installation fails after uninstalling the old version, the installer reports the error and retains backups; it does not promise automatic rollback.

<!-- readme:b24 -->

Run **`Uninstall.exe`** to remove this service and restore backed-up integration settings. The original Ollama installation and models are retained. Detailed Chinese instructions: [deployment guide][link15], [AI integration guide][link16].

<!-- readme:b25 -->

Model permissions are granted only to **LocalService (`S-1-5-19`)** with **Modify**, including protected child directories/files. Original rules are recorded before changes and restored on uninstall or failed installation; unrelated accounts, ownership and inheritance protection are retained. A temporary task tests real service-account access and is removed after the check. Explicit denies/encryption can still block access and produce a path-specific error. Model-tree junctions/reparse points are rejected; select the actual directory instead.

<!-- readme:b26 -->

## FRP and reverse-proxy setup

<!-- readme:b27 -->

For the existing Ollama proxy entry on **each server**, change only the local destination:

<!-- readme:b28 -->

```toml
localIP = "127.0.0.1"
localPort = 11436
```

<!-- readme:b29 -->

For INI configurations, use `local_ip` and `local_port`. Preserve the existing proxy name, type, public port/domain, authentication and TLS configuration. External client URLs normally stay the same. Examples are configuration fragments, not replacement files: [`frpc-ollama.toml`][link17], [`frpc-ollama.ini`][link18].

<!-- readme:b30 -->

Nginx, Caddy and SSH tunnels on the same machine should also target `11436`. An off-machine proxy requires an explicit reachable bind address and appropriately restricted firewall access. The ingress accepts ordinary HTTP, **not PROXY protocol**.

<!-- readme:b31 -->

```powershell
curl.exe --noproxy "*" http://127.0.0.1:11434/help
curl.exe --noproxy "*" http://127.0.0.1:11436/help
```

<!-- readme:b32 -->

Check `version=1.6.1` and `request_origin=local` / `remote` respectively, then check `/help` through the existing public proxy URL. During inference, inspect `/status.active_origin` and `requests[].origin`. If the proxy still targets `11434`, loopback forwarding can still be mistaken for local work.

<!-- readme:b33 -->

Acceptance sequence: run remote A, queue remote B, then queue local C with a positive wait timeout. A must finish uninterrupted; C runs before B. Both queued requests must remain within their wait deadline and queue capacity.

<!-- readme:b34 -->

## API and queue policy

<!-- readme:b35 -->

| Endpoint | Purpose |
| --- | --- |
| `GET /status` | Current state, readiness, active/queued counts and request metadata |
| `GET /help` | Live limits, capabilities, origin and client guidance |
| `GET /healthz` | Gateway liveness and this probe's origin; not proof of backend readiness |
| `GET /dashboard` | Browser status panel |
| `GET /manage` | Embedded multi-server and model manager; existing reverse proxy authentication |
| `GET /api/tags`, `GET /api/ps` | Proxied Ollama model inventory / loaded models |

<!-- readme:b36 -->

Use `X-Ollama-Queue-Timeout` per inference request: `0` declines immediately when busy, a positive number waits up to that many seconds, and omission uses the server default (15 seconds). The default maximum is 3600 seconds; read `/help` for the running instance's limits. Waiting deadlines do not cut off inference once it has started. Scheduled execution concurrency is 1 (excluding pull/push), with up to 64 queued requests and 64 MiB of queued JSON data.

<!-- readme:b37 -->

```powershell
$body = @{ model='qwen3:8b'; prompt='Hello'; stream=$false } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' `
  -Method Post -ContentType 'application/json; charset=utf-8' `
  -Headers @{'X-Ollama-Queue-Timeout'='30'} `
  -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 3700
```

<!-- readme:b38 -->

Replace the model with one installed locally. A local request only gains priority **after actually joining the queue**. Zero-wait requests can be refused while busy; a continuous local workload can delay remote work until its deadline expires.

<!-- readme:b39 -->

`can_start_now` is a snapshot, not a reservation. Safe automatic failover requires **HTTP 429/503 + `X-Ollama-Forwarded: false` + JSON `forwarded: false`**. Timeouts, disconnects or partial output do not prove that execution never began; do not silently replay uncertain requests on another machine. The service does not provide cross-server exactly-once execution.

<!-- readme:b40 -->

## Network and recovery boundaries

<!-- readme:b41 -->

**The gateway adds no authentication.** Since 1.2.2, installation/upgrade sets this product's TCP `11434` allow rule to any source, any local address and any network profile, restricted to the bundled Python program. Earlier custom source restrictions are backed up but not automatically retained. Review the rule after upgrading and enforce appropriate authentication, HTTPS and network controls. `11435` and `11436` default to loopback and are not automatically opened externally.

<!-- readme:b42 -->

Persistent configuration and history live under `%ProgramData%\OllamaStatusService`; application files live under `%ProgramFiles%\OllamaStatusService`. See [`config.example.json`][link19]. No remote configuration-write API is provided.

<!-- readme:b43 -->

If a request's completion cannot be verified, `unknown` blocks further admissions. Use the tray's recovery action only when terminating unfinished work is acceptable. Recovery stops and checks the backend before clearing uncertain records. Do not delete the database while Ollama may still be running. Queued payloads are in memory and are not replayed after restart.

<!-- readme:b44 -->

## Development and validation

<!-- readme:b45 -->

See [build instructions][link8] for test commands. Version 1.6.1 passed 18 native localization checks, 11 web localization checks, 18 isolated OSS upgrade scenarios, 21 tray/release checks, 12 Ollama update coordinator scenarios with additional recovery checks, four real installer GUI checks and five compiled hidden-worker checks. The preceding 1.6.0 integration run passed 118 Python tests, including 15 download-network checks and seven upgrade-admission checks. Package hashes, archive integrity, ASCII filenames and source/package consistency are verified before publishing. Version 1.2.5 additionally verified real NTFS ACL repair and LocalService access in a C-drive user-profile fixture. The earlier reports retain the two concurrent Ollama CLI checks from 1.2.4 and the 1.2.3 installer UI checks. Tests do not prove deployment or GPU/FRP behavior on every production server. See [validation report][link20].

<!-- readme:b46 -->

All eight README editions contain the same sections, tables, examples and operational boundaries. They are generated from shared translation blocks, and packaging rejects missing or stale editions. See [README translation maintenance][link21].

<!-- readme:b47 -->

Public examples use placeholders. Production configurations, logs and the original client/development Word and Markdown documents are intentionally excluded.

<!-- readme:b48 -->

## License

<!-- readme:b49 -->

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

<!-- readme:b50 -->

Third-party components retain their own licenses; see [`licenses/THIRD-PARTY-NOTICES.txt`][link22]. The release carries the Python and vendored dependency license files.

[link1]: docs/DOWNLOAD-NETWORK.md
[link2]: docs/OLLAMA-UPDATES.md
[link3]: docs/MANAGE.md
[link4]: https://github.com/ozil56/OllamaStatusService/releases/tag/v1.6.1
[link5]: https://github.com/ozil56/OllamaStatusService/releases/download/v1.6.1/OllamaStatusService-Windows-x64-v1.6.1.zip
[link6]: https://github.com/ozil56/OllamaStatusService/releases/download/v1.6.1/OllamaStatusService-Windows-x64-v1.6.1.zip.sha256.txt
[link7]: CHANGELOG.md
[link8]: docs/BUILD.md
[link9]: docs/LANGUAGES.md
[link10]: docs/images/idle.png
[link11]: docs/images/busy.png
[link12]: docs/images/offline.png
[link13]: docs/images/unknown.png
[link14]: examples/route_request.py
[link15]: Deployment-Guide.txt
[link16]: Integration-Guide.txt
[link17]: examples/frpc-ollama.toml
[link18]: examples/frpc-ollama.ini
[link19]: config.example.json
[link20]: Verification-Report.txt
[link21]: docs/README-TRANSLATIONS.md
[link22]: licenses/THIRD-PARTY-NOTICES.txt
