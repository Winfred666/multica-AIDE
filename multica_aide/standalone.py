"""Install and host native Multica optimization clients."""
import importlib.util
import json
import os
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

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = Path(__file__).with_name('__main__.py')
SKILLS = Path(__file__).with_name('skills')
CLIENT_PROTOCOL = 'Use multica-aide-cli for this issue. Follow its returned option until the SOP finishes.'


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


class API:
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
    for key in ('drafts', 'clients'):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(key + ' must be a positive integer')
    config.setdefault('solver_model', 'gpt-6.1-terra')
    config.setdefault('cli_babysitter_model', 'gpt-6-luna')
    if config.get('port') is not None and (type(config['port']) is not int or not 0 < config['port'] < 65536):
        raise ValueError('port must be null or a TCP port')
    for field in ('tree', 'state', 'workdirs'):
        config[field] = str((path.parent / config[field]).resolve())
    if config.get('knowledge_base'):
        config['knowledge_base'] = str((path.parent / config['knowledge_base']).resolve())
    bench = config['benchmark']
    for field in ('repository', 'public_data', 'contract'):
        if bench.get(field):
            bench[field] = str((path.parent / bench[field]).resolve())
    bench['private_paths'] = [str((path.parent / p).resolve()) for p in bench.get('private_paths', [])]
    argv = bench['evaluate_argv']
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or not any('{solution}' in a for a in argv):
        raise ValueError('benchmark.evaluate_argv must pass {solution} to the official evaluator')
    config['config_path'] = str(path)
    return config


def contract(config):
    path = Path(config['benchmark']['contract'])
    spec = importlib.util.spec_from_file_location('aide_benchmark_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('validate', 'score', 'is_complete'):
        if not callable(getattr(module, name, None)):
            raise ValueError('benchmark contract requires ' + name)
    return module


def search(config):
    score_function = contract(config).score if config.get('benchmark', {}).get('contract') else None
    return SearchTree(config['tree'], drafts=config['drafts'], knowledge_base=config.get('knowledge_base'),
                      maximize=config.get('maximize', True), score_function=score_function)


def evaluate(config, solution):
    argv = [arg.replace('{solution}', str(solution)) for arg in config['benchmark']['evaluate_argv']]
    result = subprocess.run(argv, cwd=solution, text=True, capture_output=True, check=True,
                            timeout=config['benchmark'].get('timeout_seconds', 3600))
    return json.loads(result.stdout)


def snapshot(source, destination):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', '.pi', '.multica'))
    return str(destination)


def checkout(source, destination):
    if source:
        snapshot(source, destination)
    else:
        Path(destination).mkdir(parents=True)
    for argv in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'],
                 ['git', '-c', 'user.name=Multica', '-c', 'user.email=multica@localhost',
                  'commit', '-q', '--allow-empty', '-m', 'Benchmark solution']):
        subprocess.run(argv, cwd=destination, check=True, capture_output=True)


def label(api, name):
    labels = api.request('GET', '/api/labels?resource_type=issue')['labels']
    row = next((row for row in labels if row['name'] == name), None)
    return (row or api.request('POST', '/api/labels', {'name': name, 'color': '#6b7280', 'resource_type': 'issue'}))['id']


def metadata(api, issue, values):
    for key, value in values.items():
        api.request('PUT', '/api/issues/' + issue + '/metadata/' + key, {'value': value})


def cli_skill(api):
    folder = SKILLS / 'multica-aide-cli'
    body = {'name': folder.name, 'description': CLIENT_PROTOCOL,
            'content': (folder / 'SKILL.md').read_text(), 'config': {},
            'files': [{'path': f.name, 'content': f.read_text()} for f in sorted(folder.iterdir())
                      if f.is_file() and f.name != 'SKILL.md']}
    rows = api.request('GET', '/api/skills')
    rows = rows if isinstance(rows, list) else rows['skills']
    existing = next((row for row in rows if row['name'] == folder.name), None)
    return api.request('PUT' if existing else 'POST', '/api/skills' + ('/' + existing['id'] if existing else ''), body)['id']


def install(config, parent_issue, confirm=False):
    """Called by the installer SOP after benchmark preparation and confirmation."""
    paths = [config['benchmark'].get(k) for k in ('repository', 'public_data', 'contract')]
    paths += config['benchmark']['private_paths']
    if config.get('knowledge_base'):
        paths.append(config['knowledge_base'])
    paths += [a for a in config['benchmark']['evaluate_argv'] if Path(a).is_absolute() and '{' not in a]
    missing = [p for p in dict.fromkeys(paths) if p and not Path(p).exists()]
    if not config['benchmark'].get('contract'):
        missing.append('benchmark.contract')
    if missing:
        return {'status': 'preparation_required', 'missing_paths': missing,
                'instruction': 'Prepare the benchmark resources and its validate, score and is_complete functions.'}
    contract(config)
    if not confirm:
        return {'status': 'ready', 'config': config}
    state_file = Path(config['state']) / 'installation.json'
    if state_file.exists():
        saved = read(state_file)
        if saved['config'] != config or saved['parent_issue_id'] != parent_issue:
            raise ValueError('installation settings changed; preserve the installed state')
        return saved
    benchmark = config['benchmark']
    if benchmark.get('baseline_delivery') is not None:
        artifact = Path(config['state']) / 'artifacts' / 'baseline'
        if not artifact.exists():
            snapshot(benchmark['repository'], artifact)
        search(config).register_baseline('baseline', 'Initial baseline',
            benchmark.get('baseline_description', benchmark['description']), benchmark['baseline_delivery'],
            artifact=str(artifact), review=benchmark.get('baseline_review'))
    api = API()
    parent = api.request('GET', '/api/issues/' + parent_issue)
    runtime_id = config['multica'].get('runtime_id') or api.request('GET', '/api/agents/' + parent['assignee_id'])['runtime_id']
    skill_id = cli_skill(api)
    suffix = ' #' + str(parent['number'])
    solver = api.request('POST', '/api/agents', {'name': 'Solution developer' + suffix,
        'description': 'Benchmark solver for issue ' + parent_issue, 'runtime_id': runtime_id,
        'model': config['solver_model'], 'thinking_level': config['multica'].get('thinking_level', 'high'),
        'visibility': 'workspace', 'max_concurrent_tasks': config['clients']})
    saved = {'status': 'installing', 'config': config, 'workspace_id': api.workspace, 'server_url': api.base,
             'parent_issue_id': parent_issue, 'solver_id': solver['id'], 'skill_id': skill_id, 'clients': []}
    write(state_file, saved)
    for index in range(config['clients']):
        session = 'client-' + str(index + 1)
        workdir = Path(config['workdirs']) / session
        workdir.mkdir(parents=True, exist_ok=True)
        agent = api.request('POST', '/api/agents', {'name': 'Improvement client ' + str(index + 1) + suffix,
            'description': 'Optimization client for issue ' + parent_issue, 'runtime_id': runtime_id,
            'model': config['cli_babysitter_model'], 'instructions': CLIENT_PROTOCOL,
            'thinking_level': 'low', 'visibility': 'workspace', 'max_concurrent_tasks': 1})
        api.request('POST', '/api/agents/' + agent['id'] + '/skills/add', {'skill_ids': [skill_id]})
        issue = api.request('POST', '/api/issues', {'title': 'Improvement client ' + str(index + 1),
            'description': 'Follow multica-aide-cli until its exit condition is met.',
            'project_id': config['multica']['project_id'], 'parent_issue_id': parent_issue, 'status': 'backlog'})
        api.request('POST', '/api/issues/' + issue['id'] + '/labels', {'label_id': label(api, 'query')})
        metadata(api, issue['id'], {'query_workdir': str(workdir), 'aide_checkout': str(ROOT),
                                   'aide_config': config['config_path'], 'aide_runtime_id': runtime_id})
        write(workdir / '.aide-client.json', {'config_path': config['config_path'], 'session_id': session,
            'completed': 0, 'candidate': None, 'node_id': None, 'finished': False})
        api.request('PUT', '/api/issues/' + issue['id'], {'assignee_type': 'agent', 'assignee_id': agent['id'], 'suppress_run': True})
        saved['clients'].append({'session_id': session, 'agent_id': agent['id'], 'issue_id': issue['id'], 'workdir': str(workdir)})
        write(state_file, saved)
    saved['status'] = 'installed'
    write(state_file, saved)
    return saved


def wake(api, client, content):
    api.request('PUT', '/api/issues/' + client['issue_id'], {'status': 'in_progress', 'suppress_run': True})
    return api.request('POST', '/api/issues/' + client['issue_id'] + '/comments',
                       {'content': '[@Client](mention://agent/' + client['agent_id'] + ') ' + content})


def completed_node(config):
    check = contract(config).is_complete
    for node in search(config).snapshot()['nodes']:
        if node['score'] is not None and check(node.get('delivery', node['score'])) is True:
            return node
    return None


def begin(config, max_rounds=0):
    """Host the board and supervise normal SOP completion; Multica owns failures."""
    state_file = Path(config['state']) / 'installation.json'
    saved = read(state_file)
    api = API()
    if saved['config'] != config or saved['workspace_id'] != api.workspace or saved['server_url'] != api.base:
        raise ValueError('run with the installed configuration and native workspace')
    if config.get('port'):
        viewer = subprocess.Popen([sys.executable, str(LAUNCHER), '--config', config['config_path'], 'serve'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        saved['viewer_pid'] = viewer.pid
    saved['controller_pid'] = os.getpid()
    write(state_file, saved)
    started = set()
    while True:
        node = completed_node(config)
        if node:
            return {'status': 'completed', 'node': node}
        nodes = search(config).snapshot()['nodes']
        baseline_ready = any(n['kind'] == 'baseline' and n['score'] is not None for n in nodes)
        for client in saved['clients']:
            issue = api.request('GET', '/api/issues/' + client['issue_id'])
            if issue['status'] == 'blocked':
                return {'status': 'needs_setup', 'issue_id': client['issue_id']}
            progress = read(Path(client['workdir']) / '.aide-client.json')
            if max_rounds and progress['completed'] >= max_rounds:
                continue
            if client['session_id'] not in started and (baseline_ready or client == saved['clients'][0]):
                entry = '.pi/skills/multica-aide-cli/wrapper.py'
                wake(api, client, 'Run in this issue\'s workdir: ' + shlex.join([sys.executable, entry]))
                started.add(client['session_id'])
        if max_rounds and all(read(Path(c['workdir']) / '.aide-client.json')['completed'] >= max_rounds for c in saved['clients']):
            return {'status': 'round_limit', 'nodes': nodes}
        time.sleep(config.get('poll_seconds', 10))
