"""Python 3 client example: choose a server and safely fail over BEFORE execution.

No third-party packages required. Uses non-streaming /api/generate for clarity.
Existing Ollama clients can instead just set X-Ollama-Queue-Timeout themselves.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def call_servers(servers, model, prompt, queue_seconds=0, response_timeout=3700):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ranked = []
    # These GETs are only hints; final admission is atomic at each server.
    for order, server in enumerate(servers):
        server = server.rstrip('/')
        try:
            with opener.open(server + '/status', timeout=1) as response:
                state = json.load(response)
            rank = 0 if state.get('can_start_now') else 1 if state.get('accepting_requests') else 2
        except (urllib.error.URLError, OSError, ValueError):
            rank = 3
        ranked.append((rank, order, server))
    refusals = []
    for rank, _, server in sorted(ranked):
        if rank == 3:
            refusals.append(server + ': status probe unavailable; no POST sent')
            continue
        req = urllib.request.Request(server + '/api/generate',
            data=json.dumps({'model': model, 'prompt': prompt, 'stream': False}).encode(),
            headers={'Content-Type': 'application/json', 'X-Ollama-Queue-Timeout': str(queue_seconds)})
        try:
            with opener.open(req, timeout=response_timeout) as response:
                return server, json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read()
            try:
                failure = json.loads(body)
            except ValueError:
                failure = {}
            if (error.code in (429, 503) and error.headers.get('X-Ollama-Forwarded') == 'false'
                    and isinstance(failure, dict) and failure.get('forwarded') is False):
                refusals.append(server + ': ' + body.decode('utf-8', errors='replace'))
                continue  # Explicit proof that this request was NOT sent to Ollama.
            raise RuntimeError(f'{server}: HTTP {error.code}; automatic replay disabled: '
                               + body.decode('utf-8', errors='replace')) from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            # A POST timeout/reset can happen AFTER delivery. Never blindly replay it.
            raise RuntimeError(f'{server}: delivery/completion uncertain; no automatic replay') from error
    raise RuntimeError('Every server declined before execution: ' + '; '.join(refusals))


def main():
    parser = argparse.ArgumentParser(description='Choose an Ollama server; retry only explicit pre-execution refusals.')
    parser.add_argument('--servers', nargs='+', required=True, help='e.g. http://192.168.1.10:11434 http://192.168.1.11:11434')
    parser.add_argument('--model', required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--queue-seconds', type=float, default=0, help='0 = switch immediately when busy; N = wait at most N seconds per server')
    parser.add_argument('--response-timeout', type=float, default=3700, help='Includes queue time and inference; timeout is NOT a failover signal')
    args = parser.parse_args()
    if args.queue_seconds < 0 or args.response_timeout <= args.queue_seconds:
        parser.error('queue-seconds must be nonnegative; response-timeout must exceed queue-seconds')
    server, result = call_servers(args.servers, args.model, args.prompt, args.queue_seconds, args.response_timeout)
    print(json.dumps({'server': server, 'result': result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
