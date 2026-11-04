"""Durable facts shared by the installer and client SOPs."""
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from .standalone import API, LAUNCHER, checkout, completed_node, contract, install, load_config, read, search, snapshot, write


def paragraph(value):
    if not value or not value.strip() or re.search(r'\n\s*\n', value.strip()):
        raise ValueError('Supply one paragraph.')
    return value.strip()


def client(context):
    issue = context['cli']('get', context['issue_id'])
    workdir = Path(issue['metadata']['query_workdir'])
    progress = read(workdir / '.aide-client.json')
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
        repository = config['benchmark'].get('repository')
        if repository and not benchmark.validate(Path(repository)):
            data['failures'].append('baseline delivery format')
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
        elif progress.get('node_id'):
            node = next(n for n in tree['nodes'] if n['id'] == progress['node_id'])
            route = 'tested' if node['score'] is not None else 'test'
        elif baseline is None or baseline['score'] is None:
            route = ('test' if repository else 'idea') if progress['session_id'] == 'client-1' else 'waiting'
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
        context['cli']('comment', 'add', issue['parent_issue_id'], '--content', message, write=True)
    return {'instruction': message, 'data': {'failed_checks': observation['failures']}}


def prepare_candidate(args, context):
    issue, workdir, progress, config = client(context)
    if progress.get('candidate'):
        return {'data': {'candidate': progress['candidate']}}
    tree = search(config)
    journal = tree.snapshot()
    baseline = next((n for n in journal['nodes'] if n['id'] == journal['baseline_id']), None)
    if baseline is None or baseline['score'] is None:
        candidate = workdir / 'candidate-baseline'
        if not candidate.exists():
            source = baseline['artifact'] if baseline and Path(baseline['artifact']).exists() else config['benchmark'].get('repository')
            checkout(source, candidate)
        progress.update(candidate=str(candidate), baseline=True, round=None)
        if config['benchmark'].get('repository') or baseline:
            artifact = Path(config['state']) / 'artifacts' / 'baseline'
            tree.register_baseline_description('baseline', 'Initial baseline',
                config['benchmark'].get('baseline_description', config['benchmark']['description']),
                session_id=progress['session_id'], artifact=str(artifact))
            progress['node_id'] = 'baseline'
    else:
        round_context = tree.next_context(progress['session_id'])
        candidate = workdir / ('candidate-' + round_context['round_id'])
        if not candidate.exists():
            checkout(round_context['restore_node']['artifact'], candidate)
        progress.update(candidate=str(candidate), baseline=False, round=round_context)
    write(workdir / '.aide-client.json', progress)
    return {'data': {'candidate': str(candidate)}}


def describe(args, context):
    try:
        description = paragraph(args.description)
        issue, workdir, progress, config = client(context)
        candidate = Path(progress['candidate'])
        if not contract(config).validate(candidate):
            return {'accepted': False, 'instruction': 'Make the candidate a valid delivery in the benchmark format before entering test.'}
        tree = search(config)
        identity = 'baseline' if progress['baseline'] else 'node-' + progress['round']['round_id']
        artifact = str(Path(config['state']) / 'artifacts' / identity)
        if progress['baseline']:
            tree.register_baseline_description(identity, 'Initial baseline', description,
                session_id=progress['session_id'], artifact=artifact)
        else:
            tree.register_description(identity, description.splitlines()[0][:90], description,
                session_id=progress['session_id'], round_id=progress['round']['round_id'], artifact=artifact)
        progress['node_id'] = identity
        write(workdir / '.aide-client.json', progress)
        return {'data': {'node_id': identity}}
    except ValueError as error:
        return {'accepted': False, 'instruction': str(error)}


def record(args, context):
    try:
        review = paragraph(args.review)
        issue, workdir, progress, config = client(context)
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
        if not Path(node['artifact']).exists():
            snapshot(progress['candidate'], node['artifact'])
        node = tree.register_score(node['id'], args.delivery, review)
        progress['completed'] = sum(n['session_id'] == progress['session_id'] and n['kind'] != 'baseline'
                                    and n['score'] is not None for n in tree.snapshot()['nodes'])
        write(workdir / '.aide-client.json', progress)
        return {'data': {'node': node}}
    except (ValueError, KeyError, TypeError) as error:
        return {'accepted': False, 'instruction': 'Supply the official JSON delivery and one review paragraph. ' + str(error)}


def next_candidate(args, context):
    issue, workdir, progress, config = client(context)
    progress.update(candidate=None, node_id=None)
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
    data = {'candidate': progress['candidate'], 'tree': search(config).snapshot(), 'round': progress.get('round')}
    installation = read(Path(config['state']) / 'installation.json')
    data.update(solver_id=installation['solver_id'], project_id=config['multica']['project_id'],
                benchmark=config['benchmark']['description'], public_data=config['benchmark']['public_data'],
                delivery_contract=config['benchmark']['contract'])
    if progress.get('node_id'):
        data['node_id'] = progress['node_id']
    return {'data': data}


def test_view(context):
    view = candidate_view(context)
    issue, workdir, progress, config = client(context)
    import shlex
    view['data']['test_command'] = shlex.join([arg.replace('{solution}', progress['candidate'])
                                             for arg in config['benchmark']['evaluate_argv']])
    return view


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
    config = installer_config()
    path = Path(config['state'])
    with (path / 'controller.log').open('a') as output:
        process = subprocess.Popen([sys.executable, str(LAUNCHER), '--config', config['config_path'], 'begin'],
                                   stdout=output, stderr=output, start_new_session=True)
    return {'data': {'controller_pid': process.pid, 'log': str(path / 'controller.log'), 'port': config.get('port')}}
