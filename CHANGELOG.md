# Changelog

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
