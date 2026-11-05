import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from multica_aide import monitor


class ExperimentMonitor(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def start(self, source):
        job = monitor.start([sys.executable, '-c', source], self.root, self.root / 'logs')
        self.addCleanup(monitor.stop, job)
        return job

    def test_warning_recalls_consume_only_new_output_and_exit_has_priority(self):
        job = self.start("import sys,time; print('WARNING: inspect this', file=sys.stderr, flush=True); time.sleep(1.2); print('{\"score\": 3}')")
        record = {}
        first = monitor.wait(job, record)
        self.assertEqual((first['state'], first['reason']), ('running', 'important_log'))
        self.assertIn('WARNING', first['new_output']['stderr.log'])
        with patch.object(monitor, 'INTERVALS', (.02,)):
            second = monitor.wait(job, record)
        self.assertEqual(second['reason'], 'checkpoint')
        self.assertNotIn('WARNING', str(second['new_output']))
        self.assertEqual(record['checkpoint'], 1)
        result = monitor.wait(job, record)
        self.assertEqual((result['state'], result['returncode']), ('ended', 0))
        self.assertEqual(monitor.delivery(job), {'score': 3})

    def test_checkpoint_index_persists_in_the_task_record(self):
        job = self.start('import time; time.sleep(30)')
        path = self.root / 'task_id.json'
        monitor.write(path, {'status': 'test', 'monitor': {}})
        for index in range(6):
            state = monitor.read(path)
            with patch.object(monitor, 'INTERVALS', (.001, .002, .003, .004)):
                self.assertEqual(monitor.wait(job, state['monitor'])['reason'], 'checkpoint')
            monitor.write(path, state)
            self.assertEqual(monitor.read(path)['monitor']['checkpoint'], index + 1)
        self.assertEqual(monitor.INTERVALS, (450, 900, 1800, 3600))

    def test_nonzero_exit_returns_logs_for_review(self):
        job = self.start("import sys; print('ERROR: test failed', file=sys.stderr); sys.exit(7)")
        while not (Path(job) / 'result.json').exists():
            time.sleep(.01)
        result = monitor.wait(job, {})
        self.assertEqual((result['state'], result['returncode']), ('ended', 7))
        self.assertIn('ERROR: test failed', result['new_output']['stderr.log'])

    def test_exception_stops_the_owned_process_group_and_keeps_logs(self):
        job = self.start("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print('running',flush=True); time.sleep(30)")
        while not (Path(job) / 'stdout.log').stat().st_size:
            time.sleep(.01)
        monitor.stop(job)
        result = monitor.wait(job, {})
        self.assertTrue(result['exception'])
        self.assertEqual(result['state'], 'ended')
        self.assertIn('running', result['new_output']['stdout.log'])

    def test_large_new_logs_are_bounded_and_consumed(self):
        job = self.start("print('x'*20000)")
        while not (Path(job) / 'result.json').exists():
            time.sleep(.01)
        record = {}
        first = monitor.wait(job, record)
        self.assertLessEqual(len(first['new_output']['stdout.log']), 2000)
        self.assertEqual(monitor.wait(job, record)['new_output'], {})


if __name__ == '__main__':
    unittest.main()
