from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from multica_aide.standalone import API, install, write, checkout, measured_artifact, owned_work, recover_children, register_submission, search, snapshot, submit


class StandaloneRound(unittest.TestCase):
    def test_host_scores_frozen_submission_and_task_failure_returns_to_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'original'
            original.mkdir()
            (original / 'value.txt').write_text('2')
            evaluator = root / 'evaluator.py'
            evaluator.write_text('import json,sys\nfrom pathlib import Path\n'
                                 'print(json.dumps({"score":float((Path(sys.argv[1])/"value.txt").read_text())}))\n')
            config = {'tree': str(root / 'tree'), 'state': str(root / 'controller'), 'drafts': 1,
                      'benchmark': {'evaluate_argv': [sys.executable, str(evaluator), '{solution}']}}
            baseline = root / 'controller/artifacts/baseline'
            snapshot(original, baseline)
            search(config).register_baseline('baseline', 'Baseline', 'Original result',
                                             measured_artifact(config, baseline)['score'], artifact=str(baseline))
            context = search(config).next_context('client')
            workdir = root / 'workdir'
            checkout(context['restore_node']['artifact'], workdir)
            (workdir / 'value.txt').write_text('7')
            client = {'session_id': 'client', 'round': {'workdir': str(workdir),
                      'round_id': context['round_id'], 'parent_id': context['parent_id']}}
            fixture = {'tasks': [{'id': 'task-failed', 'status': 'failed', 'created_at': '2026-01-01',
                                  'error': 'runtime launch failed'}]}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path.endswith('/task-runs'):
                        value = fixture['tasks']
                    elif self.path.endswith('/children'):
                        value = {'issues': []}
                    else:
                        value = {'id': 'query', 'status': 'in_progress', 'metadata': {
                            'query_workdir': str(workdir), 'aide_round_id': context['round_id']}}
                    payload = json.dumps(value).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, *args):
                    pass

            with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                environment = {'MULTICA_SERVER_URL': f'http://127.0.0.1:{server.server_port}',
                               'MULTICA_TOKEN': 'local-test', 'MULTICA_WORKSPACE_ID': 'test', 'MULTICA_ISSUE_ID': 'query'}
                try:
                    with patch.dict(os.environ, environment):
                        submit('better', 'Better result', 'Measured improvement')
                        self.assertTrue((workdir / '.aide-submission.json').is_file())
                        node = register_submission(config, client)
                        self.assertEqual(node['score'], 7)
                        self.assertEqual((Path(node['artifact']) / 'value.txt').read_text(), '7')
                        self.assertFalse((Path(node['artifact']) / '.aide-submission.json').exists())
                        (workdir / 'value.txt').write_text('99')
                        self.assertEqual(register_submission(config, client), node)
                        self.assertEqual(search(config).snapshot()['sessions'], {})
                        with self.assertRaisesRegex(RuntimeError, 'task-failed.*runtime launch failed'):
                            owned_work(API(), 'query')
                        fixture['tasks'].append({'id': 'task-fixed', 'created_at': '2026-01-02', 'status': 'completed'})
                        self.assertEqual(owned_work(API(), 'query'), ([], {}))
                finally:
                    server.shutdown()
                    thread.join()

    def test_confirmed_model_change_preserves_registered_baseline_and_agents(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {'state': temporary, 'tree': str(Path(temporary) / 'tree'), 'drafts': 2,
                      'solver_model': 'provider/terra', 'cli_babysitter_model': 'provider/luna'}
            tree = search(config)
            tree.register_baseline('baseline', 'Baseline', 'Official result', .37)
            before = tree.snapshot()
            clients = [{'agent_id': 'client-1'}, {'agent_id': 'client-2'}]
            write(Path(temporary) / 'installation.json', {'config': config, 'status': 'installed',
                  'solver_id': 'solver', 'parent_issue_id': 'parent', 'clients': clients})
            updated = {**config, 'solver_model': 'provider/sol'}
            with patch('multica_aide.standalone.API') as api:
                result = install(updated, 'parent', confirm=True)
            self.assertEqual(result['config'], updated)
            self.assertEqual(result['clients'], clients)
            self.assertEqual(tree.snapshot(), before)
            self.assertEqual(api.return_value.request.call_args_list[0].args,
                             ('PUT', '/api/agents/solver', {'model': 'provider/sol'}))
            self.assertEqual(api.return_value.request.call_count, 3)
            self.assertEqual(json.loads((Path(temporary) / 'installation.json').read_text())['config'], updated)

    def test_blocked_dev_child_wakes_its_babysitter_once_per_changed_state(self):
        class FixtureAPI:
            def __init__(self):
                self.revision = 'first'
                self.wakeups = []

            def request(self, method, path, body=None):
                if method == 'POST' and path.endswith('/wakeups'):
                    self.wakeups.append(body)
                    return {'id': 'wakeup'}
                if method == 'POST' and path.endswith('/comments'):
                    return {'id': 'recovery-comment'}
                if method == 'PUT':
                    return None
                if path.endswith('/children'):
                    return {'issues': [{'id': 'child'}] if path.startswith('/api/issues/query/') else []}
                if path.endswith('/task-runs'):
                    return [{'id': 'dev-' + self.revision, 'status': 'failed'}] if '/child/' in path else []
                return {'id': path.rsplit('/', 1)[-1], 'status': 'blocked' if path.endswith('/child') else 'in_progress',
                        'updated_at': self.revision}

        api = FixtureAPI()
        client = {'issue_id': 'query', 'agent_id': 'light-babysitter',
                  'round': {'round_id': 'round', 'thread_id': 'thread'}}
        active, blocked = owned_work(api, 'query')
        self.assertEqual(active, [])
        self.assertEqual(list(blocked), ['child'])
        recover_children(api, client, blocked)
        recover_children(api, client, blocked)
        self.assertEqual(len(api.wakeups), 1)
        self.assertEqual(api.wakeups[0]['agent_id'], 'light-babysitter')
        api.revision = 'retry'
        recover_children(api, client, owned_work(api, 'query')[1])
        self.assertEqual(len(api.wakeups), 2)


if __name__ == '__main__':
    unittest.main()
