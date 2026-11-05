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
    if phase in {'idea', 'test', 'review', 'tested'} and not Path(progress['candidate']).is_dir():
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


def after_test(context):
    issue, workdir, progress, config = campaign.client(context)
    complete = completed_node(config)
    return (Option('continue', 'finished' if complete else 'idea',
                   'Finish with the passing node, or prepare the next candidate.',
                   campaign.finish if complete else campaign.next_candidate),)


def review_options(context):
    issue, workdir, progress, config = campaign.client(context)
    delivery = 'delivery' in progress['test_result']
    return (Option('record', 'tested', 'Register the recorded JSON delivery and one review paragraph; report an unscored test failure through native Multica.',
                   campaign.record, params=(
                       Parameter('--delivery', 'Complete JSON delivery recorded by this test', type=json.loads, required=delivery),
                       Parameter('--review', 'One paragraph reviewing the measured result'))),)


workflow = Workflow(
    State('initialize', 'Check the benchmark, baseline, solution tree and delivery exit condition.',
          initial=True, precheck=initialize, options=entry,
          view=lambda context: {'data': context['initial']}),
    State('idea', 'Follow the supplied development issue through native review and acceptance into the candidate. Describe the solution idea in one paragraph before testing.',
          precheck=phase_check, view=campaign.candidate_view, options=(
              Option('test', 'test', 'Register the idea description and enter test.', campaign.describe,
                     params=(Parameter('--description', 'One paragraph describing this solution idea'),)),
          )),
    State('test', 'Read the collected test progress. Choose and invoke continue or exception in this turn.',
          precheck=phase_check, view=campaign.test_view, options=(
              Option('continue', 'test', 'Monitor until an important log, time checkpoint or test exit.', campaign.monitor_test),
              Option('exception', 'review', 'End this test and review its recorded result.', campaign.exception_test),
          )),
    State('review', 'Supply the official JSON delivery and one review paragraph. An interrupted or invalid test remains unscored.',
          precheck=phase_check, view=campaign.test_view, options=review_options),
    State('tested', 'The node is scored. The exit condition selects completion or another idea.',
          precheck=phase_check, view=campaign.candidate_view, options=after_test),
    State('waiting', 'Wait for client-1 to establish the measured root.', options=(
        Option('check', 'initialize', 'Check the shared baseline again.'),)),
    State('needs_setup', 'The parent must fix the failed initialization check.', final=True),
    State('test_failed', 'The test has no scorable delivery. Report the recorded review and logs through native Multica block.', final=True),
    State('finished', 'A measured solution meets the benchmark exit condition. Deliver that node through native Multica.', final=True, precheck=phase_check),
)
