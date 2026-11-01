# Automatic recovery in 1.6.2

The Windows SYSTEM task `OllamaStatusServiceWatchdog` runs once a minute. It uses
the configured direct listener and checks `/status`, service state, real inference
activity and scheduler liveness. `/healthz` remains a liveness endpoint; its success
alone does not mean that OSS can accept work.

After five consecutive minutes of unavailability, with no observable active work,
the watchdog recovers the service. Normal detection latency is five to six minutes,
plus the time required to stop and start the services. Switching between error
states does not restart the countdown. A healthy or actively working state does.

Recovery covers:

- Uncertain completion (`unknown`), including upstream timeouts, truncated or
  invalid responses, gateway crashes/restarts and request journal errors.
- An offline backend, an unresponsive status endpoint or unexpectedly stopped services.
- A dead dispatcher, stale running records with no actual worker, queues that do
  not dispatch, or a service that refuses work without an active task.

Normal inference, model transfers, model workers, uploads and download relay
connections prevent recovery while they are active. A gateway that remains
unresponsive cannot prove whether inference is still running; recovery can abort
such unconfirmed work. This is not a promise to repair failed disks, permissions,
corrupt databases, broken installations or external reverse proxies.

## Recovery sequence

1. Recheck current status. Close admission with an administrator-owned recovery
   marker, freeze queued dispatch, and recheck active work to avoid admission races.
2. Persist the recovery attempt, then stop the gateway and backend.
3. Verify backend listeners and owned processes have exited. The database cleaner
   independently checks IPv4/IPv6 listeners and takes the exclusive instance lock.
4. Mark unfinished records aborted, preserving their previous reason. Never replay
   inference or delete model files. Download jobs retain their existing resume policy.
5. Start the backend and gateway, verify backend availability and scheduler liveness,
   reopen admission and check status again.

A failed attempt retains its recovery record and admission gate. It retries every
five minutes, including after interruption or reboot. Concurrent watchdog runs are
excluded. The scheduled task has a ten-minute execution limit; an interrupted
recovery is resumed on a later run.

## Operator control and diagnosis

- Tray **Stop** records an explicit operator stop, so automatic recovery will not
  undo it. Tray **Start** or **Recover** clears that intent. A Windows reboot also
  expires the old stop intent, matching the services' automatic startup. Recovery may abort
  unfinished work, as before. For maintenance outside the tray, disable the watchdog
  task before stopping services and re-enable it afterward.
- Ollama/OSS update maintenance suspends automatic recovery. The watchdog never
  removes an updater's maintenance marker or runs an official installer.
- Check `%ProgramData%\OllamaStatusService\logs\watchdog.jsonl` for detection,
  recovery, failure and success records. It retains one rotated 5 MiB log.
- Recovery state is in `updater\watchdog-state.json`; `watchdog-recovery.lock`
  closes admission during recovery. `operator-stop.json` records an explicit stop.
  Do not delete these files or the request database to force an apparently idle state.
- Upgrade through `Install.exe` during an idle window. It registers the updated
  SYSTEM watchdog and preserves configuration, models and data. No logged-in user
  or tray application is required for automatic recovery.

Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\watchdog_checks.ps1`
for isolated state-machine tests. `tests\ollama_update_guard.py` exercises both
real HTTP listeners, the recovery gate, active inference and queued dispatch.
