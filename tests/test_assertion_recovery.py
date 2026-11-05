"""Wrapper assertion recovery persists across calls without repeating effects."""
import json
import shlex
import os
import shutil
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import test_controller as fixtures


class AssertionRecovery(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ControllerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.wrapper = fixtures.sop
        old_root = self.fixture.root
        self.fixture.root = old_root / 'review-child-work'
        self.fixture.root.mkdir()
        for path in old_root.iterdir():
            if path != self.fixture.root:
                shutil.move(str(path), self.fixture.root / path.name)
        patch.object(self.wrapper, 'ROOT', self.fixture.root).start()
        self.controller = self.wrapper.load_controller()
        patch.object(self.wrapper, 'load_controller', return_value=self.controller).start()
        self.call = self.fixture.call

    def retry(self, failure):
        return self.call(*shlex.split(failure['next_step'][0]['command'])[2:])

    def test_initial_repair_resumes_begin_and_resume_does_not_retry(self):
        with patch.object(self.controller.workflow, 'before', side_effect=AssertionError('missing prerequisite')) as gate, \
                patch.object(self.controller.workflow, 'step', wraps=self.controller.workflow.step) as step:
            failed = self.call()
            self.assertEqual(failed['status'], "error")
            self.assertEqual(self.call()['status'], failed['status'])
            self.assertEqual(gate.call_count, 1)
            step.assert_not_called()
        fixed = self.retry(failed)
        self.assertEqual(fixed['status'], "review")
        self.assertNotIn('error', fixed)
        self.assertTrue(Path(failed['evidence']).is_file())
        self.assertEqual(self.retry(failed)['error']['code'], 'invalid_transition')

    def test_failed_repair_blocks_and_returns_fixture_request_without_retry_loop(self):
        target = self.fixture.root.parent / 'multica-surface-block'
        target.mkdir(exist_ok=True)
        (target / 'wrapper.py').write_text('# separate blocked SOP')
        with patch.object(self.controller.workflow, 'before', side_effect=AssertionError('still missing')) as gate:
            failed = self.call()
            blocked = self.retry(failed)
            self.assertEqual(blocked['status'], "blocked")
            self.assertIn('fixture', blocked['instruction'])
            self.assertEqual(shlex.split(blocked['next_step'][0]['command'])[1], str(target / 'wrapper.py'))
            self.assertEqual(self.call()['status'], blocked['status'])
            self.assertEqual(gate.call_count, 2)

    def test_blocked_sop_does_not_recurse_on_its_own_failed_repair(self):
        path = self.fixture.root / 'SKILL.md'
        path.write_text(path.read_text().replace('name: review-child-work', 'name: multica-surface-block'))
        with patch.object(self.controller.workflow, 'before', side_effect=AssertionError('blocked failed')):
            blocked = self.retry(self.call())
        self.assertEqual(blocked['status'], "blocked")
        self.assertEqual(blocked['next_step'], [])
        self.assertIn('parent', blocked['instruction'])

    def test_final_repair_removes_record_without_repeating_completion_write(self):
        start = self.call()
        self.assertEqual(start['status'], "review")
        original = self.fixture.fake_multica

        def dropped_write(*args):
            if args[:3] == ('status', 'child-one', 'done'):
                self.fixture.cli_calls.append(args)
                return {**self.fixture.child, 'status': 'done'}
            return original(*args)

        with patch.object(self.wrapper, 'multica', side_effect=dropped_write):
            failed = self.call('done', '--child', 'child-one')
        self.assertEqual(failed['status'], "error")
        saved = json.loads(Path(failed['evidence']).read_text())['recovery']
        self.assertEqual(saved['assertion']['boundary'], 'after')
        self.fixture.child['status'] = 'done'  # Manual repair of persisted state.
        with patch.object(self.controller.workflow, 'step', side_effect=AssertionError('controller replayed')):
            fixed = self.retry(failed)
        self.assertEqual(fixed['status'], 'finished')
        self.assertIn('state "finished".', fixed['instruction'])
        self.assertEqual(fixed['next_step'], [])
        self.assertEqual(self.fixture.state(), b'')
        self.assertEqual(sum(c[0] == 'status' for c in self.fixture.cli_calls), 1)

    def test_repaired_target_check_receives_path_arguments(self):
        (self.fixture.root / 'controller.py').write_text(
            "from pathlib import Path\n"
            "from sop import State, Option, Parameter, Workflow\n"
            "def check(context):\n"
            "    if not isinstance(context['args'].artifact, Path):\n        raise AssertionError('artifact must remain a Path')\n"
            "workflow = Workflow(\n"
            " State('review', 'Inspect the artifact.', initial=True, options=(\n"
            "  Option('inspect', 'finished', 'Inspect.', params=(Parameter('--artifact', type=Path),)),)),\n"
            " State('finished', 'Checked.', final=True, precheck=check))\n")
        # Load the new fixture without the setUp controller override.
        import importlib.util
        spec = importlib.util.spec_from_file_location('typed_repair', self.fixture.root / 'controller.py')
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        controller.SOP_NAME = 'review-child-work'
        with patch.object(self.wrapper, 'load_controller', return_value=controller):
            start = self.call()
            with patch.object(controller.workflow, 'after', side_effect=AssertionError('repair needed')):
                failed = self.call('inspect', '--artifact', '/tmp/evidence.txt')
            self.assertEqual(failed['status'], "error")
            self.assertEqual(self.retry(failed)['status'], 'finished')

    def test_final_repair_preserves_saved_handoff_without_repeating_comment(self):
        start = self.call()
        with patch.object(self.controller.workflow, 'after', side_effect=AssertionError('readback failed')):
            failed = self.call('resteer', '--child', 'child-one', '--prompt', 'Repair')
        with patch.object(self.controller.workflow, 'step', side_effect=AssertionError('controller replayed')):
            fixed = self.retry(failed)
        self.assertEqual(fixed['status'], 'resteer')
        self.assertEqual(fixed['next_step'], [])
        self.assertEqual(sum(c[0] == 'comment' for c in self.fixture.cli_calls), 1)
