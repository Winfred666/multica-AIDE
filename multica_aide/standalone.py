"""Run a shared AIDE tree with native Multica query clients and a host evaluator."""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

from . import SearchTree

ACTIVE = {'queued', 'dispatched', 'running', 'deferred', 'waiting_local_directory'}
LAUNCHER = Path(__file__).resolve().with_name('__main__.py')
CLIENT_PROTOCOL = 'Follow the current round context; register the node description, then request host scoring.'


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


class API:
    """Use the installing native task's workspace and credentials."""
    def __init__(self):
        self.base = os.environ['MULTICA_SERVER_URL'].rstrip('/')
        self.workspace = os.environ['MULTICA_WORKSPACE_ID']
        self.headers = {'Authorization': 'Bearer ' + os.environ['MULTICA_TOKEN'],
                        'X-Workspace-ID': self.workspace, 'Content-Type': 'application/json'}

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(self.base + path, data=data, headers=self.headers, method=method), timeout=30) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except HTTPError as error:
            detail = error.read().decode(errors='replace').replace(os.environ['MULTICA_TOKEN'], '[REDACTED]')
            raise RuntimeError(f'{method} {path}: HTTP {error.code}: {detail}') from None


def load_config(path):
    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('config.yaml must contain a mapping')
    for field in ('drafts', 'clients'):
        if type(config.get(field)) is not int or config[field] < 1:
            raise ValueError(field + ' must be a positive integer')
    for field, default in (('solver_model', 'gpt-6.1-terra'), ('cli_babysitter_model', 'gpt-6-luna')):
        config.setdefault(field, default)
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError(field + ' must name an available runtime model')
    if config.get('port') is not None and (type(config['port']) is not int or not 0 < config['port'] < 65536):
        raise ValueError('port must be null or a TCP port')
    for field in ('tree', 'state', 'workdirs'):
        config[field] = str((path.parent / config[field]).resolve())
    if config.get('knowledge_base'):
        config['knowledge_base'] = str((path.parent / config['knowledge_base']).resolve())
    bench = config['benchmark']
    for field in ('repository', 'public_data'):
        bench[field] = str((path.parent / bench[field]).resolve())
    bench['private_paths'] = [str((path.parent / p).resolve()) for p in bench['private_paths']]
    if not bench['private_paths'] or any(p == '/' for p in bench['private_paths']):
        raise ValueError('benchmark.private_paths must list the hidden evaluator and data')
    for field in ('evaluate_argv',):
        if not isinstance(bench[field], list) or not bench[field] or not all(isinstance(a, str) for a in bench[field]):
            raise ValueError('benchmark.' + field + ' must be a command argument array')
    if not any('{solution}' in arg for arg in bench['evaluate_argv']):
        raise ValueError('evaluate_argv must pass {solution} to the evaluator')
    sandbox = config['sandbox']
    if not sandbox.get('profile') or not sandbox.get('probe_argv'):
        raise ValueError('sandbox needs an existing enforcing runtime profile and actual-runtime probe_argv')
    config['config_path'] = str(path)
    return config


def search(config):
    return SearchTree(config['tree'], drafts=config['drafts'], knowledge_base=config.get('knowledge_base'),
                      maximize=config.get('maximize', True))


def evaluate(config, solution):
    bench = config['benchmark']
    argv = [arg.replace('{solution}', str(solution)) for arg in bench['evaluate_argv']]
    result = subprocess.run(argv, cwd=solution, text=True, capture_output=True, check=True,
                            timeout=bench.get('timeout_seconds', 3600))
    measured = json.loads(result.stdout)
    score = measured.get('score')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError('the host evaluator must return JSON with a finite numeric score')
    return measured


def snapshot(source, destination):
    """Freeze a solution without copying a worktree's Git control file."""
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', '.aide-submission.json'))
    return str(destination)


def checkout(source, destination):
    snapshot(source, destination)
    for argv in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'],
                 ['git', '-c', 'user.name=Multica', '-c', 'user.email=multica@localhost',
                  'commit', '-q', '--allow-empty', '-m', 'Benchmark solution']):
        subprocess.run(argv, cwd=destination, check=True, capture_output=True)


def policy(config):
    read_only = [config['benchmark']['public_data'], str(LAUNCHER.parent), config['config_path']]
    if config.get('knowledge_base'):
        read_only.append(config['knowledge_base'])
    return json.dumps({'deny_paths': config['benchmark']['private_paths'] + [config['state'], config['tree']],
                       'read_only_paths': read_only},
                      separators=(',', ':'))


def probe(config):
    result = subprocess.run([*config['sandbox']['probe_argv'], '--profile', config['sandbox']['profile'],
                             '--runtime-id', config['multica']['runtime_id'],
                             '--policy', policy(config)], text=True, capture_output=True, check=True, timeout=60)
    observed = json.loads(result.stdout)
    if (observed.get('runtime_id') != config['multica']['runtime_id'] or observed.get('private_data_visible') is not False
            or observed.get('public_data_readable') is not True or observed.get('solution_writable') is not True):
        raise ValueError('the selected runtime probe must establish private_data_visible: false, '
                         'public_data_readable: true and solution_writable: true for its runtime_id')
    return observed


def label(api, name):
    labels = api.request('GET', '/api/labels?resource_type=issue')['labels']
    row = next((row for row in labels if row['name'] == name), None)
    return (row or api.request('POST', '/api/labels', {'name': name, 'color': '#6b7280', 'resource_type': 'issue'}))['id']


def metadata(api, issue, values):
    for key, value in values.items():
        api.request('PUT', '/api/issues/' + issue + '/metadata/' + key, {'value': value})


def install(config, parent_issue, confirm=False):
    """Report preparation needs, preview settings and install the round protocol."""
    command = [sys.executable, str(LAUNCHER), '--config', config['config_path']]
    repeat = [*command, 'install']
    if parent_issue:
        repeat.extend(['--parent-issue', parent_issue])
    state_file = Path(config['state']) / 'installation.json'
    if confirm and state_file.exists():
        saved = read(state_file)
        model_fields = {'solver_model', 'cli_babysitter_model'}
        fixed = lambda settings: {key: value for key, value in settings.items() if key not in model_fields}
        if fixed(saved['config']) != fixed(config) or saved['parent_issue_id'] != parent_issue:
            raise ValueError('installation settings changed; use a new state/tree/workdirs directory')
        if saved['status'] != 'installed':
            raise ValueError('installation interrupted; inspect saved resource IDs before retrying')
        api = API()
        api.request('PUT', '/api/agents/' + saved['solver_id'], {'model': config['solver_model'], 'instructions': ''})
        for client in saved['clients']:
            api.request('PUT', '/api/agents/' + client['agent_id'], {'model': config['cli_babysitter_model'], 'instructions': CLIENT_PROTOCOL})
        saved['config'] = config
        write(state_file, saved)
        return {**saved, 'next_command': shlex.join([*command, 'begin'])}
    paths = [config['benchmark']['repository'], config['benchmark']['public_data'], *config['benchmark']['private_paths']]
    if config.get('knowledge_base'):
        paths.append(config['knowledge_base'])
    for argv in (config['benchmark']['evaluate_argv'], config['sandbox']['probe_argv']):
        paths.extend(arg for arg in argv if Path(arg).is_absolute() and '{' not in arg)
    missing = [path for path in dict.fromkeys(paths) if not Path(path).exists()]
    if missing:
        return {'status': 'preparation_required',
                'next_action': 'Prepare the missing benchmark and environment resources using '
                               'AGENTS.md#native-installation, reuse settings already confirmed, then run after_preparation_command.',
                'missing_paths': missing, 'after_preparation_command': shlex.join(repeat), 'config': config}
    if not confirm:
        return {'status': 'preparation_required',
                'next_action': 'As the installing issue, read the evaluator implementation invoked by '
                               + shlex.join(config['benchmark']['evaluate_argv']) + '. '
                               'benchmark.private_paths defines worker isolation; installer reads follow the installing issue policy. '
                               'Ensure solution execution and submission generation use worker private-data denial '
                               'before the host evaluator grades submitted artifacts with private data. '
                               'Complete this preparation, reuse confirmed settings and confirm any remaining settings '
                               'before running after_preparation_command.',
                'after_preparation_command': shlex.join([*repeat, '--confirm-config']), 'config': config}
    if Path(config['benchmark']['repository']).is_relative_to(config['state']):
        raise ValueError('benchmark repository must be outside the private controller state')
    repository = Path(config['benchmark']['repository'])
    for storage in (config['state'], config['tree'], config['workdirs']):
        if Path(storage).is_relative_to(repository):
            raise ValueError('controller storage must be outside the baseline solution repository: ' + storage)
    if any(Path(path).is_relative_to(repository) for path in config['benchmark']['private_paths']):
        raise ValueError('hidden data and evaluator must be outside the baseline solution repository')
    api = API()
    isolation = probe(config)
    parent = api.request('GET', '/api/issues/' + parent_issue)
    if parent['workspace_id'] != api.workspace:
        raise ValueError('parent issue belongs to another workspace')
    if parent.get('project_id') != config['multica']['project_id']:
        raise ValueError('parent issue must belong to the configured benchmark project')
    baseline = Path(config['state']) / 'artifacts' / 'baseline'
    if not baseline.exists():
        snapshot(config['benchmark']['repository'], baseline)
    measured = measured_artifact(config, baseline)
    search(config).register_baseline('baseline', 'Initial baseline', config['benchmark']['description'],
                                     measured['score'], artifact=str(baseline), review=measured.get('review'))
    saved = {'status': 'installing', 'config': config, 'workspace_id': api.workspace, 'server_url': api.base,
             'parent_issue_id': parent_issue, 'clients': [], 'sandbox_probe': isolation}
    write(state_file, saved)
    query = label(api, 'query')
    solver = api.request('POST', '/api/agents', {
        'name': 'Solution developer', 'description': 'Benchmark solver for issue ' + parent_issue,
        'runtime_id': config['multica']['runtime_id'], 'model': config['solver_model'],
        'thinking_level': config['multica'].get('thinking_level', 'high'),
        'visibility': 'workspace', 'max_concurrent_tasks': config['clients']})
    saved['solver_id'] = solver['id']
    write(state_file, saved)
    for n in range(config['clients']):
        session = 'client-' + str(n + 1)
        agent = api.request('POST', '/api/agents', {
            'name': 'Improvement client ' + str(n + 1), 'description': 'Optimization client for issue ' + parent_issue,
            'runtime_id': config['multica']['runtime_id'], 'model': config['cli_babysitter_model'],
            'thinking_level': 'low', 'visibility': 'workspace', 'max_concurrent_tasks': 1,
            'instructions': CLIENT_PROTOCOL})
        row = {'session_id': session, 'agent_id': agent['id'], 'completed': 0}
        saved['clients'].append(row)
        write(state_file, saved)
        issue = api.request('POST', '/api/issues', {'title': 'Improvement client ' + str(n + 1),
            'description': 'Shared optimization tree; follow the current round thread.',
            'project_id': config['multica']['project_id'], 'parent_issue_id': parent_issue, 'status': 'backlog'})
        row['issue_id'] = issue['id']
        write(state_file, saved)
        api.request('POST', '/api/issues/' + issue['id'] + '/labels', {'label_id': query})
        metadata(api, issue['id'], {'sandbox_policy': policy(config),
                                    'aide_runtime_id': config['multica']['runtime_id']})
        api.request('PUT', '/api/issues/' + issue['id'], {'assignee_type': 'agent', 'assignee_id': agent['id'], 'suppress_run': True})
    saved['status'] = 'installed'
    write(state_file, saved)
    return {**saved, 'next_command': shlex.join([*command, 'begin'])}


def wake(api, client, content, marker, parent=None):
    path = '/api/issues/' + client['issue_id']
    body = {'content': marker + '\n\n' + content, 'suppress_agent_ids': [client['agent_id']]}
    if parent:
        body['parent_id'] = parent
    comment = api.request('POST', path + '/comments', body)
    thread = parent or comment['id']
    api.request('PUT', path, {'status': 'in_progress', 'suppress_run': True})
    api.request('POST', path + '/wakeups', {'agent_id': client['agent_id'], 'kind': 'at', 'mode': 'once',
        'after_seconds': 1, 'parent_comment_id': thread, 'instruction': 'Continue the work in comment ' + comment['id'] + '.',
        'idempotency_key': marker})
    return thread


def owned_work(api, issue_id):
    """Return active tasks; unresolved native failures stop the controller."""
    pending, active = [issue_id], []
    while pending:
        identity = pending.pop()
        issue = api.request('GET', '/api/issues/' + identity)
        if issue['status'] == 'blocked' or (identity == issue_id and issue['status'] == 'cancelled'):
            raise RuntimeError('Native issue ' + identity + ' is ' + issue['status'])
        tasks = api.request('GET', '/api/issues/' + identity + '/task-runs')
        running = [t for t in tasks if t['status'] in ACTIVE or (t.get('result') or {}).get('process_cleanup_pending')]
        active.extend({**task, 'issue_id': identity} for task in running)
        latest = max(tasks, key=lambda t: (t.get('created_at') or '', t['id']), default=None)
        if not running and latest and latest['status'] in {'failed', 'cancelled'}:
            if identity == issue_id or issue['status'] not in {'done', 'cancelled'}:
                raise RuntimeError(f"Native task {latest['id']} on issue {identity} {latest['status']}: "
                                   + str(latest.get('error') or ''))
        pending.extend(child['id'] for child in api.request('GET', '/api/issues/' + identity + '/children')['issues'])
    return active


def measured_artifact(config, artifact):
    """Evaluate the frozen artifact once, retaining its official result for handoff retries."""
    result_file = artifact.with_name(artifact.name + '.score.json')
    if result_file.exists():
        return read(result_file)
    measured = evaluate(config, artifact)
    write(result_file, measured)
    return measured


def register_proposal(config, client):
    """Publish the client's description while its official score is pending."""
    round_info = client['round']
    workdir = Path(round_info['workdir'])
    spec = read(workdir / '.aide-submission.json')
    if spec['round_id'] != round_info['round_id']:
        raise ValueError('submission belongs to a different client round')
    artifact = Path(config['state']) / 'artifacts' / round_info['round_id']
    return search(config).register_description(spec['id'], spec['title'], spec['description'],
        session_id=client['session_id'], round_id=round_info['round_id'],
        parent_id=round_info['parent_id'], artifact=str(artifact), put_forward_at=spec.get('put_forward_at'))


def register_submission(config, client):
    node = register_proposal(config, client)
    if node['score'] is not None:
        return node
    workdir = Path(client['round']['workdir'])
    spec = read(workdir / '.aide-submission.json')
    if not spec.get('score_requested'):
        raise ValueError('request host scoring before evaluating the registered description')
    artifact = Path(node['artifact'])
    if not artifact.exists():
        snapshot(workdir, artifact)
    measured = measured_artifact(config, artifact)
    return search(config).register_score(node['id'], measured['score'],
                                         review=measured.get('review', spec.get('review')))


def begin(config, max_rounds=0):
    """Poll registrations and scores, opening a new client thread each round."""
    state_file = Path(config['state']) / 'installation.json'
    saved = read(state_file)
    api = API()
    if saved['config'] != config or saved['workspace_id'] != api.workspace or saved['server_url'] != api.base:
        raise ValueError('run with the installed settings and native workspace')
    if saved['status'] != 'installed':
        raise ValueError('finish the saved installation before beginning client rounds')
    probe(config)
    viewer = None
    if config.get('port'):
        viewer = subprocess.Popen([sys.executable, str(LAUNCHER), '--config', config['config_path'], 'serve'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while True:
            if viewer and viewer.poll() is not None:
                raise RuntimeError('tree viewer exited; fix its configured port before resuming')
            for client in saved['clients']:
                if client.get('round') and (Path(client['round']['workdir']) / '.aide-submission.json').exists():
                    register_proposal(config, client)
                if owned_work(api, client['issue_id']):
                    continue
                if client.get('round'):
                    round_info = client['round']
                    workdir = Path(round_info['workdir'])
                    submission = workdir / '.aide-submission.json'
                    if not submission.exists():
                        client['reminders'] = client.get('reminders', 0) + 1
                        wake(api, client, 'Register the current node description, then request host scoring using the supplied commands.',
                             round_info['round_id'] + '-submit-' + str(client['reminders']), round_info['thread_id'])
                        write(state_file, saved)
                        continue
                    if not read(submission).get('score_requested'):
                        client['reminders'] = client.get('reminders', 0) + 1
                        wake(api, client, 'Request host scoring for the registered node using the supplied score command.',
                             round_info['round_id'] + '-score-' + str(client['reminders']), round_info['thread_id'])
                        write(state_file, saved)
                        continue
                    node = register_submission(config, client)
                    api.request('POST', '/api/issues/' + client['issue_id'] + '/comments', {
                        'content': f"Registered {node['id']}; score {node['score']}.", 'parent_id': round_info['thread_id'],
                        'suppress_agent_ids': [client['agent_id']]})
                    api.request('POST', '/api/comments/' + round_info['thread_id'] + '/resolve', {})
                    client['completed'] += 1
                    client.pop('round')
                    write(state_file, saved)
                if max_rounds and client['completed'] >= max_rounds:
                    continue
                context = search(config).next_context(client['session_id'])
                workdir = Path(config['workdirs']) / client['session_id'] / context['round_id']
                checkout(context['restore_node']['artifact'], workdir)
                metadata(api, client['issue_id'], {'query_workdir': str(workdir), 'aide_round_id': context['round_id']})
                quoted = shlex.join([sys.executable, str(LAUNCHER)])
                prompt = (config['benchmark']['description'] + '\n\n' + json.dumps(context, indent=2) +
                    '\nSolution repository: ' + str(workdir) + '\nPublic data: ' + config['benchmark']['public_data'] +
                    '\nNative work-issue endpoint: ' + quoted + ' dev --agent-id ' + shlex.quote(saved['solver_id']) +
                    ' --title TITLE --description-file FILE\nNode description endpoint: ' +
                    quoted + ' register --id ID --title TITLE --description DESCRIPTION\nHost scoring endpoint: ' +
                    quoted + ' score --id ID [--review REVIEW]\n'
                    'End after requesting scoring. The host registers the official score and opens your next round.')
                round_info = {key: context[key] for key in ('round_id', 'parent_id')}
                round_info.update(workdir=str(workdir), thread_id=wake(api, client, prompt, context['round_id']))
                client['round'] = round_info
                write(state_file, saved)
            if max_rounds and all(c['completed'] >= max_rounds for c in saved['clients']):
                return {'status': 'completed', 'clients': saved['clients']}
            time.sleep(config.get('poll_seconds', 10))
    finally:
        if viewer:
            viewer.terminate()
            viewer.wait(timeout=10)


def client_issue(api):
    issue = api.request('GET', '/api/issues/' + os.environ['MULTICA_ISSUE_ID'])
    inherited = issue.get('metadata') or {}
    if not inherited.get('query_workdir') or not inherited.get('aide_round_id'):
        raise ValueError('submission belongs to an active improvement client query issue')
    return issue, inherited


def describe(identity, title, description):
    """Request a pending description without ending the native client round."""
    value = {'id': identity, 'title': title, 'description': description, 'score_requested': False}
    if not all(isinstance(value[k], str) and value[k].strip() for k in ('id', 'title', 'description')):
        raise ValueError('submission needs id, title and description')
    api = API()
    issue, inherited = client_issue(api)
    value['round_id'] = inherited['aide_round_id']
    path = Path(inherited['query_workdir']) / '.aide-submission.json'
    if path.exists():
        previous = read(path)
        if previous.get('round_id') == value['round_id']:
            if any(previous.get(key) != value[key] for key in ('id', 'title', 'description')):
                raise ValueError('the round description is already submitted with different content')
            return {'status': 'description_submitted', 'id': identity}
    value['put_forward_at'] = datetime.now(timezone.utc).isoformat()
    write(path, value)
    return {'status': 'description_submitted', 'id': identity}


def request_score(identity, review=None):
    """Finish the native round and request authoritative host evaluation."""
    api = API()
    issue, inherited = client_issue(api)
    path = Path(inherited['query_workdir']) / '.aide-submission.json'
    value = read(path)
    if value['id'] != identity or value['round_id'] != inherited['aide_round_id']:
        raise ValueError('score request must identify the registered node in this round')
    value.update(score_requested=True, review=review)
    write(path, value)
    api.request('PUT', '/api/issues/' + issue['id'], {'status': 'done', 'suppress_run': True})
    return {'status': 'submitted', 'next_action': 'End this round; the host will attach its official score and review.'}


def submit(identity, title, description, review=None):
    describe(identity, title, description)
    return request_score(identity, review)


def native_client():
    """Identify a query worker whose registrations must go through its host."""
    issue_id = os.environ.get('MULTICA_ISSUE_ID')
    if not issue_id:
        return False
    issue = API().request('GET', '/api/issues/' + issue_id)
    inherited = issue.get('metadata') or {}
    return bool(inherited.get('query_workdir') and inherited.get('aide_round_id'))


def create_dev(agent_id, title, description_file):
    """Create a code child on the same isolated runtime as its query client."""
    api = API()
    parent_id = os.environ['MULTICA_ISSUE_ID']
    parent = api.request('GET', '/api/issues/' + parent_id)
    inherited = parent.get('metadata') or {}
    workdir = inherited['query_workdir']
    if not inherited.get('sandbox_policy') or not inherited.get('aide_runtime_id'):
        raise ValueError('optimization client is missing its sandbox policy')
    agent = api.request('GET', '/api/agents/' + agent_id)
    if agent['runtime_id'] != inherited['aide_runtime_id']:
        raise ValueError('development children must use the configured isolated runtime')
    description = 'Work in ' + workdir + '; cd there before shell commands.\n\n' + Path(description_file).read_text()
    issue = api.request('POST', '/api/issues', {'title': title, 'description': description,
        'project_id': parent['project_id'], 'parent_issue_id': parent_id, 'status': 'backlog'})
    api.request('POST', '/api/issues/' + issue['id'] + '/labels', {'label_id': label(api, 'dev')})
    metadata(api, issue['id'], {'sandbox_policy': inherited['sandbox_policy'],
        'aide_runtime_id': inherited['aide_runtime_id'], 'git_repo_root': workdir,
        'git_workdir': workdir, 'git_branch': 'main', 'git_owner_issue_id': issue['id']})
    api.request('PUT', '/api/issues/' + issue['id'], {'assignee_type': 'agent', 'assignee_id': agent_id, 'status': 'in_progress'})
    return {'issue_id': issue['id'], 'workdir': workdir}
