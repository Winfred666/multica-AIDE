"""Durable facts shared by the installer and client SOPs."""
import json
import math
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

from . import monitor, sop_runtime

from .standalone import API, completed_node, contract, development_running, install, load_config, metadata, read, search, wake, write
from .worktrees import add_worktree, checkpoint, git, hidden_paths


def paragraph(value):
    if not value or not value.strip() or re.search(r'\n\s*\n', value.strip()):
        raise ValueError('Supply one paragraph.')
    return value.strip()


def client(context):
    issue = context['cli']('get', context['issue_id'])
    props = issue['metadata']
    workdir = Path(props.get('aide_client_workdir') or props.get('query_workdir') or props.get('git_workdir') or Path.cwd())
    progress_file = (workdir / '.aide-client.json').resolve()
    workdir = progress_file.parent
    progress = read(progress_file)
    config = load_config(progress['config_path'])
    return issue, workdir, progress, config


def observe(context):
    data = {'failures': []}
    try:
        issue, workdir, progress, config = client(context)
        benchmark = contract(config)
        for path in [config['benchmark']['public_data'], *config['benchmark']['private_paths']]:
            if not Path(path).exists():
                data['failures'].append('benchmark resource ' + str(path))
        for arg in config['benchmark']['evaluate_argv']:
            if Path(arg).is_absolute() and '{' not in arg and not Path(arg).exists():
                data['failures'].append('test script ' + arg)
        tree = search(config).snapshot()
        complete = None
        for node in tree['nodes']:
            if not all(node.get(k) for k in ('id', 'title', 'description')):
                data['failures'].append('solution node identity')
            if node['score'] is not None:
                delivery = node.get('delivery', node['score'])
                score = benchmark.score(delivery)
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    data['failures'].append('scorable delivery')
                if not node.get('artifact') or not benchmark.validate(Path(node['artifact'])):
                    data['failures'].append('solution node artifact ' + node['id'])
                result = benchmark.is_complete(delivery)
                if not isinstance(result, bool):
                    data['failures'].append('exit condition result')
                elif result:
                    complete = node
        baseline = next((n for n in tree['nodes'] if n['id'] == tree['baseline_id']), None)
        if complete:
            route = 'finished'
        elif progress.get('development_pending'):
            route = 'developing'
        elif progress.get('node_id'):
            node = next(n for n in tree['nodes'] if n['id'] == progress['node_id'])
            route = ('tested' if node['score'] is not None else
                     'review' if progress.get('test_result', {}).get('state') == 'ended' else 'test')
        elif baseline is None or baseline['score'] is None:
            route = ('test' if benchmark.validate(Path(config['template'])) else 'idea') if progress['session_id'] == 'client-1' else 'waiting'
        else:
            route = 'idea'
        data.update(route=route, tree=tree, node=complete, workdir=str(workdir), config=config, progress=progress)
    except (FileNotFoundError, KeyError, ValueError, TypeError, StopIteration, ImportError, SyntaxError) as error:
        data['failures'].append(str(error))
    if data['failures']:
        data['route'] = 'needs_setup'
    context['observation'] = data
    return data


def block_setup(args, context):
    observation = context['observation']
    issue = context['cli']('get', context['issue_id'])
    message = '; '.join(observation['failures']) + ' check is failed, failed to enter init state.'
    context['cli']('status', issue['id'], 'blocked', '--no-start', write=True)
    if issue.get('parent_issue_id'):
        parent = context['cli']('get', issue['parent_issue_id'])
        notification = '[@Installer](mention://agent/' + parent['assignee_id'] + ') ' + message
        context['cli']('comment', 'add', parent['id'], '--content', notification, write=True)
    return {'instruction': message, 'data': {'failed_checks': observation['failures']}}


def prepare_candidate(args, context):
    issue, workdir, progress, config = client(context)
    if progress.get('candidate'):
        if progress.get('node_id'):
            return run_test(args, context)
        return {}
    tree = search(config)
    journal = tree.snapshot()
    baseline = next((n for n in journal['nodes'] if n['id'] == journal['baseline_id']), None)
    if baseline is None or baseline['score'] is None:
        identity = 'baseline'
        source = progress['template_commit']
        progress.update(baseline=True, round=None)
    else:
        round_context = tree.next_context(progress['session_id'])
        identity = 'node-' + round_context['round_id']
        source = (round_context['restore_node']['metadata']['git_commit'] if round_context['mode'] == 'refine'
                  else progress['template_commit'])
        progress.update(baseline=False, round=round_context)
    base = (round_context['restore_node']['metadata']['git_branch'] if not progress['baseline']
            and round_context['mode'] == 'refine' else git(config['template'], 'branch', '--show-current'))
    # The installed client worktree is its first solution; later nodes retain their own worktrees.
    candidate = (Path(progress.get('initial_candidate', workdir)) if not progress.get('used_workdir')
                 else Path(config['workdirs']) / identity)
    if not (candidate / '.git').exists():
        add_worktree(config, candidate, 'aide-' + identity, source)
    elif not progress.get('used_workdir'):
        git(candidate, 'reset', '--hard', source)
    progress.update(candidate=str(candidate), base_branch=base, used_workdir=True)
    write(workdir / '.aide-client.json', progress)
    if candidate != workdir:
        (candidate / '.aide-client.json').symlink_to(workdir / '.aide-client.json')
    if config.get('prepare_argv'):
        subprocess.run([arg.replace('{solution}', str(candidate)) for arg in config['prepare_argv']],
                       cwd=candidate, check=True)
    if config['mode'] == 'val_dev' and issue['metadata'].get('query_workdir'):
        binding = {'git_branch': git(candidate, 'branch', '--show-current'), 'git_base_branch': base}
        binding.update(query_workdir=str(candidate), git_workdir=str(candidate))
        for key, value in binding.items():
            if issue['metadata'].get(key) != value:
                context['cli']('metadata', 'set', issue['id'], '--key', key, '--value', value, '--type', 'string', write=True)
    if progress['baseline'] and contract(config).validate(candidate):
        return describe(SimpleNamespace(description=config['benchmark'].get('baseline_description', config['benchmark']['description'])), context)
    return {}


def describe(args, context):
    try:
        description = paragraph(args.description)
        issue, workdir, progress, config = client(context)
        candidate = Path(progress['candidate'])
        if not contract(config).validate(candidate):
            return {'accepted': False, 'instruction': 'Make the candidate a valid delivery in the benchmark format before entering test.'}
        tree = search(config)
        identity = 'baseline' if progress['baseline'] else 'node-' + progress['round']['round_id']
        artifact = str(candidate)
        checkpoint_data = {'git_commit': checkpoint(candidate, description), 'git_branch': git(candidate, 'branch', '--show-current')}
        if progress['baseline']:
            tree.register_baseline_description(identity, 'Initial baseline', description,
                session_id=progress['session_id'], artifact=artifact, metadata=checkpoint_data)
        else:
            tree.register_description(identity, description.splitlines()[0][:90], description,
                session_id=progress['session_id'], round_id=progress['round']['round_id'], artifact=artifact, metadata=checkpoint_data)
        progress['node_id'] = identity
        write(workdir / '.aide-client.json', progress)
        return run_test(args, context)
    except ValueError as error:
        return {'accepted': False, 'instruction': str(error)}


def record(args, context):
    try:
        review = paragraph(args.review)
        issue, workdir, progress, config = client(context)
        report = progress['test_result']
        monitor.write(Path(progress['test_job']) / 'review.json', {'review': review, **report})
        if 'delivery' not in report:
            return {'phase': 'test_failed', 'data': {'test_result': report, 'review': review}}
        if args.delivery != report['delivery']:
            raise ValueError('Use the complete JSON recorded by this test.')
        benchmark = contract(config)
        score = benchmark.score(args.delivery)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError('The delivery needs a finite benchmark score.')
        if not isinstance(benchmark.is_complete(args.delivery), bool):
            raise ValueError('The exit condition must return a boolean for this delivery.')
        tree = search(config)
        node = next(n for n in tree.snapshot()['nodes'] if n['id'] == progress['node_id'])
        if not benchmark.validate(Path(progress['candidate'])):
            raise ValueError('The candidate no longer meets the delivery format.')
        if (git(progress['candidate'], 'rev-parse', 'HEAD') != node['metadata']['git_commit']
                or git(progress['candidate'], 'status', '--porcelain')):
            raise ValueError('The tested solution changed; preserve the measured worktree before recording its score.')
        node = tree.register_score(node['id'], args.delivery, review)
        progress['completed'] = sum(n['session_id'] == progress['session_id'] and n['kind'] != 'baseline'
                                    and n['score'] is not None for n in tree.snapshot()['nodes'])
        write(workdir / '.aide-client.json', progress)
        return {}
    except (ValueError, KeyError, TypeError) as error:
        return {'accepted': False, 'instruction': 'Supply the official JSON delivery and one review paragraph. ' + str(error)}


def next_candidate(args, context):
    issue, workdir, progress, config = client(context)
    progress.update(candidate=None, node_id=None)
    progress.pop('test_job', None)
    progress.pop('test_result', None)
    write(workdir / '.aide-client.json', progress)
    return prepare_candidate(args, context)


def finish(args, context):
    issue, workdir, progress, config = client(context)
    node = completed_node(config)
    if node is None:
        raise ValueError('No delivery meets the exit condition.')
    progress['finished'] = True
    write(workdir / '.aide-client.json', progress)
    return {'data': {'node': node}}


def candidate_view(context):
    issue, workdir, progress, config = client(context)
    data = {'candidate': progress['candidate'], 'baseline': progress['baseline'],
            'tree': search(config).snapshot(), 'round': progress.get('round')}
    data.update(mode=config['mode'],
                benchmark=config['benchmark']['description'], public_data=config['benchmark']['public_data'],
                delivery_contract=config['benchmark']['contract'])
    if config['mode'] == 'separate':
        data['developer_issue_id'] = progress['developer_issue_id']
    if progress.get('node_id'):
        data['node_id'] = progress['node_id']
    return {'data': data}


def delegate(args, context):
    description = paragraph(args.description)
    issue, workdir, progress, config = client(context)
    api = API()
    child = progress['developer_issue_id']
    runs = api.request('GET', '/api/issues/' + child + '/task-runs')
    if development_running(runs):
        return {'accepted': False, 'instruction': 'Wait for the previous developer run to finish.'}
    policy = {'deny_paths': hidden_paths(progress['sandbox_policy']['deny_paths']),
              'read_only_paths': [str(workdir / '.aide-development.json'),
              str(workdir / '.aide-client.json'), str(Path(progress['candidate']) / '.agents')]}
    command = {'candidate': progress['candidate'], 'policy': policy}
    write(workdir / '.aide-development.json', command)
    metadata(api, child, {'query_workdir': progress['candidate'], 'git_workdir': progress['candidate'],
                          'git_branch': git(progress['candidate'], 'branch', '--show-current'),
                          'git_base_branch': progress['base_branch'],
                          'sandbox_policy': json.dumps(policy)})
    progress.update(development_pending=True, idea_description=description)
    write(workdir / '.aide-client.json', progress)
    wake(api, {'issue_id': child, 'agent_id': progress['developer_agent_id']},
         f"{description} Work in {progress['candidate']}. "
         f"Use the bubblewrap command in this issue's description for all development commands. "
         'Deliver the candidate for parent validation; leave the evaluator and private data to the parent.')
    return {'phase': 'developing', 'data': {'developer_issue_id': child}}


def development(args, context):
    issue, workdir, progress, config = client(context)
    api = API()
    child = api.request('GET', '/api/issues/' + progress['developer_issue_id'])
    if child['status'] in {'blocked', 'cancelled'}:
        return {'phase': 'needs_setup', 'data': {'developer_issue_id': child['id'], 'status': child['status']}}
    runs = api.request('GET', '/api/issues/' + child['id'] + '/task-runs')
    if child['status'] not in {'in_review', 'done'} or development_running(runs):
        return {'phase': 'developing', 'instruction': 'Await the native child delivery; recall continue after its run and cleanup finish.'}
    if not contract(config).validate(Path(progress['candidate'])):
        return {'accepted': False, 'instruction': 'The developer delivery does not meet the benchmark format; request a correction.'}
    api.request('PUT', '/api/issues/' + child['id'], {'status': 'done', 'suppress_run': True})
    progress['development_pending'] = False
    write(workdir / '.aide-client.json', progress)
    return describe(SimpleNamespace(description=progress['idea_description']), context)


def test_view(context):
    view = candidate_view(context)
    issue, workdir, progress, config = client(context)
    import shlex
    view['data']['test_command'] = shlex.join([arg.replace('{solution}', progress['candidate'])
                                             for arg in config['benchmark']['evaluate_argv']])
    view['data']['test_result'] = progress.get('test_result')
    return view


def run_test(args, context):
    issue, workdir, progress, config = client(context)
    if not progress.get('test_job'):
        argv = [arg.replace('{solution}', progress['candidate']) for arg in config['benchmark']['evaluate_argv']]
        progress['test_job'] = monitor.start(argv, progress['candidate'], workdir / 'logs')
        write(workdir / '.aide-client.json', progress)
    return {'phase': 'test', 'data': {'test_job': progress['test_job']}}


def monitor_test(args, context):
    issue, workdir, progress, config = client(context)
    report = monitor.wait(progress['test_job'], context['state'].setdefault('monitor', {}))
    sop_runtime.save_state(context, context['state'])
    if report['state'] == 'ended' and report['returncode'] == 0 and not report.get('exception'):
        try:
            delivery = monitor.delivery(progress['test_job'])
            score = contract(config).score(delivery)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError('The test must record a finite benchmark score.')
            if not isinstance(contract(config).is_complete(delivery), bool):
                raise ValueError('The exit condition must return a boolean.')
            report['delivery'] = delivery
        except (ValueError, KeyError, TypeError) as error:
            report['error'] = str(error)
    progress['test_result'] = report
    write(workdir / '.aide-client.json', progress)
    return {'phase': 'review' if report['state'] == 'ended' else 'test', 'data': report}


def exception_test(args, context):
    issue, workdir, progress, config = client(context)
    monitor.stop(progress['test_job'])
    return monitor_test(args, context)


def installer_config():
    return load_config(read(Path.cwd() / '.aide-install.json')['config_path'])


def prepare_install(args, context):
    config = load_config(args.settings)
    result = install(config, context['issue_id'])
    if result['status'] != 'ready':
        return {'accepted': False, **result}
    write(Path.cwd() / '.aide-install.json', {'config_path': config['config_path']})
    return {'data': {'config': config}}


def confirm_install(args, context):
    return {'data': install(installer_config(), context['issue_id'], confirm=True)}


def start_install(args, context):
    from .standalone import begin
    return {'data': begin(installer_config())}
