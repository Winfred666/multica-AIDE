"""Prepare, confirm, install and start one native campaign."""
from pathlib import Path

from sop import Option, Parameter, State, Workflow
from multica_aide import campaign
from multica_aide.standalone import install


def settings(context):
    return {'data': install(campaign.installer_config(), context['issue_id'])}


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
