import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
from multica_aide import sop_runtime
from multica_aide.standalone import SKILLS, evaluate, load_config, read, search, write


class ClientSOP(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skill = self.root / 'skill'
        shutil.copytree(SKILLS / 'multica-aide-cli', self.skill)
        runtime_root = patch.object(sop_runtime, 'ROOT', self.skill)
        runtime_root.start()
        self.addCleanup(runtime_root.stop)
        self.controller = sop_runtime.load_controller()
        self.contract = self.root / 'contract.py'
        self.contract.write_text("from pathlib import Path\ndef validate(solution): return (Path(solution)/'submission.json').is_file()\ndef score(delivery): return delivery['metrics']['loss']\ndef is_complete(delivery): return delivery['metrics']['loss'] <= 0.25\n")
        evaluator = self.root / 'evaluate.py'
        evaluator.write_text("import json,sys\nfrom pathlib import Path\nx=json.loads((Path(sys.argv[1])/'submission.json').read_text())\nprint(json.dumps({'metrics':{'loss':x['loss']},'valid':True}))\n")
        public = self.root / 'public'
        public.mkdir()
        self.config_path = self.root / 'config.local.yaml'
        self.settings = {'drafts': 2, 'clients': 2, 'port': None, 'maximize': False,
            'tree': str(self.root / 'tree'), 'state': str(self.root / 'controller'),
            'workdirs': str(self.root / 'workdirs'), 'multica': {'project_id': 'project', 'runtime_id': None},
            'benchmark': {'description': 'Improve the benchmark loss.', 'repository': None,
                'public_data': str(public), 'private_paths': [], 'contract': str(self.contract),
                'evaluate_argv': [sys.executable, str(evaluator), '{solution}']}}
        self.save_settings()
        self.workdir = self.root / 'client'
        self.workdir.mkdir()
        write(self.workdir / '.aide-client.json', {'config_path': str(self.config_path), 'session_id': 'client-1',
            'completed': 0, 'candidate': None, 'node_id': None, 'finished': False})
        self.issue = {'id': 'client-issue', 'workspace_id': 'workspace', 'parent_issue_id': 'parent',
                      'status': 'in_progress', 'metadata': {'query_workdir': str(self.workdir)}}
        self.calls = []
        native = patch.object(sop_runtime, 'multica', self.cli)
        native.start()
        self.addCleanup(native.stop)

    def save_settings(self):
        self.config_path.write_text(yaml.safe_dump(self.settings))
        self.config = load_config(self.config_path)
        write(Path(self.config['state']) / 'installation.json', {'solver_id': 'solver'})

    def cli(self, *words):
        self.calls.append(words)
        if words[0] == 'get': return self.issue
        if words[0] == 'status': self.issue['status'] = words[2]; return self.issue
        if words[:2] == ('comment', 'add'): return {'id': 'comment'}
        raise AssertionError(words)

    def run_sop(self, *argv):
        args = sop_runtime.arguments(list(argv), self.controller)
        context = {'task_id': 'native-test-task', 'issue_id': self.issue['id'], 'workspace_id': 'workspace',
                   'agent_id': 'client', 'argv': list(argv)}
        return sop_runtime.transition(args, context, self.controller)

    def progress(self):
        return read(self.workdir / '.aide-client.json')

    def solution(self, loss):
        path = Path(self.progress()['candidate'])
        (path / 'submission.json').write_text(json.dumps({'loss': loss}))
        return evaluate(self.config, path)

    def test_empty_baseline_then_draft_requires_description_delivery_and_review(self):
        self.assertEqual(self.run_sop()['status'], 'initialize')
        self.assertFalse(self.run_sop('record', '--delivery', '{}', '--review', 'Premature.')['accepted'])
        self.assertEqual(search(self.config).snapshot()['nodes'], [])
        self.assertEqual(self.run_sop('enter')['status'], 'idea')
        self.assertFalse(self.run_sop('test', '--description', 'A minimal template baseline.')['accepted'])
        delivery = self.solution(.9)
        self.assertEqual(self.run_sop('test', '--description', 'A minimal template baseline.')['status'], 'test')
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'Two\n\nParagraphs.')['accepted'])
        self.assertEqual(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The baseline is measured.')['status'], 'tested')
        self.assertEqual(self.run_sop('continue')['status'], 'idea')
        improved = self.solution(.2)
        self.assertEqual(self.run_sop('test', '--description', 'An improved candidate.')['status'], 'test')
        self.assertEqual(self.run_sop('record', '--delivery', json.dumps(improved), '--review', 'The measured loss meets the goal.')['status'], 'tested')
        result = self.run_sop('continue')
        self.assertTrue(result['finished'])
        self.assertEqual(result['data']['node']['delivery'], improved)
        self.assertEqual(result['data']['node']['score'], .2)
        self.assertEqual(self.issue['status'], 'in_progress')
        self.assertGreaterEqual(result['data']['node']['test_duration_seconds'], 0)
        self.assertEqual(len(search(self.config).snapshot()['nodes']), 2)

    def test_existing_untested_baseline_enters_test_without_an_idea_round(self):
        repository = self.root / 'baseline'
        repository.mkdir()
        (repository / 'submission.json').write_text('{"loss": 0.9}')
        self.settings['benchmark']['repository'] = str(repository)
        self.save_settings()
        self.assertEqual(self.run_sop()['data']['route'], 'test')
        self.assertEqual(self.run_sop('enter')['status'], 'test')
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('test', '--description', 'Cannot skip the pending test.')['accepted'])

    def test_existing_passing_delivery_exits_before_creating_a_candidate(self):
        artifact = self.root / 'passing'
        artifact.mkdir()
        (artifact / 'submission.json').write_text('{"loss": 0.2}')
        delivery = evaluate(self.config, artifact)
        search(self.config).register_baseline('baseline', 'Passing', 'Already measured.', delivery, artifact=str(artifact))
        result = self.run_sop()
        self.assertTrue(result['finished'])
        self.assertEqual(result['data']['node']['delivery'], delivery)
        self.assertIsNone(self.progress()['candidate'])

    def test_missing_contract_blocks_and_notifies_parent_at_initialization(self):
        self.contract.unlink()
        self.assertEqual(self.run_sop()['data']['route'], 'needs_setup')
        self.assertEqual(self.run_sop('enter')['status'], 'needs_setup')
        self.assertEqual(self.issue['status'], 'blocked')
        notification = next(c for c in self.calls if c[:3] == ('comment', 'add', 'parent'))
        self.assertIn('check is failed, failed to enter init state', notification[-1])


if __name__ == '__main__':
    unittest.main()
