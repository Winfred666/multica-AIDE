import json
from contextlib import chdir, redirect_stderr
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml
from multica_aide import sop_runtime
from multica_aide.standalone import SKILLS, configured_agent, evaluate, install, load_config, read, search, write
from multica_aide.worktrees import add_worktree, checkpoint, git, prepare_template, sandbox


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
            'template': str(self.root / 'template'), 'workdirs': str(self.root / 'workdirs'), 'multica': {'runtime_id': None},
            'benchmark': {'description': 'Improve the benchmark loss.',
                'public_data': str(public), 'private_paths': [], 'contract': str(self.contract),
                'evaluate_argv': [sys.executable, str(evaluator), '{solution}']}}
        self.save_settings()
        self.workdir = self.root / 'workdirs/client-1'
        self.workdir.parent.mkdir()
        commit = prepare_template(self.config, SKILLS)
        add_worktree(self.config, self.workdir, 'client-1', commit)
        write(self.workdir / '.aide-client.json', {'config_path': str(self.config_path), 'session_id': 'client-1',
            'completed': 0, 'candidate': None, 'node_id': None, 'finished': False, 'template_commit': commit})
        self.issue = {'id': 'client-issue', 'workspace_id': 'workspace', 'parent_issue_id': 'parent',
                      'status': 'in_progress', 'metadata': {'query_workdir': str(self.workdir), 'aide_client_workdir': str(self.workdir)}}
        self.calls = []
        native = patch.object(sop_runtime, 'multica', self.cli)
        native.start()
        self.addCleanup(native.stop)

    def test_installer_runs_controller_in_the_calling_task(self):
        from multica_aide import campaign
        result = {'status': 'completed', 'node': {'id': 'measured'}}
        with patch.object(campaign, 'installer_config', return_value=self.config), \
                patch('multica_aide.standalone.begin', return_value=result) as begin:
            self.assertEqual(campaign.start_install(None, {}), {'data': result})
        begin.assert_called_once_with(self.config)

    def save_settings(self):
        self.settings.setdefault('AIDE_val_dev', 'Iteration agent')
        self.config_path.write_text(yaml.safe_dump(self.settings))
        self.config = load_config(self.config_path)

    def cli(self, *words):
        self.calls.append(words)
        if words[0] == 'get':
            return {'id': 'parent', 'assignee_id': 'installer'} if words[1] == 'parent' else self.issue
        if words[0] == 'status': self.issue['status'] = words[2]; return self.issue
        if words[:2] == ('comment', 'add'): return {'id': 'comment'}
        if words[:2] == ('metadata', 'set'):
            self.issue['metadata'][words[words.index('--key') + 1]] = words[words.index('--value') + 1]
            return {}
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
        prepared = self.root / 'prepared.log'
        self.settings['prepare_argv'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; '
            f'Path({str(prepared)!r}).open("a").write(sys.argv[1]+"\\n")', '{solution}']
        self.save_settings()
        self.assertEqual(self.run_sop()['status'], 'initialize')
        self.assertFalse(self.run_sop('record', '--delivery', '{}', '--review', 'Premature.')['accepted'])
        self.assertEqual(search(self.config).snapshot()['nodes'], [])
        idea = self.run_sop('enter')
        self.assertEqual(idea['status'], 'idea')
        self.assertEqual(idea['data']['mode'], 'val_dev')
        self.assertTrue(idea['data']['baseline'])
        self.assertEqual(idea['data']['tree']['nodes'], [])
        self.assertFalse(self.run_sop('test', '--description', 'A minimal template baseline.')['accepted'])
        delivery = self.solution(.9)
        test = self.run_sop('test', '--description', 'A minimal template baseline.')
        self.assertEqual(test['status'], 'test')
        test = self.run_sop('continue')
        self.assertEqual(test['status'], 'review')
        self.assertEqual(test['data']['delivery'], delivery)
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'Two\n\nParagraphs.')['accepted'])
        self.assertEqual(self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The baseline is measured.')['status'], 'tested')
        self.assertEqual(self.run_sop('continue')['status'], 'idea')
        improved = self.solution(.2)
        self.assertEqual(self.run_sop('test', '--description', 'An improved candidate.')['status'], 'test')
        self.assertEqual(self.run_sop('continue')['status'], 'review')
        self.assertEqual(self.run_sop('record', '--delivery', json.dumps(improved), '--review', 'The measured loss meets the goal.')['status'], 'tested')
        result = self.run_sop('continue')
        self.assertTrue(result['finished'])
        self.assertEqual(result['data']['node']['delivery'], improved)
        self.assertEqual(result['data']['node']['score'], .2)
        self.assertEqual(self.issue['status'], 'in_progress')
        self.assertGreaterEqual(result['data']['node']['test_duration_seconds'], 0)
        self.assertEqual(len(search(self.config).snapshot()['nodes']), 2)
        self.assertTrue(all(c[0] in {'get', 'metadata'} for c in self.calls))
        nodes = search(self.config).snapshot()['nodes']
        for node in nodes:
            self.assertTrue((Path(node['artifact']) / '.git').is_file())
            self.assertEqual(git(node['artifact'], 'rev-parse', 'HEAD'), node['metadata']['git_commit'])
        self.assertEqual(json.loads((Path(nodes[0]['artifact']) / 'submission.json').read_text())['loss'], .9)
        self.assertEqual(prepared.read_text().splitlines(), [n['artifact'] for n in nodes])
        self.assertTrue((Path(nodes[1]['artifact']) / '.aide-client.json').is_symlink())
        from multica_aide import campaign
        self.issue['metadata'] = {}
        with chdir(nodes[1]['artifact']):
            self.assertEqual(campaign.client({'cli': self.cli, 'issue_id': self.issue['id']})[1], self.workdir)

    def test_refinement_branches_from_selected_commit_and_preserves_scored_worktrees(self):
        self.settings['drafts'] = 1
        self.save_settings()
        self.run_sop()
        self.run_sop('enter')
        for loss in (.9, .6):
            delivery = self.solution(loss)
            self.run_sop('test', '--description', 'Improve the measured solution.')
            self.run_sop('continue')
            self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The measured solution is valid.')
            self.run_sop('continue')
        progress = self.progress()
        selected = progress['round']['restore_node']
        self.assertEqual(progress['round']['mode'], 'refine')
        self.assertEqual(git(progress['candidate'], 'rev-parse', 'HEAD'), selected['metadata']['git_commit'])
        self.assertEqual(json.loads((Path(progress['candidate']) / 'submission.json').read_text())['loss'], .6)
        self.solution(.2)
        self.assertEqual(json.loads((Path(selected['artifact']) / 'submission.json').read_text())['loss'], .6)

    def test_record_rejects_changes_after_measurement(self):
        self.run_sop()
        self.run_sop('enter')
        delivery = self.solution(.9)
        self.run_sop('test', '--description', 'A measured solution.')
        self.assertEqual(self.run_sop('continue')['status'], 'review')
        self.solution(.2)
        result = self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'The candidate was edited.')
        self.assertFalse(result['accepted'])
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])

    def test_separate_mode_reuses_the_installed_child_and_waits_for_process_cleanup(self):
        from multica_aide import campaign
        self.settings.update(mode='separate', AIDE_solution_validator='Mika', AIDE_solution_developer='Mika')
        self.save_settings()
        progress = self.progress()
        progress.update(developer_issue_id='developer', developer_agent_id='mika', sandbox_policy={'deny_paths': []})
        write(self.workdir / '.aide-client.json', progress)
        api = Mock()
        status, runs = 'in_progress', []

        def request(method, path, body=None):
            if method == 'GET':
                return list(runs) if path.endswith('/task-runs') else {'id': 'developer', 'status': status}
            if method == 'POST' and path == '/api/issues':
                self.fail('Iterations must reuse the installed child')
            return {}

        api.request.side_effect = request
        with patch.object(campaign, 'API', return_value=api):
            self.run_sop()
            self.run_sop('enter')
            self.assertFalse(self.run_sop('test', '--description', 'Skip the developer.')['accepted'])
            self.assertEqual(self.run_sop('develop', '--description', 'Improve the public solution.')['status'], 'developing')
            self.solution(.9)
            status, runs = 'in_review', [{'status': 'completed', 'result': {'process_cleanup_pending': True}}]
            self.assertEqual(self.run_sop('continue')['status'], 'developing')
            runs = []
            result = self.run_sop('continue')
            self.assertEqual(result['status'], 'test')
            result = self.run_sop('continue')
            self.assertEqual(result['status'], 'review')
            delivery = result['data']['delivery']
            self.run_sop('record', '--delivery', json.dumps(delivery), '--review', 'Validation measured this delivery.')
            self.run_sop('continue')
            self.assertEqual(self.run_sop('develop', '--description', 'Improve again.')['status'], 'developing')
        self.assertEqual(self.progress()['developer_issue_id'], 'developer')

    def test_existing_untested_baseline_enters_test_without_an_idea_round(self):
        (Path(self.config['template']) / 'submission.json').write_text('{"loss": 0.9}')
        commit = checkpoint(self.config['template'], 'Prepared baseline')
        progress = self.progress()
        progress['template_commit'] = commit
        write(self.workdir / '.aide-client.json', progress)
        self.assertEqual(self.run_sop()['data']['route'], 'test')
        self.assertEqual(self.run_sop('enter')['status'], 'test')
        self.assertEqual(self.run_sop('continue')['status'], 'review')
        self.assertIsNone(search(self.config).snapshot()['nodes'][0]['score'])
        self.assertFalse(self.run_sop('test', '--description', 'Cannot skip the pending test.')['accepted'])

    def test_test_failure_enters_review_and_never_invents_a_score(self):
        self.run_sop()
        self.run_sop('enter')
        self.solution(.9)
        self.settings['benchmark']['evaluate_argv'] = [sys.executable, '-c', 'import sys; sys.exit(7)', '{solution}']
        self.save_settings()
        result = self.run_sop('test', '--description', 'A baseline with a failing evaluator.')
        self.assertEqual(result['status'], 'test')
        result = self.run_sop('continue')
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
        result = self.run_sop('continue')
        self.assertEqual(result['status'], 'test')
        self.assertIn('WARNING', str(result['data']['new_output']))
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

    def test_existing_candidate_resumes_in_its_workdir(self):
        artifact = self.root / 'baseline'
        artifact.mkdir()
        (artifact / 'submission.json').write_text('{"loss": 0.9}')
        search(self.config).register_baseline('baseline', 'Baseline', 'Measured.',
            evaluate(self.config, artifact), artifact=str(artifact))
        candidate = self.root / 'candidate'
        candidate.mkdir()
        progress = self.progress()
        progress.update(candidate=str(candidate), baseline=False)
        write(self.workdir / '.aide-client.json', progress)
        self.assertEqual(self.run_sop()['data']['route'], 'idea')
        self.assertEqual(self.run_sop('enter')['status'], 'idea')
        self.assertEqual(self.progress()['candidate'], str(candidate))

    def test_missing_contract_blocks_and_notifies_parent_at_initialization(self):
        self.contract.unlink()
        self.assertEqual(self.run_sop()['data']['route'], 'needs_setup')
        self.assertEqual(self.run_sop('enter')['status'], 'needs_setup')
        self.assertEqual(self.issue['status'], 'blocked')
        notification = next(c for c in self.calls if c[:3] == ('comment', 'add', 'parent'))
        self.assertIn('mention://agent/installer', notification[-1])
        self.assertIn('check is failed, failed to enter init state', notification[-1])


class AgentAssignment(unittest.TestCase):
    def test_controller_resumes_validator_once_after_developer_cleanup(self):
        from multica_aide import standalone
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {'state': str(root), 'poll_seconds': 0}
            client = {'session_id': 'client-1', 'issue_id': 'validator', 'agent_id': 'mika', 'workdir': str(root)}
            write(root / 'installation.json', {'config': config, 'workspace_id': 'workspace', 'server_url': 'server', 'clients': [client]})
            write(root / '.aide-client.json', {'completed': 0, 'candidate': 'solution',
                  'development_pending': True, 'developer_issue_id': 'developer'})
            api = Mock(workspace='workspace', base='server')
            cleanup = [True, False, False]
            def request(method, path, body=None):
                if path.endswith('/task-runs'):
                    return [{'status': 'completed', 'result': {'process_cleanup_pending': cleanup.pop(0)}}]
                return {'status': 'in_review' if path.endswith('/developer') else 'in_progress'}
            api.request.side_effect = request
            tree = Mock()
            tree.snapshot.return_value = {'nodes': []}
            def resumed(*args):
                progress = read(root / '.aide-client.json')
                progress['development_pending'] = False
                write(root / '.aide-client.json', progress)
            with patch.object(standalone, 'API', return_value=api), \
                    patch.object(standalone, 'search', return_value=tree), \
                    patch.object(standalone, 'completed_node', side_effect=[None, None, None, {'score': 0}]), \
                    patch.object(standalone, 'wake', side_effect=resumed) as wake:
                self.assertEqual(standalone.begin(config)['status'], 'completed')
            self.assertEqual(wake.call_count, 1)
            self.assertEqual(cleanup, [False])
            self.assertIn('Developer delivery is ready', wake.call_args.args[2])

    def test_both_modes_install_with_one_existing_agent_and_no_shared_harness_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / 'private'
            private.mkdir()
            (private / 'answers.txt').write_text('private answer')
            contract = private / 'contract.py'
            contract.write_text('def validate(solution): return True\ndef score(delivery): return 1\ndef is_complete(delivery): return False\n')
            api = Mock(workspace='workspace', base='server')
            issues, properties = [], {}

            def request(method, path, body=None):
                if method == 'GET' and path == '/api/agents?include_archived=true':
                    return [{'id': 'mika', 'name': 'Mika'}]
                if method == 'GET' and path == '/api/issues/parent':
                    return {'id': 'parent', 'assignee_id': 'mika', 'project_id': None}
                if method == 'POST' and path == '/api/issues':
                    issue = {**body, 'id': str(len(issues))}
                    issues.append(issue)
                    return issue
                if method == 'POST' and path.endswith('/labels'):
                    return {}
                if method == 'PUT' and '/metadata/' in path:
                    properties[path] = body['value']
                    return {}
                if method == 'PUT' and path.startswith('/api/issues/'):
                    self.assertEqual(body['assignee_id'], 'mika')
                    return {}
                self.fail('Unexpected workspace mutation: ' + method + ' ' + path)

            api.request.side_effect = request
            with patch('multica_aide.standalone.API', return_value=api), \
                    patch('multica_aide.standalone.label', return_value='dev'):
                for mode in ('val_dev', 'separate'):
                    directory = root / mode
                    directory.mkdir()
                    template = directory / 'template'
                    template.mkdir()
                    git(template, 'init', '--quiet', '-b', 'benchmark-feature')
                    settings = {'mode': mode, 'AIDE_val_dev': 'Mika', 'AIDE_solution_validator': 'Mika',
                        'AIDE_solution_developer': 'Mika', 'drafts': 2, 'clients': 2, 'port': None,
                        'template': 'template', 'state': 'state', 'tree': 'tree', 'workdirs': 'workdirs',
                        'multica': {'runtime_id': 'runtime'}, 'benchmark': {'description': 'Public benchmark task.',
                            'contract': str(contract), 'public_data': str(directory), 'private_paths': [str(private)],
                            'evaluate_argv': [sys.executable, str(contract), '{solution}']}}
                    configfile = directory / 'config.yaml'
                    configfile.write_text(yaml.safe_dump(settings))
                    config = load_config(configfile)
                    before = len(issues)
                    self.assertEqual(install(config, 'parent')['status'], 'ready')
                    self.assertEqual(len(issues), before)
                    installed = install(config, 'parent', confirm=True)
                    self.assertEqual(len(issues) - before, 2 if mode == 'val_dev' else 4)
                    self.assertEqual(install(config, 'parent', confirm=True), installed)
                    self.assertEqual(git(template, 'branch', '--show-current'), 'benchmark-feature')
                    self.assertEqual(git(template, 'branch', '--list', 'main'), '')
                    worktrees = git(template, 'worktree', 'list', '--porcelain')
                    self.assertEqual(sum(line.startswith('worktree ') for line in worktrees.splitlines()),
                                     3 if mode == 'val_dev' else 5)
                    for client in installed['clients']:
                        workdir = Path(client['workdir'])
                        self.assertEqual(git(workdir, 'rev-parse', 'HEAD'), installed['template_commit'])
                        self.assertEqual(git(workdir, 'show', 'HEAD:.agents/skills/multica-aide-cli/SKILL.md'),
                                         (workdir / '.agents/skills/multica-aide-cli/SKILL.md').read_text().strip())
                        if mode == 'separate':
                            child = read(workdir / '.aide-client.json')['developer_issue_id']
                            self.assertEqual(issues[int(child)]['parent_issue_id'], client['issue_id'])
                            self.assertIn(str(private), json.loads(properties['/api/issues/' + child + '/metadata/sandbox_policy'])['deny_paths'])

    def test_bubblewrap_hides_private_validation_and_keeps_development_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, private = root / 'candidate', root / 'private'
            candidate.mkdir()
            private.mkdir()
            (private / 'answer').write_text('locked')
            code = ("from pathlib import Path; import sys; "
                    "p=Path(sys.argv[1]); assert not (p/'answer').exists(); "
                    "Path('submission.json').write_text('{}')")
            subprocess.run(sandbox({'deny_paths': [str(private), str(private / 'answer')]}, candidate,
                                   [sys.executable, '-c', code, str(private)]), check=True, capture_output=True)
            self.assertEqual((candidate / 'submission.json').read_text(), '{}')
            self.assertEqual((private / 'answer').read_text(), 'locked')

    def test_missing_named_agent_warns_once_then_reuses_existing_settings(self):
        api, agents, warnings = Mock(), [], StringIO()

        def request(method, path, body=None):
            if method == 'GET':
                return agents
            self.assertEqual((method, path), ('POST', '/api/agents'))
            self.assertIn('WARNING', warnings.getvalue())
            agents.append({**body, 'id': 'created'})
            return agents[-1]

        api.request.side_effect = request
        with redirect_stderr(warnings):
            first = configured_agent(api, 'Worker', 'runtime', {'model': 'configured-model'})
            second = configured_agent(api, 'Worker', 'runtime', {'model': 'another-model'})
        self.assertIs(first, second)
        self.assertEqual(first['model'], 'configured-model')
        self.assertEqual(warnings.getvalue().count('WARNING'), 1)
        self.assertEqual(len(agents), 1)

    def test_archived_agent_is_restored_and_duplicate_names_do_not_create_more_agents(self):
        api = Mock()
        archived = {'id': 'existing', 'name': 'Worker', 'archived_at': 'yesterday'}
        api.request.return_value = [archived]
        self.assertEqual(configured_agent(api, 'Worker', 'runtime', {}), archived)
        api.request.assert_any_call('POST', '/api/agents/existing/restore', {})
        api.reset_mock()
        api.request.return_value = [archived, {**archived, 'id': 'duplicate'}]
        with self.assertRaisesRegex(ValueError, 'Multiple agents named'):
            configured_agent(api, 'Worker', 'runtime', {})
        self.assertTrue(all(c.args[0] == 'GET' for c in api.request.call_args_list))


if __name__ == '__main__':
    unittest.main()
