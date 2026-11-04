from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from multica_aide import SearchTree, select_parent
from multica_aide.viewer import make_server, render_tree


class SharedTree(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tree = SearchTree(self.temporary.name, drafts=2, knowledge_base="/notes")
        self.baseline = self.tree.register_baseline("baseline", "Baseline", "Initial measured solution", 5, artifact="baseline-repo")

    def test_concurrent_clients_reserve_drafts_once_then_share_best(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            contexts = list(pool.map(self.tree.next_context, ["one", "two", "three"]))
        self.assertEqual(sum(c["mode"] == "draft" for c in contexts), 2)
        self.assertEqual(self.tree.next_context("one"), contexts[0])
        for i, context in enumerate(contexts):
            node = self.tree.register("solution-" + str(i), "Idea", "Measured idea", 6 + i,
                                      session_id=context["session_id"], round_id=context["round_id"], artifact={"repo": str(i)})
            self.assertEqual(node["parent_id"], context["parent_id"])
        resumed = SearchTree(self.temporary.name, drafts=2).next_context("one")
        self.assertEqual(resumed["mode"], "refine")
        self.assertEqual(resumed["restore_node"]["id"], "solution-2")
        self.assertEqual(resumed["restore_node"]["artifact"], {"repo": "2"})
        self.assertEqual(len(resumed["tree_summary"]), 4)
        filtered = self.tree.next_context('two', eligible_ids={'solution-2'})
        self.assertEqual(filtered['mode'], 'refine')
        self.assertEqual(filtered['parent_id'], 'solution-2')

    def test_rejected_score_preserves_round_and_nodes(self):
        context = self.tree.next_context("client")
        for score in (None, True, "1", float("nan"), float("inf")):
            with self.subTest(score=score), self.assertRaises(ValueError):
                self.tree.register("invalid", "Bad", "Unmeasured", score, session_id="client")
        self.assertEqual(self.tree.next_context("client"), context)
        self.assertEqual(self.tree.snapshot()["nodes"], [self.baseline])
        with self.assertRaisesRegex(ValueError, "pending round"):
            self.tree.register("wrong-round", "Bad", "Measured elsewhere", 1,
                               session_id="client", round_id="other")

    def test_proposal_stays_pending_until_score_and_preserves_registration_times(self):
        context = self.tree.next_context("one")
        put_forward = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        scored = datetime(2026, 11, 4, 12, 0, 7, tzinfo=timezone.utc)
        with patch('multica_aide.tree.datetime', wraps=datetime) as clock:
            clock.now.side_effect = [put_forward, scored]
            proposal = self.tree.register_description('proposal', 'Proposal', 'Awaiting official score',
                                                      session_id='one', round_id=context['round_id'])
            self.assertIsNone(proposal['score'])
            self.assertEqual(proposal['put_forward_at'], put_forward.isoformat())
            self.assertIsNone(proposal['scored_at'])
            self.assertEqual(self.tree.next_context('one'), context)
            # One reserved draft plus its proposal occupies one slot, leaving the second draft available.
            second = self.tree.next_context('two')
            self.assertEqual(second['mode'], 'draft')
            third = self.tree.next_context('three')
            self.assertEqual(third['mode'], 'refine')
            self.assertEqual(third['restore_node']['id'], 'baseline')
            self.assertEqual(third['tree_summary'][1]['put_forward_at'], put_forward.isoformat())
            retry = self.tree.register_description('proposal', 'Proposal', 'Awaiting official score', session_id='one')
            self.assertEqual(retry, proposal)
            with self.assertRaisesRegex(ValueError, 'finite numeric score'):
                self.tree.register_score('proposal', None)
            self.assertIn('one', self.tree.snapshot()['sessions'])
            measured = self.tree.register_score('proposal', 9, review='Official result')
            self.assertEqual(measured['scored_at'], scored.isoformat())
            self.assertEqual(measured['test_duration_seconds'], 7)
            self.assertEqual(self.tree.register_score('proposal', 9, review='Official result'), measured)
            self.assertEqual(self.tree.register_description('proposal', 'Proposal', 'Awaiting official score',
                                                            session_id='one'), measured)
            self.assertEqual(clock.now.call_count, 2)
        self.assertNotIn('one', self.tree.snapshot()['sessions'])
        next_round = self.tree.next_context('one')
        self.assertEqual(next_round['parent_id'], 'proposal')
        self.assertEqual(self.baseline['put_forward_at'], self.baseline['scored_at'])
        self.assertEqual(self.baseline['test_duration_seconds'], 0)

    def test_host_preserves_first_cli_registration_time_before_polling(self):
        queued_at = '2026-11-04T12:00:00+00:00'
        node = self.tree.register_description('queued', 'Queued', 'First CLI registration',
                                              session_id='queued-client', put_forward_at=queued_at)
        self.assertEqual(node['put_forward_at'], queued_at)
        retry = self.tree.register_description('queued', 'Queued', 'First CLI registration',
                                               session_id='queued-client', put_forward_at='2026-11-04T12:00:30+00:00')
        self.assertEqual(retry['put_forward_at'], queued_at)
        with patch('multica_aide.tree.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 4, 12, 0, 45, tzinfo=timezone.utc)
            measured = self.tree.register_score('queued', 8)
        self.assertEqual(measured['test_duration_seconds'], 45)

    def test_outer_controller_registers_after_custom_scoring(self):
        node = self.tree.register("custom", "Custom", "External phase result", 4,
                                  session_id="outer", parent_id="baseline", review="Retain assertions")
        replay = self.tree.register("custom", "Custom", "External phase result", 4,
                                    session_id="outer", parent_id="baseline", review="Retain assertions")
        self.assertEqual(node, replay)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.tree.register("custom", "Changed", "External phase result", 4,
                               session_id="outer", parent_id="baseline")
        self.assertEqual(select_parent(self.tree.snapshot()["nodes"], drafts=0, maximize=False)["id"], "custom")

    def test_parent_can_release_failed_round_without_publishing_it(self):
        context = self.tree.next_context("failed-client")
        with self.assertRaisesRegex(ValueError, "pending round"):
            self.tree.cancel_round("failed-client", "other")
        self.assertEqual(self.tree.cancel_round("failed-client", context["round_id"]), context)
        self.assertIsNone(self.tree.cancel_round("failed-client", context["round_id"]))
        retry = self.tree.next_context("failed-client")
        self.assertNotEqual(retry["round_id"], context["round_id"])
        self.assertEqual(retry["mode"], "draft")
        self.assertEqual(self.tree.snapshot()["nodes"], [self.baseline])

    def test_reevaluation_changes_the_next_parent_and_rejects_missing_score(self):
        self.tree.register('candidate', 'Candidate', 'Measured candidate', 7, session_id='outer', parent_id='baseline')
        self.tree.drafts = 0
        self.assertEqual(self.tree.next_context('first')['parent_id'], 'candidate')
        self.tree.rescore('candidate', 3, review='Aggregate observed quality')
        self.assertEqual(self.tree.next_context('second')['parent_id'], 'baseline')
        self.assertEqual(self.tree.next_context('third', eligible_ids={'candidate'})['parent_id'], 'candidate')
        self.assertEqual(self.tree.next_context('first', eligible_ids={'baseline'})['parent_id'], 'candidate')
        with self.assertRaisesRegex(ValueError, 'finite numeric score'):
            self.tree.rescore('candidate', None)
        self.assertEqual(self.tree.snapshot()['nodes'][1]['score'], 3)

    def test_viewer_serves_only_widget_and_tree(self):
        server = make_server(self.tree, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        with urlopen(base + "/tree.json") as response:
            data = json.load(response)
        self.assertEqual(data["nodes"], [self.baseline])
        with urlopen(base) as response:
            html = response.read().decode()
        self.assertIn("SOP exploration", html)
        self.assertIn("<svg", html)
        self.assertIn('Put forward:', html)
        self.assertIn('Test duration:', html)
        self.assertNotIn("tracks.html", html)
        escaped = render_tree([dict(self.baseline, title="</script><script>bad</script>")])
        self.assertNotIn("</script><script>bad", escaped)


if __name__ == "__main__":
    unittest.main()
