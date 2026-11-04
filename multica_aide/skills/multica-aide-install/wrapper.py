#!/usr/bin/env python3
"""Run this feature SOP using the installed AIDE module."""
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

module = Path(__file__).resolve().parents[3]
if not (module / 'multica_aide').is_dir():
    request = Request(os.environ['MULTICA_SERVER_URL'].rstrip('/') + '/api/issues/' + os.environ['MULTICA_ISSUE_ID'],
        headers={'Authorization': 'Bearer ' + os.environ['MULTICA_TOKEN'], 'X-Workspace-ID': os.environ['MULTICA_WORKSPACE_ID']})
    with urlopen(request, timeout=30) as response:
        module = Path(json.load(response)['metadata']['aide_checkout'])
sys.path.insert(0, str(module))
from multica_aide import sop_runtime
sop_runtime.ROOT = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.exit(sop_runtime.main())
