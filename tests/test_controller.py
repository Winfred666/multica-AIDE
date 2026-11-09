import contextlib
import importlib.util
import io
import json
import shlex
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TEMPLATE = Path(__file__).resolve().parents[1] / "multica_aide"
SPEC = importlib.util.spec_from_file_location("symbolic_sop_wrapper", TEMPLATE / "sop_runtime.py")
sop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sop)


class ControllerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copy(TEMPLATE / 'sop_runtime.py', self.root / 'wrapper.py')
        shutil.copy(TEMPLATE / 'sop.py', self.root / 'sop.py')
        shutil.copy(TEMPLATE / 'monitor.py', self.root / 'monitor.py')
        (self.root / 'config.json').write_text('{"DEBUG": false}')
        shutil.copy(Path(__file__).parent / 'fixtures/review_controller.py', self.root / 'controller.py')
        (self.root / 'SKILL.md').write_text('---\nname: review-child-work\ndescription: Review a child delivery.\n---\n')
        self.addCleanup(patch.stopall)
        patch.object(sop, "ROOT", self.root).start()
        self.env = {"MULTICA_TASK_ID": "task-one", "MULTICA_ISSUE_ID": "parent-one", "MULTICA_AGENT_ID": "agent-one",
                    "MULTICA_WORKSPACE_ID": "workspace-one", "SYMBOLIC_SOP_ERROR_REPO": "example/sop-debug"}
        patch.dict(os.environ, self.env, clear=True).start()
        self.github_calls = []
        self.private = True
        self.github = patch.object(sop, "github", side_effect=self.fake_github).start()
        self.issue = {"id": "parent-one", "workspace_id": "workspace-one", "status": "in_progress"}
        self.child = {"id": "child-one", "parent_issue_id": "parent-one", "workspace_id": "workspace-one",
                      "status": "in_review", "assignee_type": "agent", "assignee_id": "child-agent"}
        self.cli_calls = []
        patch.object(sop, "multica", side_effect=self.fake_multica).start()

    def fake_multica(self, *args):
        self.cli_calls.append(args)
        if args == ("get", "parent-one"):
            return self.issue.copy()
        if args == ("get", "child-one"):
            return self.child.copy()
        if args == ("children", "parent-one"):
            return {"stages": [{"stage": 1, "issues": [self.child.copy()]}], "unstaged": []}
        if args[:3] == ("status", "child-one", "done"):
            self.child["status"] = "done"
            return self.child.copy()
        if args[:3] == ("comment", "add", "child-one"):
            return {"id": "child-comment"}
        raise AssertionError(f"Unexpected Multica command: {args!r}")

    def fake_github(self, *args, payload=None):
        self.github_calls.append((args, payload))
        if args[0] == "repos/" + os.environ["SYMBOLIC_SOP_ERROR_REPO"]:
            return {"private": self.private, "full_name": os.environ["SYMBOLIC_SOP_ERROR_REPO"]}
        return {"number": 1, "html_url": "https://github.com/example/sop-debug/issues/1"}

    def call(self, *argv, plain=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = sop.main(list(argv))
        if plain:
            self.assertEqual(code, 0)
            return output.getvalue().strip()
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertEqual(next(iter(result)), 'status')
        self.assertFalse({'skill', 'initial_status', 'ok', 'finished', 'command', 'alternatives', 'argv', 'debug', 'reporting'} & result.keys())
        if result['status'] != 'valid':
            self.assertIsInstance(result['next_step'], list)
            for option in result['next_step']:
                self.assertEqual(set(option), {'instruction', 'command'})
        self.assertEqual(code, 0 if argv == ('--help',) or 'error' not in result else 1)
        return result

    def record_path(self, task=None):
        return self.root / '.state' / ((task or os.environ['MULTICA_TASK_ID']) + '.json')

    def state(self):
        path = self.record_path()
        return path.read_bytes() if path.exists() else b''


    def assert_progress_unchanged(self, before):
        # Emitted decisions are cached even when an option is rejected.
        def progress(raw):
            if not raw:
                return {}
            state = json.loads(raw)
            for name in ('output', 'decisions', 'option_diagrams', 'option_targets'):
                state.pop(name, None)
            return state
        self.assertEqual(progress(self.state()), progress(before))


    def test_reports_stay_local_until_destination_is_explicitly_configured(self):
        with patch.dict(os.environ, {"SYMBOLIC_SOP_ERROR_REPO": ""}):
            result = self.call("--error", "permission denied")
        self.assertEqual(self.github_calls, [])
        self.assertTrue(list((self.root / ".errors").glob("*.json")))
        # The queued incident's previous destination cannot override the current opt-in.
        result = self.call()
        self.assertEqual(list((self.root / '.errors').glob('*.json')), [])

    def test_lifecycle_and_callbacks_are_independent_of_cwd(self):
        start = self.call()
        self.assertEqual(shlex.split(start['next_step'][0]['command'])[2:], ['done', '--child', '<child>'])
        self.assertEqual(start['data']['children'][0]['id'], 'child-one')
        self.assertEqual(self.call()['status'], 'review')
        before = self.state()
        invalid = self.call('done')
        self.assertEqual(invalid['error']['code'], 'invalid_input')
        self.assertIn('--child', invalid['next_step'][0]['command'])
        self.assert_progress_unchanged(before)
        resteered = self.call('resteer', '--child', 'child-one', '--prompt', 'Repair the failing check')
        self.assertEqual(resteered['status'], 'resteer')
        self.assertEqual(resteered['next_step'], [])
        self.assertEqual(self.state(), b'')
        writes = [c for c in self.cli_calls if c[0] in {'status', 'comment'}]
        repeated = self.call('resteer', '--child', 'child-one', '--prompt', 'Repair the failing check')
        self.assertEqual(repeated['error']['code'], 'invalid_transition')
        self.assertEqual([c for c in self.cli_calls if c[0] in {'status', 'comment'}], writes)
        self.assertFalse(any(c[0] == 'wakeup' for c in self.cli_calls))
        self.assertEqual(self.call('init')['status'], 'review')
        done = self.call('done', '--child', 'child-one')
        self.assertEqual(done['status'], 'finished')
        self.assertEqual(done['next_step'], [])
        self.assertEqual(self.state(), b'')
        self.assertEqual(self.child['status'], 'done')
        self.assertEqual(self.issue['status'], 'in_progress')
        count = len([c for c in self.cli_calls if c[0] == 'status'])
        self.assertEqual(self.call('done', '--child', 'child-one')['error']['code'], 'invalid_transition')
        self.assertEqual(len([c for c in self.cli_calls if c[0] == 'status']), count)

    def test_unpersisted_completion_fails_without_advancing_even_without_debug(self):
        for debug in ('true', 'false'):
            with self.subTest(debug=debug), patch.dict(os.environ, {'DEBUG': debug}):
                self.record_path().unlink(missing_ok=True)
                initial = self.call()
                state = self.state()

                def acknowledge_without_writing(*args):
                    if args[:3] == ('status', 'child-one', 'done'):
                        return {**self.child, 'status': 'done'}
                    return self.fake_multica(*args)

                with patch.object(sop, 'multica', side_effect=acknowledge_without_writing):
                    failed = self.call('done', '--child', 'child-one')
                self.assertIn('error', failed)
                self.assertEqual(failed['error']['code'], 'assertion_failed')
                self.assertIn('not persisted', failed['error']['message'])
                self.assertEqual(failed['status'], "error")
                self.assertNotEqual(self.state(), state)

    def test_debug_metadata_is_ignored_and_copy_flags_are_rejected(self):
        self.issue['metadata'] = {'DEBUG': True, 'aide_replay_target': 'review-child-work'}
        started = self.call()
        self.assertEqual(started['status'], "review")
        self.assertNotIn('--debug', shlex.split(started['next_step'][0]['command']))
        before = self.state()
        for args in [('--debug',), ('--restore', 'snapshot-id'), ('--parent', 'evolver')]:
            with self.subTest(args=args):
                failed = self.call(*args)
                self.assertIn('error', failed)
                self.assert_progress_unchanged(before)
        self.assertEqual(self.call('--validate'), {'status': 'valid'})

    def test_help_never_creates_or_changes_task_state(self):
        body = (self.root / 'SKILL.md').read_text().split('---', 2)[2].strip()
        self.assertEqual(self.call('--help', plain=True), body)
        self.assertEqual(self.state(), b'')
        started = self.call()
        before = self.state()
        self.assertEqual(self.call('--help'), started)
        self.assertEqual(self.state(), before)

    def test_quit_is_injected_reported_and_removes_the_task_record(self):
        started = self.call()
        choice = started['next_step'][-1]
        self.assertEqual(shlex.split(choice['command'])[2:], ['quit'])
        self.assertNotIn("Option('quit'", (self.root / 'controller.py').read_text())
        result = self.call('quit')
        self.assertEqual(result['status'], 'quitted')
        self.assertEqual(result['instruction'], 'You are in SOP state "quitted". SoP review-child-work quitted')
        self.assertEqual(result['error']['code'], 'quitted')
        self.assertEqual(result['next_step'], [])
        self.assertFalse(self.record_path().exists())
        self.assertFalse(any(c[0] in {'status', 'comment'} for c in self.cli_calls))
        report = json.loads(self.github_calls[-1][1]['body'].split('\n\n', 1)[1])
        self.assertEqual(report['code'], 'quitted')
        self.assertEqual(report['argv'], ['quit'])
        self.assertEqual(report['state']['status'], 'review')
        self.assertIn('SOPError: SoP review-child-work quitted', report['traceback'])
        self.assertEqual(self.call()['status'], 'review')

    def test_quit_keeps_its_report_queued_when_github_is_unavailable(self):
        self.call()
        with patch.object(sop, 'github', side_effect=ConnectionError('offline')):
            result = self.call('quit')
        self.assertEqual(result['error']['code'], 'quitted')
        self.assertFalse(self.record_path().exists())
        reports = list((self.root / '.errors').glob('*.json'))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())['code'], 'quitted')

    def test_invalid_calls_are_json_failures_without_new_rows(self):
        for argv in [("nonsense",), ("--unknown",), ("done", "--decline"),
                     ("--expect", "review@" + "a" * 32), ("done", "--expect", "bad"), ]:
            with self.subTest(argv=argv):
                before = self.state()
                self.assertIn('error', self.call(*argv))
                self.assertEqual(self.state(), before)
        with patch.dict(os.environ, {"MULTICA_TASK_ID": ""}):
            self.assertEqual(self.call()["error"]["code"], "missing_task_id")
        with patch.dict(os.environ, {"MULTICA_ISSUE_ID": ""}):
            self.assertEqual(self.call()["error"]["code"], "missing_issue_id")

    def test_options_require_init_and_invalid_options_return_current_guidance(self):
        before = self.state()
        failed = self.call('done', '--child', 'child-one')
        self.assertEqual(failed['error']['code'], 'invalid_transition')
        self.assert_progress_unchanged(before)
        self.assertEqual(failed['instruction'], 'Inspect a child deliverable before accepting it or requesting a correction.')
        self.call()
        before = self.state()
        failed = self.call('unknown')
        self.assertEqual(failed['error']['code'], 'invalid_transition')
        self.assertTrue(any('resteer' in choice['command'] for choice in failed['next_step']))
        self.assertIn('--child', failed['next_step'][0]['command'])
        self.assert_progress_unchanged(before)

    def test_task_rows_are_isolated(self):
        first = self.call()["status"]
        with patch.dict(os.environ, {"MULTICA_TASK_ID": "task-two"}):
            second = self.call()["status"]
        self.assertEqual(self.call()["status"], first)
        self.assertEqual(first, second)
        self.assertEqual({p.stem for p in (self.root / '.state').glob('*.json')}, {'task-one', 'task-two'})

    def test_custom_sop_uses_its_own_controller_and_assertions(self):
        wrapper = (self.root / "wrapper.py").read_bytes()
        (self.root / "controller.py").write_text(
            "from sop import State, Option, Parameter, Workflow\n"
            "def check(context):\n"
            "    value = context['cli']('get', context['issue_id'])\n"
            "    if value['workspace_id'] != context['workspace_id']:\n        raise AssertionError('workspace changed')\n"
            "    return value\n"
            "def advance(args, context):\n    return {'data': args.value}\n"
            "workflow = Workflow(\n"
            " State('inspect', 'Inspect the result.', initial=True, precheck=check, options=(\n"
            "  Option('advance', 'confirm', 'Confirm the result.', advance, params=(Parameter('--value'),)),)),\n"
            " State('confirm', 'Confirm the evidence.', precheck=check, options=(Option('finish', 'finished', 'Finish.'),)),\n"
            " State('finished', 'Stop.', final=True, precheck=check))\n")
        initial = self.call()
        advanced = self.call("advance", "--value", "observed-result")
        self.assertEqual(advanced["status"], "confirm")
        self.assertEqual(advanced["data"], "observed-result")
        self.assertNotIn('`quit`', advanced['instruction'])
        self.assertEqual(self.call('quit')['error']['code'], 'invalid_transition')
        self.assertEqual(self.call(*shlex.split(advanced['next_step'][0]['command'])[2:])['status'], 'finished')
        self.assertEqual((self.root / "wrapper.py").read_bytes(), wrapper)
        self.assertTrue(all(call == ('get', 'parent-one') for call in self.cli_calls))

    def test_wrapper_enforces_handoffs_and_rejection_preserves_state(self):
        controller = sop.load_controller()
        with patch.object(sop, "load_controller", return_value=controller):
            with patch.object(controller.workflow, "step", return_value={"phase": "review", "accepted": False, "instruction": "Decline."}):
                self.assertIsNone(self.call()["status"])
                self.assertEqual(self.state(), b'')
            review = self.call()["status"]
            before = self.state()
            with patch.object(controller.workflow, "step", return_value={"phase": "waiting", "instruction": "Wait."}):
                self.assertIn('error', self.call())
                self.assert_progress_unchanged(before)
            with patch.object(controller.workflow, "step", return_value={"phase": "finished", "accepted": False, "instruction": "Stop."}):
                self.assertIn('error', self.call("done"))
                self.assert_progress_unchanged(before)

    def test_simple_object_workflow_needs_no_separate_assertion_module(self):
        (self.root / 'controller.py').write_text(
            "from sop import State, Option, Workflow\n"
            "workflow = Workflow(\n"
            " State('choose', 'Review the result.', initial=True, options=(Option('accept', 'finished', 'Accept the result.'),)),\n"
            " State('finished', 'Accepted.', final=True))\n")
        start = self.call()
        self.assertEqual(start['instruction'], 'You are in SOP state "choose". Review the result.')
        self.assertEqual(start['next_step'][0]['instruction'], 'Accept the result.')
        self.assertNotIn('This moves to', start['instruction'])
        self.assertEqual(self.call(*shlex.split(start['next_step'][0]['command'])[2:])['status'], 'finished')
        self.assertFalse((self.root / 'test.py').exists())

    def test_malformed_task_json_is_preserved(self):
        self.record_path().parent.mkdir()
        for content in ['{', '[]', '{}', '{"status":"unknown"}']:
            with self.subTest(content=content):
                self.record_path().write_text(content)
                before = self.state()
                self.assertEqual(self.call()['error']['code'], 'invalid_state')
                self.assertEqual(self.state(), before)

    def test_record_deletion_failure_preserves_progress_and_reports_the_write(self):
        work = self.call()["status"]
        before = self.state()
        unlink = Path.unlink

        def remove(path, **kwargs):
            if path == self.record_path():
                raise PermissionError('permission denied on simulated disk')
            return unlink(path, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=remove):
            result = self.call("resteer", "--child", "child-one", "--prompt", "Repair")
        self.assertIn('error', result)
        self.assertIn("access", result["instruction"])
        self.assertEqual(shlex.split(result['next_step'][0]['command']), [sys.executable, str(self.root / "wrapper.py")])
        self.assert_progress_unchanged(before)
        self.assertFalse(list(self.record_path().parent.glob(".task-one.json.*")))

    def test_task_record_preserves_additional_fields_on_resume(self):
        self.call()
        state = json.loads(self.state())
        state['observation'] = {'child': 'child-one'}
        self.record_path().write_text(json.dumps(state))
        self.assertEqual(self.call()['status'], 'review')
        self.assertEqual(json.loads(self.state()), state)

    def test_known_action_error_has_minimal_hint_and_full_redacted_report(self):
        self.call()
        before = self.state()
        with patch.dict(os.environ, {"GH_TOKEN": "ghp_fixtureSecret", "MULTICA_TOKEN": "mat_fixtureSecret"}):
            result = self.call("--error", "permission denied: GH_TOKEN=ghp_fixtureSecret MULTICA_TOKEN=mat_fixtureSecret")
        self.assert_progress_unchanged(before)
        self.assertIn("access", result["instruction"])
        self.assertEqual(self.call('--help'), result)
        self.assertNotIn("rules", result["error"])
        report = json.loads(self.github_calls[-1][1]["body"].split("\n\n", 1)[1])
        self.assertEqual(report["task_id"], "task-one")
        self.assertEqual(report["issue_id"], "parent-one")
        self.assertEqual(report["agent_id"], "agent-one")
        self.assertEqual(report["workspace_id"], "workspace-one")
        self.assertEqual(len(report["script_sha256"]), 64)
        self.assertIn("traceback", report)
        self.assertIn("argv", report)
        self.assertNotIn("fixtureSecret", json.dumps(report))

    def test_error_file_details_are_reported_without_creating_state(self):
        error_file = self.root / "agent-error.txt"
        error_file.write_text("unexpected runtime behavior\nsecond line")
        result = self.call("--error-file", str(error_file))
        self.assertIn("second line", self.github_calls[-1][1]["body"])
        self.assertEqual(self.state(), b'')

    def test_public_repository_refused_and_outbox_recovers(self):
        self.private = False
        result = self.call("--error", "permission denied")
        self.assertEqual(len(self.github_calls), 1)
        self.assertEqual(len(list((self.root / ".errors").glob("*.json"))), 1)
        self.private = True
        recovered = self.call()
        self.assertNotIn('error', recovered)
        self.assertEqual(list((self.root / ".errors").glob("*.json")), [])

    def test_report_transport_failure_cannot_rollback_work(self):
        self.github.side_effect = RuntimeError("connection timed out")
        self.call("--error", "failed")
        work = self.call()
        self.assertNotIn('error', work)
        self.assertIn(work["status"].encode(), self.state())

    def test_unwritable_outbox_keeps_agent_error_concise(self):
        (self.root / ".errors").write_text("blocked")
        result = self.call("--error", "failed")
        self.assertEqual(result["error"], {"code": "action_error", "message": "failed"})

    def test_large_reports_are_split_without_truncation(self):
        message = "diagnostic " * 4000
        self.call("--error", message)
        posts = [payload["body"].split("\n\n", 1)[1] for args, payload in self.github_calls if payload]
        self.assertGreater(len(posts), 1)
        report = json.loads("".join(posts))
        self.assertEqual(report["reported_error"], message)
        self.assertTrue(all(len(chunk) <= 18000 for chunk in posts))

    def test_secret_assignments_remain_valid_json_and_redacted(self):
        values = ["token=plain-secret", 'password: "plain-secret"', '{"api_key":"plain-secret"}',
                  'password="plain-secret with spaces"', "token='plain-secret with spaces'",
                  "Authorization: Bearer plain-secret", "Authorization: Basic plain-secret",
                  "Authorization: Bearer ghp_exampleCredential", "multi\nline secret=plain-secret"]
        for value in values:
            with self.subTest(value=value):
                redacted = sop.redact({"message": value})
                self.assertNotIn("plain-secret", redacted["message"])
                self.assertNotIn("with spaces", redacted["message"])
                self.assertNotIn("ghp_exampleCredential", redacted["message"])
        with patch.dict(os.environ, {"GH_TOKEN": "true"}):
            self.assertEqual(sop.redact({"task_id": "task-one", "ok": True, "code": "missing_task_id"}),
                             {"task_id": "task-one", "ok": True, "code": "missing_task_id"})

    def test_redaction_is_idempotent_through_queue_and_response(self):
        raw = {"message": 'password="correct horse battery staple" token=[REDACTED] safe details remain'}
        safe = sop.redact(raw)
        self.assertEqual(sop.redact(safe), safe)
        self.assertEqual(sop.redact(sop.redact(safe)), safe)

    def test_failure_callback_matches_the_required_recovery(self):
        result = self.call("--error", "permission denied")
        self.assertEqual(shlex.split(result['next_step'][0]['command']), [sys.executable, str(self.root / "wrapper.py")])
        with patch.object(sop, "transition", side_effect=RuntimeError("unexpected fault")):
            result = self.call("--error", "unexpected fault")
        self.assertEqual(shlex.split(result['next_step'][0]['command'])[-2:], ["--error", "unexpected fault"])

    def process_environment(self, task="task-one"):
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        fake = binary / "gh"
        if not fake.exists():
            fake.write_text(f"#!{sys.executable}\nimport json, sys\n"
                            "payload = json.load(sys.stdin) if '--input' in sys.argv else None\n"
                            "print(json.dumps({'private': True, 'full_name': 'example/sop-debug'} "
                            "if payload is None else {'number': 1, 'html_url': 'https://example.invalid/issues/1'}))\n")
            fake.chmod(0o700)
        cli = binary / "multica"
        if not cli.exists():
            cli.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
                           "args = sys.argv[2:]\n"
                           "state = Path(__file__).with_name('issue-status')\n"
                           "if args[0] == 'status': state.write_text(args[2])\n"
                           "result = {'stages': [], 'unstaged': []} if args[0] == 'children' else "
                           "{'id': args[1], 'workspace_id': 'workspace-one', 'parent_issue_id': 'parent-one', "
                           "'status': (state.read_text() if state.exists() else 'in_review') if args[1] == 'child-one' else 'in_progress'}\n"
                           "print(json.dumps(result))\n")
            cli.chmod(0o700)
        return {**self.env, "MULTICA_TASK_ID": task, "PATH": str(binary)}

    def test_entrypoint_with_fake_github_from_different_working_directory(self):
        env = self.process_environment()
        for argv in [[], ["--error", "permission denied"]]:
            result = subprocess.run([sys.executable, str(self.root / "wrapper.py"), *argv],
                                    cwd="/", env=env, capture_output=True, text=True, timeout=10)
            response = json.loads(result.stdout)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.returncode, 0 if 'error' not in response else 1)
        self.assertEqual(json.loads(self.state())['status'], 'review')

    def test_completion_allows_another_run_with_the_same_native_task(self):
        for _ in range(2):
            self.child['status'] = 'in_review'
            self.assertEqual(self.call()['status'], 'review')
            self.assertEqual(self.call('done', '--child', 'child-one')['status'], 'finished')
            self.assertFalse(self.record_path().exists())
        self.assertEqual(sum(c[:3] == ('status', 'child-one', 'done') for c in self.cli_calls), 2)

    def test_concurrent_tasks_keep_each_others_rows(self):
        command = [sys.executable, str(self.root / "wrapper.py")]
        tasks = [f"parallel-{i}" for i in range(6)]
        processes = [(task, subprocess.Popen(command, env=self.process_environment(task),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
                     for task in tasks]
        results = [(task, process.communicate(timeout=10)) for task, process in processes]
        for task, (output, error) in results:
            self.assertEqual(error, "")
            result = json.loads(output)
            self.assertNotIn('error', result)
        self.assertEqual({p.stem for p in (self.root / '.state').glob('*.json')}, set(tasks))
        for task in tasks:
            state = json.loads(self.record_path(task).read_text())
            self.assertEqual(state['status'], 'review')
            self.assertEqual(state['output']['status'], 'review')

    def test_broken_controller_returns_debug_json_without_losing_state(self):
        self.call()
        before = self.state()
        (self.root / "controller.py").write_text("def broken(:\n")
        failed = self.call()
        self.assertEqual(failed["error"]["code"], "controller_failure")
        self.assertEqual(self.state(), before)

    def test_child_scope_and_assignment_are_checked_before_mutation(self):
        token = self.call()["status"]
        before = self.state()
        for field, value in [("parent_issue_id", "different-parent"), ("workspace_id", "different-workspace"),
                             ("status", "done"), ("assignee_type", "member")]:
            with self.subTest(field=field), patch.dict(self.child, {field: value}):
                result = self.call("resteer", "--child", "child-one", "--prompt", "Correction")
                self.assertIn('error', result)
                self.assertEqual(result["error"]["code"], "invalid_child")
                self.assert_progress_unchanged(before)
        self.assertFalse(any(command[0] in ("wakeup", "status") for command in self.cli_calls))


if __name__ == "__main__":
    unittest.main()
