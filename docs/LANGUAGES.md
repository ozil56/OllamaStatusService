# Languages in OSS 1.6.1

OSS includes offline UI resources for Simplified Chinese (`zh-CN`), Traditional Chinese (`zh-TW`), English (`en`), Japanese (`ja`), Korean (`ko`), German (`de`), French (`fr`) and Spanish (`es`). Unsupported languages fall back to English. Regional variants use their matching language; `zh-Hant`, Taiwan, Hong Kong and Macau use Traditional Chinese.

## Desktop and installation

The installer, uninstaller, tray menus, service confirmations and Ollama update window initially follow the Windows UI language. Choose a language in the window or the tray's **Language** submenu. **Auto** restores automatic matching. The selection is saved for that Windows user at `%LOCALAPPDATA%\OllamaStatusService\language.txt`.

Elevated workers receive the language explicitly, so progress and OSS confirmation text remain consistent after UAC. Windows-owned dialogs and the official Ollama installer retain the language behavior provided by Windows or Ollama. Raw third-party errors and technical logs are preserved for diagnosis.

The installer locks its language control while installation runs. The tray and update window can change display language without restarting services. The tray opens a localized offline guide from `locales/guide.<language>.html` in a built package. Detailed engineering documents remain in their existing languages.

## Web pages and API

`/manage` and `/dashboard` share a language selector. **Auto** follows the browser's `Accept-Language`, including quality preferences; manual choice is saved in the display-only `oss.language` cookie for one year. It does not change authentication or server settings. Each browser can use a different language. Changing language reloads the page; server-owned downloads continue.

`/status` and `/help` use the same preference. A query such as `/status?lang=ja` overrides the cookie; `?lang=auto` follows the browser. Responses provide `Content-Language` and avoid caching across language preferences.

The API retains existing keys, state codes and capabilities. `/status` adds `language`, `state_text` and `labels`. The manager adds display fields for known OSS errors and progress while retaining the original values. User-defined node names, model names, paths, identifiers and upstream payloads are never translated as UI assets.

## Maintaining translations

`locales/messages.json` contains the source text and all seven translated variants; Simplified Chinese uses the source. It is embedded into desktop executables and read locally by Python and PowerShell. There is no runtime translation service or network requirement. Preserve format placeholders, protocol strings and executable filenames when editing translations. Rebuild both native executables after catalog changes.

Run `tests/localization_http.py` for catalog completeness, placeholders, locale negotiation, rendered JavaScript syntax, API compatibility and active-download isolation. Run `tests/localization_checks.ps1` under Windows PowerShell 5.1 for native matching, saved choice, worker arguments, formatted diagnostics and rendered installation/update windows in all eight languages.

## Dynamic messages and saved history

Version summaries and timestamped updater history retain message data and are rendered again when the language changes. Updater and installer workers save `source_message` alongside the compatible `message` and `language` fields. Older update records are also readable. Version numbers, timestamps, filesystem paths and external diagnostics remain unchanged; translated text is used only for OSS-owned UI prose. Language changes do not run an installer or restart services.

The native regression harness drives the same check and polling paths as the real updater, using isolated release/status fixtures. It covers same-version state, an available update, failures, language changes while a check is pending, old Chinese records, mixed records from 1.6.0 and installer completion history. Preview images are taken from those states, rather than separately translated sample paragraphs.
