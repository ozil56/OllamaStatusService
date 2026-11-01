# Installation and service lifecycle fixes in 1.6.3

## Extracted package directory

The old installer and Open-Tray launcher omitted the installed tray's working
directory. A background process could inherit the extraction directory and keep
a Windows directory handle open even after every file inside it had been deleted.

Both launchers now set the working directory to the installed tray's directory.
The tray also selects its own executable directory at entry, including its hidden
administrative workers. After the installer window exits, its extracted package
is disposable. A still-running installer must itself be closed before removal.

## Stopping services

The installation engine already requests administrator elevation. Administrator
rights allow service control, but do not make a pending or unresponsive service
stop instantly. The old one-shot stop aborted on a control exception, omitted
nested Windows errors, and had no bounded process-level fallback.

For each OSS service, starting with the gateway, 1.6.3:

1. Observes fresh service status, waits through pending states and retries eligible
   control requests at five-second intervals for up to 100 seconds.
2. Rechecks status after request errors. Stopped or already-removed services count
   as success; pending states do not cause an immediate installation failure.
3. If graceful stopping times out, requires administrator elevation and verifies
   the service name, registered executable, dedicated process type, executable of
   the live process, sole ownership and unchanged PID. Only that process tree is
   terminated; no process-name-wide killing is used.
4. Before forced termination, saves startup mode and delayed-start settings in the
   protected updater directory and temporarily disables the service. This prevents
   SCM failure-restart actions from restarting it during database cleanup. Managed
   startup restores the saved settings; successful uninstall discards obsolete
   records. A newer startup-mode decision from the Ollama updater is respected.
5. Waits up to another 20 seconds for Stopped. Uninstall, database cleanup and
   replacement cannot continue on an unverified stop.

The existing idle/admission checks still precede upgrades. The tray's explicit
Stop/Recover operations retain their interruption warning. No service ACL is
weakened. Process ownership mismatches and access failures are reported instead
of forcing operations on unrelated programs.

Diagnostics: `%ProgramData%\OllamaStatusService\logs\service-control.jsonl`
(5 MiB plus one rotated log). Pending forced-stop startup settings are in
`updater\forced-stop-OllamaStatusGateway.json` or the corresponding backend file.
Do not remove these records to bypass an incomplete operation.

## Desktop dialogs

The management PowerShell process previously created a separate unthemed form.
The tray's early worker entry also skipped visual-style initialization. Desktop
entry points now initialize the theme before dispatch, and PowerShell loads the
same `OssDialog` implementation from the tray assembly. It does not run the tray.
Installer/uninstaller, updater, confirmations and errors share typography, colors
and button styling. Confirmation defaults and initial focus remain Cancel; long
errors remain selectable and scrollable. Native file/folder selection uses the
Windows Common Item Dialog and follows the operating system's appearance.

## Verification

`tests\working_directory_checks.ps1` uses real compiled worker processes and
Windows directory deletion. An optional, already retained 1.6.2 baseline reproduces
the empty-folder lock; that Release is withdrawn and is not needed for normal
checks. The fixed worker releases the extraction directory while remaining alive.
It also checks both compiled launchers' working-directory configuration.

`tests\service_lifecycle_checks.ps1` replaces only SCM/process boundaries with
fixtures to exercise stopping, retries, force-stop scope, restart suppression and
startup restoration. It does not stop production services or alter their ACLs.

`tests\dialog_checks.ps1` checks eight translated confirmations, Enter/Escape,
explicit confirmation, long diagnostics, native folder COM configuration and
the actual PowerShell dialog bridge. `tests\localization_checks.ps1` renders both
main windows in eight languages and checks dynamic translations. The optional
DialogTests `--visual-preview` is an isolated UI fixture: its choices only write
the selected test directory and never control services.
