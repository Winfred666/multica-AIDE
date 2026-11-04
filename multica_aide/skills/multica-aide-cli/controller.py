"""One sequential optimization procedure; native Multica owns solution work."""
import json
from pathlib import Path

from sop import Completed, Option, Parameter, State, Workflow
from multica_aide import campaign
from multica_aide.standalone import completed_node


def initialize(context):
    observed = campaign.observe(context)
    if observed['route'] == 'finished' and 'result' not in context:
        raise Completed('The existing node meets the exit condition.', {'node': observed['node']})
    return observed


def entry(context):
    observed = context['initial']
    target = observed['route']
    action = campaign.block_setup if target == 'needs_setup' else campaign.prepare_candidate if target in {'idea', 'test'} else None
    return (Option('enter', target, 'Enter the state selected by the integrity and baseline checks.', action),)


def phase_check(context):
    issue, workdir, progress, config = campaign.client(context)
    phase = context.get('result', {}).get('phase', context['phase'])
    if phase in {'idea', 'test', 'tested'} and not Path(progress['candidate']).is_dir():
        raise AssertionError('Candidate directory is missing.')
    if phase in {'test', 'tested'}:
        nodes = campaign.search(config).snapshot()['nodes']
        node = next(n for n in nodes if n['id'] == progress['node_id'])
        if (node['score'] is not None) != (phase == 'tested'):
            raise AssertionError('The solution node does not match the test state.')
    if phase == 'finished' and completed_node(config) is None:
        raise AssertionError('The delivered node does not meet the exit condition.')
    return progress


def after_test(context):
    issue, workdir, progress, config = campaign.client(context)
    complete = completed_node(config)
    return (Option('continue', 'finished' if complete else 'idea',
                   'Finish with the passing node, or prepare the next candidate.',
                   campaign.finish if complete else campaign.next_candidate),)


workflow = Workflow(
    State('initialize', 'Check the benchmark, baseline, solution tree and delivery exit condition.',
          initial=True, precheck=initialize, options=entry,
          view=lambda context: {'data': context['initial']}),
    State('idea', 'Use native development and review with the supplied solver to produce a valid candidate. Describe its idea in one paragraph before testing.',
          precheck=phase_check, view=campaign.candidate_view, options=(
              Option('test', 'test', 'Register the idea description and enter test.', campaign.describe,
                     params=(Parameter('--description', 'One paragraph describing this solution idea'),)),
          )),
    State('test', 'Run the official test. Supply its JSON delivery and one review paragraph.',
          precheck=phase_check, view=campaign.test_view, options=(
              Option('record', 'tested', 'Register the measured delivery and review.', campaign.record, params=(
                  Parameter('--delivery', 'Official test delivery as JSON', type=json.loads),
                  Parameter('--review', 'One paragraph reviewing the measured result'))),
          )),
    State('tested', 'The node is scored. The exit condition selects completion or another idea.',
          precheck=phase_check, view=campaign.candidate_view, options=after_test),
    State('waiting', 'Wait for client-1 to establish the measured root.', options=(
        Option('check', 'initialize', 'Check the shared baseline again.'),)),
    State('needs_setup', 'The parent must fix the failed initialization check.', final=True),
    State('finished', 'A measured solution meets the benchmark exit condition. Deliver that node through native Multica.', final=True, precheck=phase_check),
)
