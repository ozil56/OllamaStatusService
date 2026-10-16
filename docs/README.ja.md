[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**Ollama の稼働状態を可視化し、実行中の処理を中断せずにローカルの待機リクエストを優先します。**

バージョン **1.2.5 · Windows x64**。[ダウンロード](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.2.5) · [変更履歴（英語）](../CHANGELOG.md) · [ビルド手順（英語）](BUILD.md)

既存の Ollama に HTTP ゲートウェイ、Windows サービス、トレイ表示を追加します。モデルがメモリに読み込まれていることと、推論を実行中であることは別です。本サービスはゲートウェイを通るリクエストを追跡します。Ollama 本体、モデル、GPU ドライバーは同梱しません。アプリの UI は現在簡体字中国語です。README の翻訳は UI の翻訳ではありません。

1.2.5 では、LocalService の権限不足による空のモデル一覧を修正しました。インストールと更新時にモデルフォルダー全体の権限を補い、実際のサービスアカウントでアクセスとモデル一覧を確認します。既定の C ドライブ内ユーザーフォルダーにも対応します。

1.2.4 では、同時実行する `ollama pull`/`push` のタイムアウトを修正しました。モデル転送は推論キューとその時間制限を使用せず、進捗をストリーミングします。転送は `/status` に別集計され、保守のため状態は `busy` になりますが、推論枠は消費しません。推論の開始可否には `can_start_now` を使用してください。

## 主な機能

- `/status` と `/dashboard`：状態、実行数、待機数、リクエストの送信元。
- ローカル優先、同じ種別では FIFO。実行中の処理は完了まで継続します。将来のバッチ用に枠を予約しません。
- `/help`、サービス検出、OpenAPI による機械可読の制限・接続ガイド。
- 緑＝空き、橙＝処理中、赤＝オフライン、紫＝完了状態が不明。トレイを終了してもサービスは動作します。
- モデル保存先の自動検出、整合性検証、アップグレード前の状態・ポート確認。

## インストール

Windows 10/11 x64、またはデスクトップ環境のある Windows Server が対象です。既存の Ollama とローカルモデル、PowerShell 5.1、.NET Framework 4.8、タスクスケジューラ、ファイアウォール管理機能が必要です。インストールには管理者権限を使います。

1. Release の `OllamaStatusService-Windows-x64-v1.2.5.zip` と `.sha256.txt` を取得し、`Get-FileHash -Algorithm SHA256` で比較します。GitHub の Source code ZIP はインストーラではありません。
2. 新規投入を止め、実行・待機中の処理が終わってから、新しいフォルダーへ全体を展開します。
3. **`安装.exe`** を起動し、Ollama とモデルのパスを確認して UAC を許可します。更新にも同じプログラムを使います。
4. `http://127.0.0.1:11434/dashboard` を確認します。実行環境と依存関係は同梱され、インストール時のダウンロードは不要です。

通常のスケジューリングは非プリエンプティブですが、インストール、更新、停止、復旧では処理が中断され得ます。保守時間を確保してください。旧版削除後に新版のインストールが失敗しても、自動ロールバックは保証されません。削除は **`卸载.exe`**。既存の Ollama とモデルは保持されます。

## ポートと FRP

| ポート | 役割 |
| --- | --- |
| `11434` | IPv4/IPv6 の直接接続。実 Socket から local / remote を判定 |
| `11436` | 既定は `127.0.0.1`。プロキシ専用、常に remote |
| `11435` | ループバックの Ollama バックエンド。直接利用しない |

各サーバーの既存 FRP エントリを `localIP = "127.0.0.1"`、`localPort = 11436` に変更します。INI では `local_ip` / `local_port`。公開ポート、名前、認証、TLS は維持します。同じマシンの Nginx/Caddy/SSH トンネルも `11436` を使用します。別マシンのプロキシには明示的な待受設定と接続元を制限したファイアウォールが必要です。PROXY protocol は解析しません。

両入口は一つのキューを共有します。HTTP ヘッダーで優先度を偽装できません。`/help` の `request_origin` は直接接続で local、プロキシ経由で remote であることを確認してください。`11434` へ転送し続けると、ループバックが local と見なされます。

## クライアントと安全性

`X-Ollama-Queue-Timeout` は待機秒数です。`0` は混雑時に即拒否、省略時は既定 15 秒。実際の上限は `/help` を確認します。開始後の推論をこの待機期限で中断しません。本機優先には実際にキューへ入る必要があり、ローカルの連続投入でリモートが期限切れになる場合があります。

自動切替が安全と判断できるのは **HTTP 429/503、`X-Ollama-Forwarded: false`、JSON `forwarded: false` の全条件**を満たす場合です。タイムアウトや部分出力は未実行の証拠ではありません。`can_start_now` は予約ではなく瞬間の状態です。

**ゲートウェイには追加の認証がありません。** 1.2.2 以降、インストール・更新時に製品の TCP `11434` 許可規則を全接続元・全ローカルアドレス・全ネットワークプロファイルへ設定します（同梱 Python に限定）。以前の制限はバックアップされますが、自動維持されません。認証、HTTPS、ネットワーク制御を別途保ち、更新後に規則を確認してください。`11435` / `11436` は既定でループバックのみです。

完了を確認できない `unknown` 状態では新規受付を停止します。復旧はバックエンドを停止・確認してから不確定記録を消去します。実行中に DB を削除しないでください。待機データは再起動後に再実行されません。設定は `%ProgramData%\OllamaStatusService` に保存します。

テストは模擬バックエンドを使用し、すべての実環境の GPU・FRP・更新を保証するものではありません。[英語 README](../README.md) に仕様と検証範囲があります。

## ライセンス

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

[サードパーティーのライセンス](../licenses/THIRD-PARTY-NOTICES.txt)も参照してください。
