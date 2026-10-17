"""Isolated manual browser fixture. Never connects to real OSS or real models."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage_http import ManageTests, SELF


parser = argparse.ArgumentParser()
parser.add_argument('--state', required=True)
parser.add_argument('--stop', required=True)
args = parser.parse_args()
fixture = ManageTests()
try:
    fixture.setUp()
    fixture.api('servers/'+SELF+'/pull', 'POST', {'model':'held-progress-browser-preview'})
    peer = fixture.second_node()
    fixture.api('servers', 'POST', {'name':'测试远端节点', 'url':peer})
    state = {'url':fixture.direct+'/manage', 'temporary_data':str(fixture.work)}
    Path(args.state).write_text(json.dumps(state), encoding='utf-8')
    print(json.dumps(state), flush=True)
    while not Path(args.stop).exists():
        time.sleep(.2)
finally:
    fixture.doCleanups()
