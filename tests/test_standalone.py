from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from multica_aide.__main__ import main
from multica_aide.standalone import API, begin, install, write, checkout, measured_artifact, owned_work, policy, register_proposal, register_submission, search, snapshot, submit


class StandaloneRound(unittest.TestCase):
    def test_host_scores_frozen_submission_and_task_failure_returns_to_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'original'
            original.mkdir()
            (original / 'value.txt').write_text('2')
            evaluator = root / 'evaluator.py'
            evaluator.write_text('import json,sys\nfrom pathlib import Path\n'
                                 'tree=Path(sys.argv[2])\n'
                                 'if tree.exists():\n'
                                 ' node=next(n for n in json.loads(tree.read_text())["nodes"] if n["id"]=="better")\n'
                                 ' assert node["score"] is None and node["put_forward_at"]\n'
                                 'print(json.dumps({"score":float((Path(sys.argv[1])/"value.txt").read_text()),'
                                 '"review":"Official host review"}))\n')
            config = {'tree': str(root / 'tree'), 'state': str(root / 'controller'), 'drafts': 1,
                      'benchmark': {'evaluate_argv': [sys.executable, str(evaluator), '{solution}',
                                                     str(root / 'tree/tree.json')]}}
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
                        value = {'id': 'query', 'status': fixture.get('issue_status', 'in_progress'), 'metadata': {
                            'query_workdir': str(workdir), 'aide_round_id': context['round_id']}}
                    payload = json.dumps(value).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(payload)

                def do_PUT(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    fixture['issue_status'] = body['status']
                    fixture['suppress_run'] = body['suppress_run']
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{}')

                def log_message(self, *args):
                    pass

            with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                environment = {'MULTICA_SERVER_URL': f'http://127.0.0.1:{server.server_port}',
                               'MULTICA_TOKEN': 'local-test', 'MULTICA_WORKSPACE_ID': 'test', 'MULTICA_ISSUE_ID': 'query'}
                try:
                    with patch.dict(os.environ, environment):
                        with patch.object(sys, 'argv', ['AIDE_cli', 'register', '--id', 'better',
                                          '--title', 'Better result', '--description', 'Measured improvement']), \
                                redirect_stdout(io.StringIO()):
                            main()
                        self.assertTrue((workdir / '.aide-submission.json').is_file())
                        self.assertNotIn('issue_status', fixture)
                        pending = register_proposal(config, client)
                        self.assertIsNone(pending['score'])
                        self.assertIsNone(pending['review'])
                        self.assertTrue(pending['put_forward_at'])
                        self.assertEqual(pending['put_forward_at'],
                                         json.loads((workdir / '.aide-submission.json').read_text())['put_forward_at'])
                        self.assertFalse(Path(pending['artifact']).exists())
                        self.assertIn('client', search(config).snapshot()['sessions'])
                        with self.assertRaisesRegex(ValueError, 'request host scoring'):
                            register_submission(config, client)
                        with patch.object(sys, 'argv', ['AIDE_cli', 'score', '--id', 'better', '--score', '999']), \
                                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                            main()
                        with patch.object(sys, 'argv', ['AIDE_cli', 'score', '--id', 'better', '--review', 'Client note']), \
                                redirect_stdout(io.StringIO()):
                            main()
                        self.assertEqual(fixture['issue_status'], 'done')
                        self.assertTrue(fixture['suppress_run'])
                        submit('better', 'Better result', 'Measured improvement', 'Client note')
                        node = register_submission(config, client)
                        self.assertEqual(node['score'], 7)
                        self.assertEqual(node['review'], 'Official host review')
                        self.assertEqual(node['put_forward_at'], pending['put_forward_at'])
                        self.assertGreaterEqual(datetime.fromisoformat(node['scored_at']),
                                                datetime.fromisoformat(node['put_forward_at']))
                        self.assertGreaterEqual(node['test_duration_seconds'], 0)
                        self.assertEqual((Path(node['artifact']) / 'value.txt').read_text(), '7')
                        self.assertFalse((Path(node['artifact']) / '.aide-submission.json').exists())
                        (workdir / 'value.txt').write_text('99')
                        self.assertEqual(register_submission(config, client), node)
                        self.assertEqual(search(config).snapshot()['sessions'], {})
                        with self.assertRaisesRegex(RuntimeError, 'task-failed.*runtime launch failed'):
                            owned_work(API(), 'query')
                        fixture['tasks'].append({'id': 'task-fixed', 'created_at': '2026-01-02', 'status': 'completed'})
                        self.assertEqual(owned_work(API(), 'query'), [])
                finally:
                    server.shutdown()
                    thread.join()

    def test_direct_cli_register_then_score_updates_the_same_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {'tree': temporary, 'drafts': 1}
            tree = search(config)
            tree.register_baseline('baseline', 'Baseline', 'Measured baseline', .2)
            context = tree.next_context('embedded-client')
            with patch.dict(os.environ, {}, clear=True), \
                    patch('multica_aide.__main__.load_config', return_value=config):
                with patch.object(sys, 'argv', ['AIDE_cli', 'describe', '--id', 'candidate', '--title', 'Candidate',
                                  '--description', 'Proposed change', '--session-id', 'embedded-client',
                                  '--round-id', context['round_id']]), redirect_stdout(io.StringIO()):
                    main()
                proposed = tree.snapshot()['nodes'][-1]
                self.assertIsNone(proposed['score'])
                with patch.object(sys, 'argv', ['AIDE_cli', 'score', '--id', 'candidate', '--score', '.8',
                                  '--review', 'Measured result']), redirect_stdout(io.StringIO()):
                    main()
            state = tree.snapshot()
            self.assertEqual(len(state['nodes']), 2)
            final = state['nodes'][-1]
            self.assertEqual(final['id'], proposed['id'])
            self.assertEqual(final['put_forward_at'], proposed['put_forward_at'])
            self.assertEqual((final['score'], final['review']), (.8, 'Measured result'))
            self.assertEqual(state['sessions'], {})

    def test_confirmed_model_change_preserves_registered_baseline_and_agents(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {'state': temporary, 'tree': str(Path(temporary) / 'tree'), 'drafts': 2,
                      'config_path': str(Path(temporary) / 'config.yaml'),
                      'solver_model': 'provider/terra', 'cli_babysitter_model': 'provider/luna'}
            tree = search(config)
            tree.register_baseline('baseline', 'Baseline', 'Official result', .37)
            before = tree.snapshot()
            clients = [{'agent_id': 'client-1', 'issue_id': 'query-1', 'completed': 1},
                       {'agent_id': 'client-2', 'issue_id': 'query-2', 'completed': 1}]
            write(Path(temporary) / 'installation.json', {'config': config, 'status': 'installed',
                  'workspace_id': 'workspace', 'server_url': 'http://local-test',
                  'solver_id': 'solver', 'parent_issue_id': 'parent', 'clients': clients})
            updated = {**config, 'solver_model': 'provider/sol'}
            with patch('multica_aide.standalone.API') as api:
                result = install(updated, 'parent', confirm=True)
            self.assertEqual(result['config'], updated)
            self.assertEqual(result['clients'], clients)
            self.assertEqual(shlex.split(result['next_command'])[-3:], ['--config', config['config_path'], 'begin'])
            self.assertEqual(tree.snapshot(), before)
            updated_solver = api.return_value.request.call_args_list[0].args
            self.assertEqual(updated_solver[:2], ('PUT', '/api/agents/solver'))
            self.assertEqual(updated_solver[2]['model'], 'provider/sol')
            self.assertEqual(updated_solver[2]['instructions'], '')
            self.assertEqual(api.return_value.request.call_count, 3)
            self.assertEqual(json.loads((Path(temporary) / 'installation.json').read_text())['config'], updated)
            with patch('multica_aide.standalone.API') as api:
                install(updated, 'parent', confirm=True)
            self.assertEqual(api.return_value.request.call_count, 3)
            self.assertEqual(tree.snapshot(), before)
            with patch('multica_aide.standalone.API') as api, \
                    patch('multica_aide.standalone.probe') as probe_runtime, \
                    patch('multica_aide.standalone.owned_work', return_value=[]):
                api.return_value.workspace = 'workspace'
                api.return_value.base = 'http://local-test'
                self.assertEqual(begin(updated, max_rounds=1)['status'], 'completed')
            api.return_value.request.assert_not_called()
            probe_runtime.assert_called_once_with(updated)
            self.assertEqual(tree.snapshot(), before)

    def test_missing_benchmark_returns_preparation_before_native_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = str(Path(temporary) / 'benchmark')
            knowledge = str(Path(temporary) / 'knowledge.md')
            config = {'state': str(Path(temporary) / 'controller'), 'tree': str(Path(temporary) / 'tree'),
                      'config_path': str(Path(temporary) / 'config.yaml'), 'knowledge_base': knowledge,
                      'benchmark': {'repository': missing, 'public_data': missing, 'private_paths': [missing],
                                    'evaluate_argv': [sys.executable, missing, '{solution}']},
                      'sandbox': {'probe_argv': [sys.executable, missing]}}
            with patch('multica_aide.standalone.API') as api:
                result = install(config, 'parent')
            self.assertEqual(result['status'], 'preparation_required')
            self.assertEqual(result['missing_paths'], [missing, knowledge])
            self.assertEqual(shlex.split(result['after_preparation_command'])[-3:], ['install', '--parent-issue', 'parent'])
            Path(missing).mkdir()
            Path(knowledge).write_text('Benchmark knowledge')
            self.assertIn(knowledge, json.loads(policy(config))['read_only_paths'])
            with patch('multica_aide.standalone.API') as api:
                result = install(config, 'parent')
            self.assertEqual(result['status'], 'preparation_required')
            self.assertNotIn('next_command', result)
            self.assertEqual(shlex.split(result['after_preparation_command'])[-1], '--confirm-config')
            api.assert_not_called()

if __name__ == '__main__':
    unittest.main()
