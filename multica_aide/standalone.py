"""Install and host native Multica optimization clients."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

from . import SearchTree, sop_runtime
from .viewer import render_tree
from .worktrees import add_worktree, git, hidden_paths, prepare_template, sandbox

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = Path(__file__).with_name('__main__.py')
SKILLS = Path(__file__).with_name('skills')


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
    config.setdefault('agent_defaults', {})
    config.setdefault('mode', 'val_dev')
    if config['mode'] not in {'val_dev', 'separate'}:
        raise ValueError('mode must be val_dev or separate')
    roles = ('AIDE_val_dev',) if config['mode'] == 'val_dev' else ('AIDE_solution_validator', 'AIDE_solution_developer')
    for role in roles:
        if not isinstance(config.get(role), str) or not config[role].strip():
            raise ValueError(role + ' must name an agent')
    if config.get('port') is not None and (type(config['port']) is not int or not 0 < config['port'] < 65536):
        raise ValueError('port must be null or a TCP port')
    config.setdefault('template', '.aide/template')
    for field in ('tree', 'state', 'workdirs', 'template'):
        config[field] = str((path.parent / config[field]).resolve())
    if Path(config['workdirs']).is_relative_to(config['template']):
        raise ValueError('workdirs must be outside the prepared template repository')
    if config.get('knowledge_base'):
        config['knowledge_base'] = str((path.parent / config['knowledge_base']).resolve())
    bench = config['benchmark']
    for field in ('public_data', 'contract'):
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


def label(api, name):
    labels = api.request('GET', '/api/labels?resource_type=issue')['labels']
    row = next((row for row in labels if row['name'] == name), None)
    return (row or api.request('POST', '/api/labels', {'name': name, 'color': '#6b7280', 'resource_type': 'issue'}))['id']


def metadata(api, issue, values):
    for key, value in values.items():
        api.request('PUT', '/api/issues/' + issue + '/metadata/' + key, {'value': value})


def configured_agent(api, name, runtime_id, defaults):
    """Reuse the configured workspace agent without changing its settings."""
    matches = [a for a in api.request('GET', '/api/agents?include_archived=true') if a['name'] == name]
    if len(matches) > 1:
        raise ValueError('Multiple agents named ' + name + '; select a unique agent name')
    if matches:
        agent = matches[0]
        if agent.get('archived_at'):
            api.request('POST', '/api/agents/' + agent['id'] + '/restore', {})
        return agent
    print(f'WARNING: Agent {name!r} does not exist. Installation will create this shared workspace agent '
          'using agent_defaults.', file=sys.stderr, flush=True)
    return api.request('POST', '/api/agents', {
        **defaults, 'name': name, 'runtime_id': runtime_id, 'visibility': 'workspace',
        'description': 'Iterates on assigned improvement tasks.',
        'instructions': 'Follow the procedure supplied by the assigned issue.'})


def install(config, parent_issue, confirm=False):
    """Called by the installer SOP after benchmark preparation and confirmation."""
    paths = [config['benchmark'].get(k) for k in ('public_data', 'contract')]
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
    if config['mode'] == 'separate':
        if not config['benchmark']['private_paths']:
            raise ValueError('Separate validation requires benchmark.private_paths, including the locked evaluator')
        if any(Path(p).is_relative_to(config['template']) or Path(config['template']).is_relative_to(p)
               or Path(p).is_relative_to(config['workdirs']) or Path(config['workdirs']).is_relative_to(p)
               for p in config['benchmark']['private_paths']):
            raise ValueError('Private validation resources must be outside template and solution worktrees')
        subprocess.run(sandbox({'deny_paths': config['benchmark']['private_paths']}, Path.cwd(), ['/usr/bin/true']),
                       check=True, capture_output=True)
    if not confirm:
        return {'status': 'ready', 'config': config}
    state_file = Path(config['state']) / 'installation.json'
    if state_file.exists():
        saved = read(state_file)
        if saved['config'] != config or saved['parent_issue_id'] != parent_issue:
            raise ValueError('installation settings changed; preserve the installed state')
        if saved['status'] != 'installed':
            raise ValueError('Installation is incomplete; reconcile its saved issues before continuing')
        return saved
    template_commit = prepare_template(config, SKILLS)
    api = API()
    parent = api.request('GET', '/api/issues/' + parent_issue)
    runtime_id = config['multica'].get('runtime_id') or api.request('GET', '/api/agents/' + parent['assignee_id'])['runtime_id']
    role = 'AIDE_val_dev' if config['mode'] == 'val_dev' else 'AIDE_solution_validator'
    agent = configured_agent(api, config[role], runtime_id, config['agent_defaults'])
    developer = (configured_agent(api, config['AIDE_solution_developer'], runtime_id, config['agent_defaults'])
                 if config['mode'] == 'separate' else None)
    saved = {'status': 'installing', 'config': config, 'workspace_id': api.workspace, 'server_url': api.base,
             'parent_issue_id': parent_issue, 'template_commit': template_commit, 'clients': []}
    write(state_file, saved)
    repository = Path(config['template'])
    Path(config['workdirs']).mkdir(parents=True, exist_ok=True)
    base = git(repository, 'branch', '--show-current')
    for index in range(config['clients']):
        session = 'client-' + str(index + 1)
        workdir = Path(config['workdirs']) / session
        issue = api.request('POST', '/api/issues', {'title': 'Improvement client ' + str(index + 1),
            'description': f'Run python3 {workdir}/.agents/skills/multica-aide-cli/wrapper.py until it finishes.',
            'project_id': parent.get('project_id'), 'parent_issue_id': parent_issue, 'status': 'backlog'})
        branch = 'aide-' + issue['id']
        add_worktree(config, workdir, branch, template_commit)
        api.request('POST', '/api/issues/' + issue['id'] + '/labels', {'label_id': label(api, 'dev')})
        metadata(api, issue['id'], {'query_workdir': str(workdir), 'aide_client_workdir': str(workdir), 'aide_checkout': str(ROOT),
                                   'git_repo_root': str(repository), 'git_workdir': str(workdir),
                                   'git_branch': branch, 'git_parent_branch': base, 'git_owner_issue_id': issue['id']})
        progress = {'config_path': config['config_path'], 'session_id': session,
                    'completed': 0, 'candidate': None, 'node_id': None, 'finished': False,
                    'template_commit': template_commit}
        if developer:
            candidate = Path(config['workdirs']) / ('development-' + str(index + 1))
            add_worktree(config, candidate, branch + '-development', template_commit)
            child = api.request('POST', '/api/issues', {'title': 'Develop solutions for client ' + str(index + 1),
                'description': 'Develop only the public task supplied by the validator. Run every development command through '
                    + shlex.join([sys.executable, str(LAUNCHER), 'develop', '--workdir', str(workdir), '--'])
                    + ' COMMAND. Leave Git commits and evaluation to the validator. Deliver the changed files '
                    'and mark this issue in_review; await its next iteration.',
                'project_id': parent.get('project_id'), 'parent_issue_id': issue['id'], 'status': 'backlog'})
            api.request('POST', '/api/issues/' + child['id'] + '/labels', {'label_id': label(api, 'dev')})
            policy = {'deny_paths': hidden_paths([*config['benchmark']['private_paths'], config['benchmark']['contract'],
                       config['config_path'], config['state'], config['tree']])}
            metadata(api, child['id'], {'sandbox_policy': json.dumps(policy), 'query_workdir': str(candidate),
                'git_repo_root': str(repository), 'git_workdir': str(candidate), 'git_branch': branch + '-development',
                'git_parent_branch': branch, 'git_owner_issue_id': child['id']})
            api.request('PUT', '/api/issues/' + child['id'],
                        {'assignee_type': 'agent', 'assignee_id': developer['id'], 'suppress_run': True})
            progress.update(developer_issue_id=child['id'], developer_agent_id=developer['id'],
                            sandbox_policy=policy, initial_candidate=str(candidate))
            write(workdir / '.aide-development.json', {'candidate': str(candidate), 'policy': policy})
        write(workdir / '.aide-client.json', progress)
        api.request('PUT', '/api/issues/' + issue['id'], {'assignee_type': 'agent', 'assignee_id': agent['id'], 'suppress_run': True})
        saved['clients'].append({'session_id': session, 'agent_id': agent['id'], 'issue_id': issue['id'], 'workdir': str(workdir)})
        write(state_file, saved)
    benchmark = config['benchmark']
    if benchmark.get('baseline_delivery') is not None:
        artifact = Path(config['workdirs']) / 'baseline'
        add_worktree(config, artifact, 'aide-baseline', template_commit)
        search(config).register_baseline('baseline', 'Initial baseline',
            benchmark.get('baseline_description', benchmark['description']), benchmark['baseline_delivery'],
            artifact=str(artifact), review=benchmark.get('baseline_review'),
            metadata={'git_commit': template_commit, 'git_branch': 'aide-baseline'})
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


def development_running(tasks):
    return any(t['status'] in {'pending', 'queued', 'dispatched', 'running', 'cancelling', 'deferred', 'waiting_local_directory'}
               or (t.get('result') or {}).get('process_cleanup_pending') for t in tasks)


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
    delivered = set()
    observed = None
    while True:
        node = completed_node(config)
        nodes = search(config).snapshot()['nodes']
        summary = [(n['id'], n['score']) for n in nodes]
        if summary != observed:
            print('AIDE tree: ' + json.dumps(summary), file=sys.stderr, flush=True)
            sop_runtime.upload_artifact('aide-tree.html', render_tree(nodes))
            observed = summary
        if node:
            return {'status': 'completed', 'node': node}
        baseline_ready = any(n['kind'] == 'baseline' and n['score'] is not None for n in nodes)
        for client in saved['clients']:
            issue = api.request('GET', '/api/issues/' + client['issue_id'])
            if issue['status'] == 'blocked':
                return {'status': 'needs_setup', 'issue_id': client['issue_id']}
            progress = read(Path(client['workdir']) / '.aide-client.json')
            if max_rounds and progress['completed'] >= max_rounds:
                continue
            entry = str(Path(client['workdir']) / '.agents/skills/multica-aide-cli/wrapper.py')
            if (client['session_id'] not in started and not progress.get('development_pending')
                    and (baseline_ready or client == saved['clients'][0])):
                wake(api, client, 'Run in this issue\'s workdir: ' + shlex.join([sys.executable, entry]))
                started.add(client['session_id'])
                print('Started ' + client['session_id'] + ': ' + client['issue_id'], file=sys.stderr, flush=True)
            if progress.get('development_pending') and progress['candidate'] not in delivered:
                child = '/api/issues/' + progress['developer_issue_id']
                delivery = api.request('GET', child)
                if delivery['status'] in {'in_review', 'done'} and not development_running(api.request('GET', child + '/task-runs')):
                    wake(api, client, 'Developer delivery is ready. Resume ' + shlex.join([sys.executable, entry]))
                    delivered.add(progress['candidate'])
                    started.add(client['session_id'])
        if max_rounds and all(read(Path(c['workdir']) / '.aide-client.json')['completed'] >= max_rounds for c in saved['clients']):
            return {'status': 'round_limit', 'nodes': nodes}
        time.sleep(config.get('poll_seconds', 10))
