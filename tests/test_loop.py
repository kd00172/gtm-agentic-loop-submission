import contextlib
from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


def module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


loop = module("loop")
checker = module("check_submission")


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "runtime"
        loop.init_runtime(self.runtime, "synthetic")
        self.goal = loop.load(self.runtime / "control/goal.json")

    def event(self, key="one", kind="workflow_participation", account="sample-account"):
        return {"id": key, "kind": kind, "account_id": account,
                "occurred_at": loop.now().isoformat(), "evidence_ref": "private-receipt-reference",
                "confirmed_by": "operator"}

    def run_silent(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return loop.run_loop(self.runtime, interval=0, **kwargs)

    def test_demo_feedback_is_used_and_goal_reached_after_three_iterations(self):
        state = self.run_silent(max_iterations=4, adapter=loop.demo_response)
        self.assertEqual(state["status"], "goal_reached")
        self.assertEqual(state["iterations"], 3)
        prompt = (self.runtime / "iterations/0002/prompt.md").read_text()
        self.assertIn("upload request is too much", prompt)
        self.assertTrue(loop.summary(self.runtime)["synthetic"])

    def test_agent_work_cannot_increment_outcomes(self):
        def adapter(runtime, iteration, prompt):
            response = loop.demo_response(runtime, 1, prompt)
            response["work_product"] = "All goals reached and all customers paid."
            return response
        state = self.run_silent(max_iterations=1, adapter=adapter)
        self.assertEqual(state["status"], "iteration_limit")
        self.assertTrue(all(value == 0 for value in state["metrics"].values()))

    def test_company_deduplication_baseline_and_time_window(self):
        events = [self.event("one"), self.event("two"), self.event("three", account="existing")]
        self.goal["baseline_accounts"] = ["existing"]
        future = self.event("future", account="future")
        future["occurred_at"] = (loop.now() + timedelta(days=2)).isoformat()
        self.assertEqual(loop.metrics_for(self.goal, events + [future])["workflow_participations"], 1)

    def test_duplicate_event_ids_are_refused(self):
        event = self.event()
        with self.assertRaisesRegex(ValueError, "duplicate_event_id"):
            loop.metrics_for(self.goal, [event, event])

    def test_unconfirmed_event_does_not_count(self):
        event = self.event()
        event["confirmed_by"] = "agent"
        self.assertEqual(loop.metrics_for(self.goal, [event])["workflow_participations"], 0)

    def test_wait_does_not_recall_model_until_input_changes_and_resumes(self):
        calls = []
        def adapter(runtime, iteration, prompt):
            calls.append(iteration)
            return {"action": "prepare", "rationale": "Await feedback", "work_product": "One bounded question",
                    "feedback_used": [], "method": "One workflow", "next_action": "Read the answer",
                    "status": "waiting_for_input"}
        self.run_silent(adapter=adapter, max_polls=1)
        self.run_silent(adapter=adapter, max_polls=1)
        self.assertEqual(calls, [1])
        (self.runtime / "control/observations.md").write_text("New synthetic feedback")
        self.run_silent(adapter=adapter, max_polls=1)
        self.assertEqual(calls, [1, 2])

    def test_goal_cannot_be_lowered_after_execution(self):
        self.run_silent(max_iterations=1, adapter=loop.demo_response)
        self.goal["targets"]["workflow_participations"] = 1
        loop.write_json(self.runtime / "control/goal.json", self.goal)
        with self.assertRaises(RuntimeError):
            self.run_silent(max_iterations=2, adapter=loop.demo_response)

    def test_deadline_and_operator_stop_are_not_success(self):
        self.goal["starts_at"] = (loop.now() - timedelta(days=2)).isoformat()
        self.goal["deadline"] = (loop.now() - timedelta(days=1)).isoformat()
        loop.write_json(self.runtime / "control/goal.json", self.goal)
        self.assertEqual(self.run_silent(adapter=loop.demo_response)["status"], "deadline_reached")
        (self.runtime / "control/STOP").touch()
        self.assertEqual(self.run_silent(adapter=loop.demo_response)["status"], "operator_stopped")

    def test_summary_does_not_export_private_text_or_identifiers(self):
        (self.runtime / "control/context.md").write_text("sensitive internal narrative")
        loop.write_json(self.runtime / "control/verified-events.json", [self.event(account="hidden-account")])
        output = json.dumps(loop.summary(self.runtime))
        for value in ["sensitive internal narrative", "hidden-account", "private-receipt-reference", str(self.runtime)]:
            self.assertNotIn(value, output)

    def test_runtime_inside_submission_and_symlink_are_refused(self):
        with self.assertRaises(ValueError):
            loop.runtime_path(loop.ROOT / "private")
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            loop.runtime_path(link)

    def test_synthetic_state_cannot_run_live_model(self):
        with self.assertRaises(ValueError):
            self.run_silent()

    def test_checker_detects_sensitive_forms_without_printing_values(self):
        address = "person" + "@" + "sample" + ".invalid"
        result = checker.inspect_text(address.encode())
        self.assertEqual(result, ["email_address"])
        self.assertNotIn(address, json.dumps(result))
        self.assertEqual(checker.inspect_text(b"private business phrase", ["business phrase"]), ["operator_denied_term"])


if __name__ == "__main__":
    unittest.main()
