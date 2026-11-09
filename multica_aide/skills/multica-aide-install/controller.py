"""Prepare, confirm, install and start one native campaign."""
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

# Copied client skills locate their installed campaign module through the native issue.
module = Path(__file__).resolve().parents[3]
if not (module / 'multica_aide').is_dir():
    request = Request(os.environ['MULTICA_SERVER_URL'].rstrip('/') + '/api/issues/' + os.environ['MULTICA_ISSUE_ID'],
        headers={'Authorization': 'Bearer ' + os.environ['MULTICA_TOKEN'], 'X-Workspace-ID': os.environ['MULTICA_WORKSPACE_ID']})
    with urlopen(request, timeout=30) as response:
        module = Path(json.load(response)['metadata']['aide_checkout'])
sys.path.insert(0, str(module))

from sop import Option, Parameter, State, Workflow
from multica_aide import campaign
from multica_aide.standalone import install



def settings(context):
    config = campaign.installer_config()
    result = install(config, context['issue_id'])
    facts = {key: config[key] for key in ('drafts', 'clients', 'mode', 'port', 'template') if key in config}
    facts['benchmark'] = config['benchmark']['description']
    facts['agents'] = {key: value for key, value in config.items() if key.startswith('AIDE_')}
    return {'data': result, 'instruction': 'Confirm these campaign settings:\n' + json.dumps(facts, indent=2)}


workflow = Workflow(
    State('prepare', 'Prepare the requested official benchmark, delivery contract and local configuration. Reuse downloaded data, the Python environment and native runtime.',
          initial=True, options=(
              Option('prepare', 'confirm', 'Validate the prepared benchmark and display configuration.', campaign.prepare_install,
                     params=(Parameter('--settings', 'Path to config.local.yaml', type=Path),)),
          )),
    State('confirm', 'Confirm D, N, mode, named agents, template branch, port and benchmark. Select separate mode explicitly when the validation set must stay private; otherwise use val_dev. Reuse answers already given.',
          view=settings, options=(Option('install', 'installed', 'Install the confirmed configuration.', campaign.confirm_install),)),
    State('installed', 'The native clients are prepared. Start their SOP loop and HTML board.',
          options=(Option('start', 'running', 'Run the campaign in this task until it completes or needs setup.', campaign.start_install),)),
    State('running', 'The campaign controller returned. Report its measured result or setup blocker.', final=True),
)
