"""Read-only preflight using the NEW runtime against the EXISTING configuration."""
import argparse
from contextlib import closing
from dataclasses import fields
import json
from pathlib import Path
import sqlite3

from gateway import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--check-idle', action='store_true')
    args = parser.parse_args()
    values = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    unknown = set(values) - {field.name for field in fields(Settings)}
    if unknown:
        raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(unknown)))
    settings = Settings(**values)
    settings.validate()
    if args.check_idle:
        database = Path(settings.data_dir) / 'requests.sqlite3'
        if database.exists():
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                count = db.execute("SELECT COUNT(*) FROM requests WHERE state IN ('running','queued','unknown')").fetchone()[0]
            if count:
                raise RuntimeError('任务尚未结束或存在 unknown，暂停升级。请先等待任务结束；unknown 请通过托盘恢复后再升级。')
    print('Existing configuration and task journal validated (read-only).')


if __name__ == '__main__':
    main()
