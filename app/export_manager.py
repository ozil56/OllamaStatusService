"""Export the old manager's node list under its original Windows account.

The output contains existing peer credentials. Keep it outside public packages.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from manage_core import credentials, normalize_url


def export(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute('SELECT * FROM servers ORDER BY created, rowid')]
    servers = []
    for row in rows:
        url, embedded = normalize_url(row['url'])
        if urlsplit(url).hostname in {'localhost', '127.0.0.1', '::1', 'oss.internal'}:
            continue
        auth = credentials(row) or embedded or {}
        servers.append(dict(name=row['name'], url=url, enabled=bool(row['enabled']), auth=auth))
    # Exclusive creation prevents silently replacing another migration file.
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'schema': 1, 'servers': servers}, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return len(servers)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Old data/manager.sqlite3')
    parser.add_argument('--output', required=True, help='Private output JSON, outside the public package')
    args = parser.parse_args()
    count = export(args.source, args.output)
    print(f'Exported {count} nodes to {Path(args.output).resolve()}. Contains existing peer credentials; keep private.')
