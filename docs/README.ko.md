[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**Ollama의 실제 작업 상태를 확인하고, 실행 중인 작업을 끊지 않으면서 로컬 대기 요청에 다음 순서를 제공합니다.**

**1.2.5 · Windows x64** · [다운로드](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.2.5) · [변경 기록](../CHANGELOG.md) · [소스 빌드](BUILD.md)

기존 Ollama 앞에 HTTP 게이트웨이, Windows 서비스, 트레이 아이콘을 추가합니다. 메모리에 로드된 모델이 반드시 추론 중인 것은 아닙니다. 이 서비스는 게이트웨이를 통과하는 요청의 수명을 추적합니다. Ollama, 모델, GPU 드라이버는 포함하지 않습니다. 앱 UI는 현재 중국어 간체이며, README 번역이 UI 번역을 의미하지는 않습니다.

1.2.5는 LocalService 권한 부족으로 모델 목록이 비어 보이는 문제를 수정합니다. 설치 및 업그레이드 시 모델 폴더 전체의 권한을 보완하고 실제 서비스 계정으로 접근과 모델 목록을 확인합니다. C 드라이브의 기본 사용자 폴더도 지원합니다.

1.2.4는 동시 `ollama pull`/`push` 시간 초과를 수정합니다. 모델 전송은 추론 대기열과 시간 제한을 적용받지 않으며 진행 상황을 스트리밍합니다. 전송은 `/status`에서 별도로 집계되고 유지보수를 위해 상태가 `busy`가 되지만 추론 슬롯은 차지하지 않습니다. 추론 시작 가능 여부는 `can_start_now`로 확인하세요.

## 주요 기능

- `/status`, `/dashboard`: 상태, 실행·대기 수, 요청 출처와 ID.
- 로컬 대기 요청 우선, 같은 종류에서는 FIFO. 현재 작업은 끝까지 실행하며 미래 배치의 자리를 예약하지 않습니다.
- `/help`, 서비스 검색, OpenAPI: 실제 제한과 연동 규칙을 기계가 읽을 수 있는 형식으로 제공합니다.
- 녹색은 유휴, 주황은 작업 중, 빨강은 오프라인, 보라는 완료 여부 불명입니다. 트레이를 종료해도 서비스는 계속 실행됩니다.
- 모델 경로 탐색, 설치 파일 무결성 검사, 업그레이드 전 상태·포트 검사.

## 설치 및 업그레이드

Windows 10/11 x64 또는 데스크톱이 있는 Windows Server, 기존 Ollama와 로컬 모델, PowerShell 5.1, .NET Framework 4.8, 작업 스케줄러와 방화벽 관리 기능이 필요합니다. 설치에는 관리자 권한을 사용합니다.

1. Release에서 `OllamaStatusService-Windows-x64-v1.2.5.zip`과 `.sha256.txt`를 내려받고 `Get-FileHash -Algorithm SHA256`으로 비교합니다. GitHub의 Source code ZIP은 설치 패키지가 아닙니다.
2. 새 요청을 중단하고 실행·대기 작업이 끝난 뒤, 새 폴더에 전체 압축을 풉니다.
3. **`安装.exe`**를 실행하여 Ollama와 모델 경로를 확인하고 UAC를 허용합니다. 기존 버전도 같은 실행 파일로 업그레이드합니다.
4. `http://127.0.0.1:11434/dashboard`를 확인합니다. 런타임과 의존성이 포함되어 설치 중 다운로드하지 않습니다.

비선점 보장은 정상 스케줄링에 적용됩니다. 설치, 업그레이드, 서비스 중지·복구는 작업을 중단할 수 있으므로 유지보수 시간에 진행하세요. 구버전 제거 후 새 설치가 실패하면 오류와 백업을 남기지만 자동 롤백을 보장하지 않습니다. 제거는 **`卸载.exe`**이며 원래 Ollama와 모델은 보존합니다.

## 포트 및 FRP

| 포트 | 역할 |
| --- | --- |
| `11434` | IPv4/IPv6 직접 연결, 실제 소켓으로 local / remote 판정 |
| `11436` | 기본 `127.0.0.1`, 프록시 전용, 항상 remote |
| `11435` | 루프백 Ollama 백엔드, 직접 우회 호출 금지 |

각 서버의 기존 FRP 항목을 `localIP = "127.0.0.1"`, `localPort = 11436`으로 변경합니다. INI는 `local_ip` / `local_port`입니다. 공개 포트, 이름, 인증, TLS는 유지합니다. 같은 호스트의 Nginx/Caddy/SSH 터널도 `11436`으로 연결합니다. 다른 호스트의 프록시는 명시적인 바인딩과 출처 제한 방화벽이 필요합니다. PROXY protocol은 지원하지 않습니다.

두 진입점은 하나의 큐를 공유합니다. HTTP 헤더로 로컬 우선순위를 얻을 수 없습니다. `/help`의 `request_origin`이 직접 로컬 연결에서는 local, 프록시에서는 remote인지 확인하세요. 프록시를 계속 `11434`에 연결하면 루프백 요청이 local로 분류될 수 있습니다.

## 요청 정책 및 안전 경계

`X-Ollama-Queue-Timeout`은 대기 시간(초)입니다. `0`은 바쁠 때 즉시 거절하고 생략 시 기본 15초를 사용합니다. 실제 제한은 `/help`에서 확인하세요. 추론이 시작된 뒤에는 이 대기 시간으로 중단하지 않습니다. 로컬 우선순위는 실제로 큐에 들어온 요청에만 적용되며, 지속적인 로컬 요청은 원격 요청의 시간 초과를 유발할 수 있습니다.

안전한 자동 서버 변경은 **HTTP 429/503 + `X-Ollama-Forwarded: false` + JSON `forwarded: false`**가 모두 확인될 때만 가능합니다. 시간 초과, 연결 끊김, 일부 출력은 미실행 증거가 아닙니다. `can_start_now`는 스냅샷이며 예약이 아닙니다.

**게이트웨이는 인증 기능을 추가하지 않습니다.** 1.2.2부터 설치·업그레이드는 제품의 TCP `11434` 허용 규칙을 모든 출처·로컬 주소·네트워크 프로필로 설정하며, 동봉 Python 프로그램으로 제한합니다. 이전 제한은 백업되지만 자동 유지되지 않습니다. 인증, HTTPS, 네트워크 접근 제어를 유지하고 업데이트 후 규칙을 확인하세요. `11435`와 `11436`은 기본 루프백 전용입니다.

완료 여부를 확인할 수 없는 `unknown` 상태는 새 작업을 차단합니다. 복구는 백엔드 종료를 확인한 뒤 불확실한 기록을 지웁니다. 실행 중 DB를 삭제하지 마세요. 대기 요청은 재시작 후 재실행하지 않습니다. 설정은 `%ProgramData%\OllamaStatusService`에 저장합니다.

테스트는 모의 백엔드를 사용하며 모든 실제 서버의 GPU·FRP·배포를 보증하지 않습니다. 전체 설명과 검증 범위는 [영문 README](../README.md)를 참조하세요.

## 라이선스

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

[타사 구성 요소 고지](../licenses/THIRD-PARTY-NOTICES.txt)도 적용됩니다.
