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
        development = patch('multica_aide.campaign.develop')
        self.develop = development.start()
        self.addCleanup(development.stop)
        helper = patch('multica_aide.campaign.development_helper', return_value=Path(__file__))
        helper.start()
        self.addCleanup(helper.stop)

    def test_installer_runs_controller_in_the_calling_task(self):
        from multica_aide import campaign
        result = {'status': 'completed', 'node': {'id': 'measured'}}
        with patch.object(campaign, 'installer_config', return_value=self.config), \
                patch('multica_aide.standalone.begin', return_value=result) as begin:
            self.assertEqual(campaign.start_install(None, {}), {'data': result})
        begin.assert_called_once_with(self.config)

    def save_settings(self):
        self.config_path.write_text(yaml.safe_dump(self.settings))
        self.config = load_config(self.config_path)
        write(Path(self.config['state']) / 'installation.json', {'solver_id': 'solver'})

    def cli(self, *words):
        self.calls.append(words)
        if words[0] == 'get':
            return {'id': 'parent', 'assignee_id': 'installer'} if words[1] == 'parent' else self.issue
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
        idea = self.run_sop('enter')
        self.assertEqual(idea['status'], 'idea')
        self.assertEqual(idea['data']['solver_id'], 'solver')
        self.assertTrue(idea['data']['baseline'])
        self.assertEqual(idea['data']['tree']['nodes'], [])
        self.assertFalse(self.run_sop('test', '--description', 'A minimal template baseline.')['accepted'])
        delivery = self.solution(.9)
        test = self.run_sop('test', '--description', 'A minimal template baseline.')
        self.assertEqual(test['status'], 'review')
        self.assertEqual(test['data']['delivery'], delivery)
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'Two\n\nParagraphs.')['accepted'])
        progress = self.progress()
        progress['development_issue'] = {'id': 'development'}
        write(self.workdir / '.aide-client.json', progress)
        with patch('multica_aide.campaign.API') as api:
            api.return_value.request.return_value = {'status': 'in_progress'}
            self.assertFalse(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The baseline is measured.')['accepted'])
            self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
            api.return_value.request.return_value = {'status': 'done'}
            self.assertEqual(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The baseline is measured.')['status'], 'tested')
        self.assertEqual(self.run_sop('continue')['status'], 'idea')
        improved = self.solution(.2)
        self.assertEqual(self.run_sop('test', '--description', 'An improved candidate.')['status'], 'review')
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
        self.assertEqual(self.run_sop('enter')['status'], 'review')
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('test', '--description', 'Cannot skip the pending test.')['accepted'])

    def test_test_failure_enters_review_and_never_invents_a_score(self):
        self.run_sop()
        self.run_sop('enter')
        self.solution(.9)
        self.settings['benchmark']['evaluate_argv'] = [sys.executable, '-c', 'import sys; sys.exit(7)', '{solution}']
        self.save_settings()
        result = self.run_sop('test', '--description', 'A baseline with a failing evaluator.')
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['data']['returncode'], 7)
        self.assertEqual(self.run_sop('record', '--review', 'The evaluator failed; its logs identify the blocker.')['status'], 'test_failed')
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])

    def test_cli_warning_continue_and_exception_use_the_same_test_job(self):
        from multica_aide import monitor
        self.run_sop()
        self.run_sop('enter')
        self.solution(.9)
        self.settings['benchmark']['evaluate_argv'] = [sys.executable, '-c',
            "import sys,time; print('WARNING: slow test',file=sys.stderr,flush=True); time.sleep(30)", '{solution}']
        self.save_settings()
        result = self.run_sop('test', '--description', 'A long experiment.')
        self.assertEqual(result['status'], 'test')
        job = self.progress()['test_job']
        self.addCleanup(monitor.stop, job)
        with patch.object(monitor, 'INTERVALS', (.001,)):
            result = self.run_sop('continue')
        self.assertEqual(result['status'], 'test')
        record = read(self.skill / '.state/native-test-task.json')
        self.assertEqual(record['monitor']['checkpoint'], 1)
        self.assertNotIn('WARNING', str(result['data']['new_output']))
        result = self.run_sop('exception')
        self.assertEqual(result['status'], 'review')
        self.assertTrue(result['data']['exception'])
        self.assertEqual(self.progress()['test_job'], job)

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

    def test_existing_development_resumes_without_preparing_another_workdir(self):
        artifact = self.root / 'baseline'
        artifact.mkdir()
        (artifact / 'submission.json').write_text('{"loss": 0.9}')
        search(self.config).register_baseline('baseline', 'Baseline', 'Measured.',
            evaluate(self.config, artifact), artifact=str(artifact))
        candidate = self.root / 'candidate'
        candidate.mkdir()
        progress = self.progress()
        progress.update(candidate=str(candidate), baseline=False, development_issue={'id': 'development'})
        write(self.workdir / '.aide-client.json', progress)
        with patch('multica_aide.campaign.development_helper', return_value=self.root / 'absent'):
            self.assertEqual(self.run_sop()['data']['route'], 'idea')
            self.assertEqual(self.run_sop('enter')['status'], 'idea')
        self.develop.assert_not_called()

    def test_missing_contract_blocks_and_notifies_parent_at_initialization(self):
        self.contract.unlink()
        self.assertEqual(self.run_sop()['data']['route'], 'needs_setup')
        self.assertEqual(self.run_sop('enter')['status'], 'needs_setup')
        self.assertEqual(self.issue['status'], 'blocked')
        notification = next(c for c in self.calls if c[:3] == ('comment', 'add', 'parent'))
        self.assertIn('mention://agent/installer', notification[-1])
        self.assertIn('check is failed, failed to enter init state', notification[-1])


if __name__ == '__main__':
    unittest.main()
