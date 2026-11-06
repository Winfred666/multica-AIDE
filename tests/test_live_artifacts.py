"""Exercise live deliverables through the SOP runtime and multipart HTTP API."""
import contextlib
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


PACKAGE = Path(__file__).resolve().parents[1] / 'multica_aide'
spec = importlib.util.spec_from_file_location('artifact_runtime', PACKAGE / 'sop_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class LiveArtifacts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copy(PACKAGE / 'sop.py', self.root / 'sop.py')
        shutil.copy(PACKAGE / 'sop_runtime.py', self.root / 'wrapper.py')
        (self.root / 'SKILL.md').write_text('---\nname: multica-example\ndescription: Measure an idea.\n---\n')
        (self.root / 'config.json').write_text('{"DEBUG": true}')
        (self.root / 'controller.py').write_text('''
from pathlib import Path
from sop import Completed, State, Option, Workflow
ROOT = Path(__file__).parent
def initialize(context):
    if (ROOT / 'already-finished').exists():
        raise Completed('The delivery already meets the goal.')
def test(args, context):
    context['state']['score'] = .25
    if (ROOT / 'skip-review').exists():
        return {'phase': 'finished'}
def finish(args, context):
    with (ROOT / 'actions').open('a') as output: output.write('finish\\n')
def check(context):
    if context.get('result', {}).get('phase') == 'finished' and (ROOT / 'fail').exists():
        raise AssertionError('Delivery missing')
def artifacts(context):
    context['cli']('get', context['issue_id'])
    return {'tree.html': '<!doctype html><p>score=' + str(context['state'].get('score')) + '</p>'}
workflow = Workflow(
    State('idea', 'Develop a measured idea.', initial=True, precheck=initialize,
          options=(Option('test', 'review', 'Test.', test),)),
    State('review', 'Review the measured score.', options=(
        Option('refresh', 'review', 'Refresh.', test), Option('finish', 'finished', 'Deliver.', finish))),
    State('finished', 'Deliver the accepted solution.', final=True, precheck=check))
''')
        self.rows, self.requests = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                message = BytesParser(policy=default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body)
                parts = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
                filename = parts['file'].get_filename()
                row = {'id': filename, 'issue_id': parts['issue_id'].get_content(),
                       'task_id': parts['task_id'].get_content(), 'filename': filename,
                       'content_type': parts['file'].get_content_type(), 'html': parts['file'].get_content()}
                owner.requests.append((self.path, dict(self.headers), row))
                owner.rows[(row['issue_id'], filename, row['content_type'])] = row
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'id': filename}).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.env = {'MULTICA_TASK_ID': 'task-one', 'MULTICA_ISSUE_ID': 'issue-one',
                    'MULTICA_WORKSPACE_ID': 'workspace-one', 'MULTICA_AGENT_ID': 'agent-one',
                    'MULTICA_TOKEN': 'test-artifact-token',
                    'MULTICA_SERVER_URL': 'http://127.0.0.1:' + str(self.server.server_port)}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env, clear=True).start()
        patch.object(runtime, 'ROOT', self.root).start()
        patch.object(runtime, 'multica', return_value={'id': 'issue-one'}).start()

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = runtime.main(list(args))
        return code, json.loads(output.getvalue())

    def state_html(self):
        return self.rows[('issue-one', 'multica-example-state.html', 'text/html')]['html']

    def test_transitions_replace_live_files_and_preserve_native_identity(self):
        self.assertEqual(self.call()[1]['status'], 'idea')
        self.assertIn('task-one', self.state_html())
        self.assertIn('Develop a measured idea.', self.state_html())
        self.assertIn('idea --&gt;|test| review', self.state_html())
        requests = len(self.requests)
        self.call()
        self.call('--help')
        self.call('--validate')
        self.assertEqual(len(self.requests), requests)
        self.assertEqual(self.call('test')[1]['status'], 'review')
        self.assertIn('class review current', self.state_html())
        self.assertIn('score=0.25', self.rows[('issue-one', 'tree.html', 'text/html')]['html'])
        requests = len(self.requests)
        self.call('refresh')
        self.assertEqual(len(self.requests), requests + 1)  # Existing view only; no state shift.
        self.assertEqual(self.call('finish')[1]['status'], 'finished')
        self.assertFalse((self.root / '.state/task-one.json').exists())
        self.assertIn('class finished current', self.state_html())
        with patch.dict(os.environ, {'MULTICA_TASK_ID': 'task-two'}):
            self.call()
        self.assertEqual(len(self.rows), 2)
        self.assertIn('task-two', self.state_html())
        self.assertNotIn('task-one', self.state_html())
        for path, headers, row in self.requests:
            self.assertEqual(path, '/api/upload-file')
            self.assertEqual(headers['Authorization'], 'Bearer test-artifact-token')
            self.assertEqual(headers['X-Workspace-Id'], 'workspace-one')
            self.assertEqual(row['issue_id'], 'issue-one')
            self.assertEqual(row['content_type'], 'text/html')

    def test_failed_target_is_visible_and_repair_does_not_repeat_action(self):
        self.call()
        self.call('test')
        (self.root / 'fail').touch()
        self.assertEqual(self.call('finish')[1]['status'], 'error')
        self.assertIn('&quot;failed_state&quot;: &quot;finished&quot;', self.state_html())
        self.assertIn('&quot;boundary&quot;: &quot;after&quot;', self.state_html())
        self.assertIn('Delivery missing', self.state_html())
        (self.root / 'fail').unlink()
        self.assertEqual(self.call('repaired')[1]['status'], 'finished')
        self.assertEqual((self.root / 'actions').read_text(), 'finish\n')

    def test_debug_is_opt_in_and_existing_views_remain_live(self):
        for config in (None, '{"DEBUG": false}'):
            with self.subTest(config=config):
                path = self.root / 'config.json'
                if config is None:
                    path.unlink()
                else:
                    path.write_text(config)
                self.rows.clear()
                self.call()
                self.call('test')
                self.assertEqual(list(self.rows), [('issue-one', 'tree.html', 'text/html')])
                self.assertIn('score=0.25', next(iter(self.rows.values()))['html'])
                self.call('finish')

    def test_chart_uses_the_actual_scripted_target(self):
        self.call()
        (self.root / 'skip-review').touch()
        self.assertEqual(self.call('test')[1]['status'], 'finished')
        self.assertIn('idea --&gt;|test| finished', self.state_html())
        self.assertNotIn('idea --&gt;|test| review', self.state_html())

    def test_chart_shows_a_failed_scripted_target_and_completion_on_entry(self):
        self.call()
        (self.root / 'skip-review').touch()
        (self.root / 'fail').touch()
        self.assertEqual(self.call('test')[1]['status'], 'error')
        self.assertIn('idea --&gt;|test| finished', self.state_html())
        (self.root / 'fail').unlink()
        self.assertEqual(self.call('repaired')[1]['status'], 'finished')
        (self.root / 'already-finished').touch()
        with patch.dict(os.environ, {'MULTICA_TASK_ID': 'task-two'}):
            self.assertEqual(self.call()[1]['status'], 'finished')
        self.assertIn('task-two', self.state_html())
        self.assertIn('class finished current', self.state_html())

    def test_upload_failure_preserves_action_result_and_task_record(self):
        self.call()
        errors = io.StringIO()
        with patch.object(runtime, 'urlopen', side_effect=OSError('offline')), contextlib.redirect_stderr(errors):
            code, result = self.call('test')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'review')
        self.assertEqual(json.loads((self.root / '.state/task-one.json').read_text())['status'], 'review')
        self.assertIn('Live artifact update failed: offline', errors.getvalue())

    def test_foreground_controller_delivers_the_final_tree_before_returning(self):
        from multica_aide import standalone
        config = {'state': str(self.root), 'port': None}
        standalone.write(self.root / 'installation.json', {'config': config,
            'workspace_id': 'workspace-one', 'server_url': self.env['MULTICA_SERVER_URL']})
        node = {'id': 'baseline', 'score': .25}
        tree = Mock()
        tree.snapshot.return_value = {'nodes': [node]}
        api = Mock(workspace='workspace-one', base=self.env['MULTICA_SERVER_URL'])
        with patch.object(standalone, 'API', return_value=api), \
                patch.object(standalone, 'completed_node', return_value=node), \
                patch.object(standalone, 'search', return_value=tree):
            self.assertEqual(standalone.begin(config)['status'], 'completed')
        self.assertIn('"score": 0.25', self.rows[('issue-one', 'aide-tree.html', 'text/html')]['html'])
