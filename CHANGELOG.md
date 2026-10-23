# Changelog

## 1.6.1 — 2026-10-23

- Publish complete README editions in all eight languages, including Traditional Chinese. Preserve every section, table, architecture diagram and code example, and reject incomplete or stale documentation during packaging.

- Fix mixed languages in the Ollama updater: version summaries and timestamped history are rendered from retained message data when the UI language changes.
- Translate older saved update records, including the restored OSS management message, without changing version numbers, timestamps or backup paths.
- Keep the original source message in updater and setup progress files so completion history can switch languages after the operation finishes.
- Add regressions for live language changes, asynchronous update checks, completed installations and all eight supported languages.

## 1.6.0 — 2026-10-23

- Localize installation, uninstall, tray menus, confirmations, update progress, /manage, /dashboard, /status and /help in eight languages: Simplified/Traditional Chinese, English, Japanese, Korean, German, French and Spanish.
- Match the Windows UI language or browser preference by default; save explicit choices per desktop user or browser and propagate the chosen language to elevated workers. Ship all translations and short localized guides offline.
- Preserve API keys, states, model names, user paths and original diagnostic details. Changing display language does not restart services or cancel tasks.
- Include the 1.5.1 download-network and tray fixes below, with English executable/package filenames.

## 1.5.1 — 2026-10-23

- Discover existing Windows/user proxies and forward Ollama HTTPS model transfers through a loopback CONNECT relay, with a limited Fake-IP fallback for known model domains. Preserve certificate validation and existing download connections.
- Include active relay connections and in-progress uploads in upgrade safety checks; gate admission when upgrading OSS 1.5.0 or newer. Preserve network settings across OSS and Ollama upgrades.
- Refresh tray upgrade offers from the installed version after checks and completed upgrades, so the current release is not offered again.
- Launch tray administration and Ollama update scripts through a GUI worker with no console. Replace the command-file tray launcher with Open-Tray.exe.

## 1.5.0 — 2026-10-23

- Standardize distributed filenames: `Install.exe`, `Uninstall.exe`, `Open-Tray.cmd`, `Deployment-Guide.txt/html`, `Integration-Guide.txt` and `Verification-Report.txt`. Update all translated instructions and use an English Windows shortcut name while cleaning up legacy localized shortcuts.
- Add tray checks for official stable Ollama releases, six-hour polling, per-version notifications and an upgrade progress window.
- Verify the official installer URL, size, SHA256 and Ollama Inc. signature before stopping anything.
- Close admission before the final idle check; refuse busy, queued, transferring, uploading or uncertain work without interrupting it.
- Preserve the original models, configuration and service environment; coordinate service shutdown, official installation, desktop-process cleanup, startup restoration and backend/gateway/model verification.
- Retain recovery records and backups after failures or crashes; keep admission closed when recovery cannot be verified. Never upgrade Ollama merely because a new release was detected.

## 1.4.0 — 2026-10-17

- Embed the multi-server manager at `/manage` with a persistent local node, peer CRUD/import, model inventory, downloads/deletions and durable pull pause/resume.
- Use the existing Nginx/reverse proxy authentication for page, assets and API; no new account, password, listener or process. Mutations require a per-process CSRF token.
- Execute every job on its target OSS. Closing a browser or the managing node does not cancel work on another server. Paused jobs and registries survive restart/upgrade.
- Encrypt stored peer credentials using the service account's Windows DPAPI. Provide a separate private migration export for old manager registries; never bundle credentials in the public release.


## 1.3.1 — 2026-10-17 (local build)

- Add persistent pause/resume controls for server-owned pull jobs. Pausing closes the task's Ollama stream and retains partial download data; resuming reuses the same task ID and reissues pull.
- Keep paused jobs paused across service restarts. Repeated controls do not create duplicate streams; completed jobs and deletes reject pause/resume.
- Advertise controls in status/help/list/OpenAPI. Preserve last known progress while reconnecting and exclude pre-existing bytes from the initial speed estimate.
- Extend real HTTP tests for cancellation, restart, concurrency, retries and local manager reconnection.

## 1.3.0 — 2026-10-16 (local build)

- Add durable server-owned pull/delete jobs with immediate acceptance, stable idempotency keys and queryable progress. Client disconnection and client shutdown do not cancel accepted jobs.
- Resume incomplete pulls after OSS restart. Never replay an unconfirmed delete. Keep deletion in the existing origin-aware inference queue.
- Advertise task capabilities in status/help and include active owned jobs in maintenance busy checks. Keep the legacy `/api/pull` streaming contract unchanged.
- Existing legacy transfers must finish before upgrading; no live transfer migration is attempted.


## 1.2.5 — 2026-10-16

- Fix empty model lists caused by LocalService lacking access to an existing model library, including default Windows user-profile directories and descendants with protected ACLs.
- On install and upgrade, check the complete model tree and grant LocalService Modify where needed. Preserve owners, inheritance settings and other accounts' rules; journal changes before applying them for uninstall/failure restoration.
- Run a temporary LocalService task to verify enumeration, existing-file access and create/write/rename/delete operations before starting the backend. Report the failing path if access remains blocked.
- Check both backend and gateway `/api/tags` before reporting success. Existing manifests with an empty backend list, mismatched inventories and failed access checks now fail installation.
- Do not follow model-directory junctions/reparse points into other data; select the actual directory when one is detected. Explicit deny rules or encryption may still require administrator intervention and are reported as errors.
- Retain the 1.2.4 concurrent pull/push fix and existing scheduling behavior.

## 1.2.4 — 2026-10-16

- Fix simultaneous `ollama pull` requests timing out in the single-slot inference queue. POST `/api/pull` and `/api/push` now stream concurrently to Ollama, without inference queue, read or total deadlines.
- Preserve upstream progress, errors and non-streaming responses; close the upstream connection on client disconnect.
- Track active transfer HTTP connections separately in status/help/dashboard. Transfers mark the service busy for maintenance but leave inference slots available (`can_start_now`). Transfer disconnects do not create persistent inference `unknown` records.
- Preserve local-priority inference scheduling and existing create/copy/delete scheduling.
- Add eight isolated real-HTTP transfer regressions, including cancellation before response headers, with no real model downloads.


## 1.2.3 — 2026-10-16

- Add a dedicated proxy ingress on `127.0.0.1:11436`; all requests entering it are classified as `remote` using the accepted socket's local port.
- Share one application lifecycle, scheduler, capacity limit and request store between direct and proxy listeners.
- Preserve non-preemptive local priority and FIFO within local/remote queues. Client-supplied origin and forwarding headers cannot grant local priority.
- Expose `request_origin` in `/help` and `/healthz` for migration checks without inference.
- Fail startup if either ingress cannot bind; check proxy-port conflicts before uninstalling during upgrades.
- Require both direct status and proxy `remote` health checks before reporting installation success.
- Provide FRP TOML/INI migration fragments, English-first README with six translations, build instructions and a sanitized offline release.

### Migration

On every server, upgrade during an idle maintenance window, then change the existing proxy's local target from `11434` to `127.0.0.1:11436`. Keep public routing, authentication and TLS. A proxy still forwarding to `11434` can still be classified as local.

### Inherited deployment behavior

The direct listener supports IPv4/IPv6. Since 1.2.2, installation/upgrade sets the product's TCP `11434` allow rule to any source/local address/network profile, restricted to the bundled Python executable. Old source restrictions are backed up but not automatically retained. No gateway authentication is added. Review network access after upgrading.

### Validation scope

The 1.2.3 implementation record includes 60 Python tests, 10 isolated upgrade scenarios, 14 C# model-discovery checks and four installer UI scenarios. Mock inference and isolated system interfaces do not substitute for target-machine GPU, FRP and deployment acceptance tests.

### Public distribution

The original client/development Word documents and matching Markdown files, production settings, logs and local build records are excluded. Public examples use placeholder addresses. The ZIP is rebuilt after public documentation changes, so its checksum differs from private builds of the same application version.
