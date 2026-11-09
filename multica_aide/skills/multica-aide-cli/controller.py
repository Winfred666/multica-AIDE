"""One sequential optimization procedure; native Multica owns solution work."""
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

from sop import Branch, Completed, Option, Parameter, State, Workflow
from multica_aide import campaign, sop_runtime
from multica_aide.standalone import completed_node
from multica_aide.viewer import render_tree


# Campaign monitor checkpoints use the invoking standalone package's task record.
sop_runtime.ROOT = Path(__file__).resolve().parent


def artifacts(context):
    _, _, _, config = campaign.client(context)
    return {'aide-tree.html': render_tree(campaign.search(config).snapshot()['nodes'])}


def initialize(context):
    observed = campaign.observe(context)
    if observed['route'] == 'finished' and 'result' not in context:
        raise Completed('The existing node meets the exit condition.', {'node': observed['node']})
    return observed


def enter(args, context):
    """```mermaid
    flowchart LR
        enter_read[Read integrity observation and saved progress] --> enter_action[Apply setup block or candidate preparation when required]
        enter_action --> enter_return[Return action result and selected phase]
    ```"""
    observed = context['initial']
    target = observed['route']
    action = campaign.block_setup if target == 'needs_setup' else campaign.prepare_candidate if target in {'idea', 'test'} else None
    result = action(args, context) if action else {}
    return {'phase': target, **result}


def phase_check(context):
    issue, workdir, progress, config = campaign.client(context)
    phase = context['result']['phase'] if 'result' in context else context['phase']
    if phase in {'idea', 'developing', 'test', 'review', 'tested'} and not Path(progress['candidate']).is_dir():
        raise AssertionError('Candidate directory is missing.')
    if phase in {'test', 'review', 'tested'}:
        nodes = campaign.search(config).snapshot()['nodes']
        node = next(n for n in nodes if n['id'] == progress['node_id'])
        if (node['score'] is not None) != (phase == 'tested'):
            raise AssertionError('The solution node does not match the test state.')
    if phase == 'review' and progress.get('test_result', {}).get('state') != 'ended':
        raise AssertionError('The experiment must end before review.')
    if phase == 'finished' and completed_node(config) is None:
        raise AssertionError('The delivered node does not meet the exit condition.')
    return progress


def advance(args, context):
    """```mermaid
    flowchart LR
        advance_read[Read measured completion] --> advance_action[Finish client progress or prepare next candidate]
        advance_action --> advance_return[Return action result and selected phase]
    ```"""
    issue, workdir, progress, config = campaign.client(context)
    complete = completed_node(config)
    result = campaign.finish(args, context) if complete else campaign.next_candidate(args, context)
    return {'phase': 'finished' if complete else 'idea', **result}


def record(args, context):
    return {'phase': 'tested', **campaign.record(args, context)}


# The branch wrapper retains the campaign action's local decision diagram.
record.__doc__ = campaign.record.__doc__


def review_options(context):
    issue, workdir, progress, config = campaign.client(context)
    delivery = 'delivery' in progress['test_result']
    return (Option('record', 'record_result', 'Register the recorded JSON delivery and one review paragraph; report an unscored test failure through native Multica.',
                   record, params=(
                       Parameter('--delivery', 'Complete JSON delivery recorded by this test', type=json.loads, required=delivery),
                       Parameter('--review', 'One paragraph reviewing the measured result'))),)


def idea_options(context):
    issue, workdir, progress, config = campaign.client(context)
    separate = config['mode'] == 'separate'
    return (Option('develop' if separate else 'test', 'developing' if separate else 'test',
                   'Describe the idea and dispatch the existing developer.' if separate else 'Describe this solution idea and start its test.',
                   campaign.delegate if separate else campaign.describe,
                   params=(Parameter('--description', 'One paragraph describing this solution idea'),)),)


workflow = Workflow(
    State('initialize', 'Check the benchmark, baseline, solution tree and delivery exit condition.',
          initial=True, precheck=initialize, options=(
              Option('enter', 'initialize_result', 'Enter the state selected by the integrity and baseline checks.', enter),),
          view=lambda context: {'data': context['initial']}),
    State('idea', 'Use the configured mode: improve this worktree directly, or dispatch its installed developer child.',
          precheck=phase_check, view=campaign.candidate_view, options=idea_options),
    State('developing', 'Review the developer delivery, then run locked validation in this issue.',
          precheck=phase_check, view=campaign.candidate_view, options=(
              Option('continue', 'development_result', 'Check developer delivery and begin validation when ready.', campaign.development),)),
    State('test', 'Read the collected test progress. Choose and invoke continue or exception in this turn.',
          precheck=phase_check, view=campaign.test_view, options=(
              Option('continue', 'test_result', 'Monitor until an important log, time checkpoint or test exit.', campaign.monitor_test),
              Option('exception', 'review', 'End this test and review its recorded result.', campaign.exception_test),
          )),
    State('review', 'Supply the official JSON delivery and one review paragraph. An interrupted or invalid test remains unscored.',
          precheck=phase_check, view=campaign.test_view, options=review_options),
    State('tested', 'The node is scored. The exit condition selects completion or another idea.',
          precheck=phase_check, view=campaign.candidate_view, options=(
              Option('continue', 'completion_result', 'Finish with the passing node, or prepare the next candidate.', advance),)),
    Branch('initialize_result', 'Integrity, baseline and saved progress', {
        'setup required': 'needs_setup', 'goal met': 'finished', 'root pending': 'waiting',
        'candidate needed': 'idea', 'developer pending': 'developing', 'test pending': 'test',
        'test ended': 'review', 'node scored': 'tested'}),
    Branch('development_result', 'Developer delivery ready?', {
        'pending': 'developing', 'blocked or cancelled': 'needs_setup', 'ready': 'test'}),
    Branch('test_result', 'Experiment ended?', {'running': 'test', 'ended': 'review'}),
    Branch('record_result', 'Scorable delivery recorded?', {'yes': 'tested', 'no': 'test_failed'}),
    Branch('completion_result', 'Measured exit condition met?', {
        'yes': 'finished', 'no': 'idea', 'valid baseline test launched': 'test'}),
    State('waiting', 'Wait for client-1 to establish the measured root.', options=(
        Option('check', 'initialize', 'Check the shared baseline again.'),)),
    State('needs_setup', 'The parent must fix the failed initialization check.', final=True),
    State('test_failed', 'The test has no scorable delivery. Report the recorded review and logs through native Multica block.', final=True),
    State('finished', 'A measured solution meets the benchmark exit condition. Deliver that node through native Multica.', final=True, precheck=phase_check),
)
