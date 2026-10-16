# Building and testing on Windows

The repository contains application source, C# GUI source, PowerShell lifecycle scripts, tests, public documentation and images. Compiled executables and vendored dependencies belong in release assets, not Git history.

## Build a complete offline package

Use Windows x64 with Windows PowerShell 5.1 and the .NET Framework 4.8 compiler. No service installation or administrator session is required to compile and package.

1. Download the **1.2.3 Windows release ZIP and checksum** from the project's Releases page. Verify its SHA256 and extract it completely. This supplies the pinned Python runtime, Python wheels already installed under `vendor`, and WinSW.
2. Obtain the source checkout at tag `v1.2.3`.
3. Run this from the source root, replacing the example dependency path:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\Build-Package.ps1 `
  -DependencyBundle 'C:\Build\Dependencies\OllamaStatusService' `
  -OutputDirectory 'C:\Build\Output-1.2.3'
```

`Build-Package.ps1` verifies the dependency bundle's manifest, dependency lock, Python version and WinSW hash. It copies only an explicit public source list, recompiles the installer/uninstaller and tray from C#, regenerates the HTML guide and encoding-sensitive launchers, then creates the ZIP, internal file manifest and external SHA256. It refuses to overwrite an existing output package.

This is an **offline source rebuild using dependencies from the verified release**, not a dependency-bootstrap script. Rebuilding can produce different ZIP timestamps and executable metadata, so bit-for-bit identity is not promised.

### Dependency provenance

- Python 3.13.12 Windows x64 embeddable distribution: <https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip>
  - Original archive SHA256: `76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0`.
  - The package's `runtime/python313._pth` adds `..\app`, `..\vendor` and enables `import site`.
- WinSW 2.12.0 x64: <https://github.com/winsw/winsw/releases/tag/v2.12.0>
  - `bin/WinSW.exe` SHA256: `b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f`.
- Python library versions are pinned in [`requirements.lock.txt`](../requirements.lock.txt), with distribution licenses in the release's `vendor/*.dist-info` directories.

If recreating dependencies independently, use a Python 3.13 x64 environment to install the locked requirements into `vendor`, preserve all license files, and validate the resulting package. The supplied build command deliberately uses the already verified release dependencies.

## Run isolated checks

Run these commands **from the newly built package directory**, which contains `runtime` and `vendor`:

```powershell
.\runtime\python.exe tests\proxy_ingress.py
.\runtime\python.exe tests\local_priority.py
.\runtime\python.exe tests\dual_stack.py
.\runtime\python.exe tests\integration.py
.\runtime\python.exe tests\upgrade_validation.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\upgrade_checks.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\model_discovery_checks.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\script_checks.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\config_checks.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\native_process_checks.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\package_integrity_checks.ps1
```

The five Python suites contain 60 tests. They use temporary ports and a mock inference backend, not production models. The upgrade scenarios replace system service/firewall/task interfaces with isolated fixtures. `setup_ui_checks.ps1` additionally opens real installer windows for interactive GUI checks; it does not install or uninstall production services.

These checks do not prove successful installation on every Windows host or real GPU/public FRP performance. Deployment acceptance must still verify origins and the ordering of a running remote request, a queued remote request, and a queued local request.

## Encoding and release hygiene

- CMD launchers: GB2312, CRLF, no UTF-8 BOM. `.gitattributes` prevents Git from treating them as text to normalize.
- PowerShell lifecycle scripts: UTF-8 with BOM and CRLF for Windows PowerShell 5.1.
- Python, Markdown and JSON: UTF-8.
- Do not commit installed `config.json`, request databases, logs, credentials, local build output or private source documents.
- The original client/development Word documents **and their matching Markdown files** are excluded from both repository and release packages.
- Rebuild the file manifest and ZIP checksum after changing any packaged file.
