#!/usr/bin/env python3
"""Goal-driven agent iterations; all operating data stays outside this repository."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    "workflow_conversation": "workflow_conversations",
    "workflow_participation": "workflow_participations",
    "playback_delivered": "playbacks_delivered",
    "evidence_backed_revision": "evidence_backed_revisions",
    "paid_pilot": "paid_pilots",
}
STATUSES = {"continue", "waiting_for_input", "waiting_for_approval"}
RUN_STATUSES = STATUSES | {"not_started", "running", "goal_reached", "deadline_reached",
                           "iteration_limit", "operator_stopped", "error"}


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_text(path, limit=24000):
    if path.is_symlink():
        raise ValueError("symlink_input_refused")
    if path.stat().st_size > limit * 4:
        raise ValueError("input_too_large")
    value = path.read_text(encoding="utf-8")
    if len(value) > limit:
        raise ValueError("input_too_large")
    return value


def load(path, limit=24000):
    return json.loads(read_text(path, limit))


def write_json(path, value):
    if path.is_symlink():
        raise ValueError("symlink_output_refused")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink():
        raise ValueError("symlink_output_refused")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def runtime_path(value):
    candidate = Path(value).expanduser().absolute()
    if candidate.is_symlink():
        raise ValueError("symlink_runtime_refused")
    resolved = candidate.resolve()
    if resolved == ROOT or resolved.is_relative_to(ROOT) or ROOT.is_relative_to(resolved):
        raise ValueError("runtime_must_be_separate_from_submission")
    for folder in ("control", "agent", "iterations"):
        if (resolved / folder).is_symlink():
            raise ValueError("symlink_runtime_refused")
    return resolved


def init_runtime(runtime, mode="live"):
    if runtime.exists() and any(runtime.iterdir()):
        raise ValueError("runtime_must_be_new_or_empty")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.chmod(0o700)
    for folder in ("control", "agent", "iterations"):
        (runtime / folder).mkdir(mode=0o700)
    goal = load(ROOT / "config/goal.template.json")
    if mode == "synthetic":
        goal["starts_at"] = (now() - timedelta(days=1)).isoformat()
        goal["deadline"] = (now() + timedelta(days=1)).isoformat()
    write_json(runtime / "control/goal.json", goal)
    write_json(runtime / "control/verified-events.json", [])
    (runtime / "control/context.md").write_text("Add permitted product context, audience, and operating scope here.\n")
    (runtime / "control/observations.md").write_text("No external observations recorded.\n")
    write_json(runtime / "state.json", {"mode": mode, "status": "not_started", "iterations": 0,
               "goal_digest": None, "history": [], "method": "Investigate one recent workflow before proposing a comparison."})


def validate_goal(goal):
    if set(goal) != {"starts_at", "deadline", "targets", "stretch_targets", "baseline_accounts"}:
        raise ValueError("invalid_goal_fields")
    if timestamp(goal["starts_at"]) >= timestamp(goal["deadline"]):
        raise ValueError("invalid_goal_window")
    if not isinstance(goal["baseline_accounts"], list) or any(not isinstance(x, str) for x in goal["baseline_accounts"]):
        raise ValueError("invalid_baseline")
    if not isinstance(goal["targets"], dict) or not goal["targets"]:
        raise ValueError("nonempty_targets_required")
    for group in (goal["targets"], goal["stretch_targets"]):
        if not isinstance(group, dict):
            raise ValueError("invalid_targets")
        for key, value in group.items():
            if key not in METRICS.values() or type(value) is not int or value < 1:
                raise ValueError("invalid_metric_target")


def metrics_for(goal, events, at=None):
    validate_goal(goal)
    if not isinstance(events, list):
        raise ValueError("event_list_required")
    at = at or now()
    counts = {key: set() for key in METRICS.values()}
    seen = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != {"id", "kind", "account_id", "occurred_at", "evidence_ref", "confirmed_by"}:
            raise ValueError("invalid_event_fields")
        if any(not isinstance(v, str) or not v.strip() for v in event.values()):
            raise ValueError("nonempty_event_strings_required")
        if event["id"] in seen:
            raise ValueError("duplicate_event_id")
        seen.add(event["id"])
        if event["kind"] not in METRICS:
            raise ValueError("unknown_event_kind")
        occurred = timestamp(event["occurred_at"])
        if event["confirmed_by"] != "operator" or not (timestamp(goal["starts_at"]) <= occurred <= min(at, timestamp(goal["deadline"]))):
            continue
        if event["kind"] in {"workflow_participation", "paid_pilot"} and event["account_id"] in goal["baseline_accounts"]:
            continue
        key = event["id"] if event["kind"] == "evidence_backed_revision" else event["account_id"]
        counts[METRICS[event["kind"]]].add(key)
    return {key: len(values) for key, values in counts.items()}


def reached(goal, metrics):
    return all(metrics[key] >= target for key, target in goal["targets"].items())


def read_inputs(runtime):
    control = runtime / "control"
    goal = load(control / "goal.json")
    context = read_text(control / "context.md")
    observations = read_text(control / "observations.md")
    events = load(control / "verified-events.json", 256000)
    return goal, context, observations, events


def build_prompt(goal, context, observations, metrics, state):
    instructions = read_text(ROOT / "prompts/iteration.md")
    skill = read_text(ROOT / "skills/gtm-loop/SKILL.md")
    data = {"goal": goal, "confirmed_metrics": metrics, "product_and_scope": context,
            "observations": observations, "current_method": state["method"],
            "previous_iterations": state["history"][-2:], "iteration": state["iterations"]}
    return instructions + "\n\nEXECUTION SKILL\n" + skill + "\n\nOPERATING DATA\n" + json.dumps(data, ensure_ascii=False)


def validate_response(response):
    fields = {"action", "rationale", "work_product", "feedback_used", "method", "next_action", "status"}
    if not isinstance(response, dict) or set(response) != fields:
        raise ValueError("invalid_response_fields")
    for key in fields - {"feedback_used"}:
        if not isinstance(response[key], str) or not response[key].strip() or len(response[key]) > 24000:
            raise ValueError("invalid_response_text")
    if response["status"] not in STATUSES or not isinstance(response["feedback_used"], list):
        raise ValueError("invalid_response_status_or_feedback")
    if len(response["feedback_used"]) > 20 or any(not isinstance(x, str) or len(x) > 4000 for x in response["feedback_used"]):
        raise ValueError("invalid_feedback")
    return response


def codex_response(runtime, directory, prompt, model, timeout):
    output = directory / "response.json"
    command = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--color", "never", "--cd", str(runtime / "agent"),
               "--output-schema", str(ROOT / "config/response.schema.json"), "--output-last-message", str(output)]
    if model:
        command += ["--model", model]
    command += ["-"]
    with (directory / "agent.log").open("w") as log:
        process = subprocess.Popen(command, cwd=runtime / "agent", stdin=subprocess.PIPE,
                                   stdout=log, stderr=log, text=True, start_new_session=True)
        try:
            process.communicate(input=prompt, timeout=timeout)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if process.returncode:
        raise RuntimeError("agent_process_failed_see_private_log")
    return load(output, 180000)


@contextlib.contextmanager
def runtime_lock(runtime):
    lock_path = runtime / ".runner.lock"
    if lock_path.is_symlink():
        raise ValueError("symlink_lock_refused")
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("runtime_already_running") from None
        yield


def run_loop(runtime, max_iterations=200, interval=300, timeout=600, model=None, adapter=None, max_polls=None):
    if max_iterations < 1 or interval < 0 or timeout < 1:
        raise ValueError("invalid_runner_limits")
    with runtime_lock(runtime):
        state = load(runtime / "state.json", 1000000)
        if (state["mode"] == "synthetic") != (adapter is not None):
            raise ValueError("synthetic_and_live_modes_must_not_mix")
        polls = 0
        try:
            while True:
                goal, context, observations, events = read_inputs(runtime)
                metrics = metrics_for(goal, events)
                goal_hash = digest(goal)
                if state["goal_digest"] not in (None, goal_hash):
                    raise ValueError("goal_changed_start_a_new_runtime")
                state["goal_digest"] = goal_hash
                state["metrics"] = metrics
                terminal = None
                if (runtime / "control/STOP").exists():
                    terminal = "operator_stopped"
                elif reached(goal, metrics):
                    terminal = "goal_reached"
                elif now() >= timestamp(goal["deadline"]):
                    terminal = "deadline_reached"
                elif state["iterations"] >= max_iterations:
                    terminal = "iteration_limit"
                if terminal:
                    state["status"] = terminal
                    write_json(runtime / "state.json", state)
                    return state
                input_hash = digest([goal, context, observations, events])
                waiting = state.get("last_agent_status", "").startswith("waiting_") and input_hash == state.get("last_input_digest")
                delay = max(0, state.get("next_call_after", 0) - time.time())
                if waiting or delay > 0:
                    write_json(runtime / "state.json", state)
                    polls += 1
                    if max_polls is not None and polls >= max_polls:
                        return state
                    time.sleep(min(30, max(1, delay) if not waiting else 30,
                                   max(0.1, (timestamp(goal["deadline"]) - now()).total_seconds())))
                    continue
                state["iterations"] += 1
                state["status"] = "running"
                directory = runtime / "iterations" / f'{state["iterations"]:04d}'
                directory.mkdir(mode=0o700)
                prompt = build_prompt(goal, context, observations, metrics, state)
                (directory / "prompt.md").write_text(prompt, encoding="utf-8")
                write_json(directory / "basis.json", {"goal_digest": goal_hash, "input_digest": input_hash,
                           "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
                           "method_digest": digest(state["method"]), "mode": state["mode"],
                           "started_at": now().isoformat(), "confirmed_metrics_before": metrics})
                write_json(runtime / "state.json", state)
                remaining = max(0.1, (timestamp(goal["deadline"]) - now()).total_seconds())
                if adapter:
                    response = adapter(runtime, state["iterations"], prompt)
                else:
                    response = codex_response(runtime, directory, prompt, model, min(timeout, remaining))
                response = validate_response(response)
                write_json(directory / "response.json", response)
                state["history"].append({"iteration": state["iterations"], **response})
                state["history"] = state["history"][-2:]
                state["method"] = response["method"]
                state["last_agent_status"] = response["status"]
                state["last_input_digest"] = input_hash
                state["next_call_after"] = time.time() + interval
                state["status"] = response["status"]
                write_json(runtime / "state.json", state)
                print(json.dumps({"iteration": state["iterations"], "status": state["status"], "mode": state["mode"]}), flush=True)
        except KeyboardInterrupt:
            state["status"] = "operator_stopped"
            write_json(runtime / "state.json", state)
            return state
        except Exception as exc:
            state["status"] = "error"
            write_json(runtime / "state.json", state)
            # Raw errors can contain private context or paths; retain them only locally.
            (runtime / "last-error.txt").write_text(type(exc).__name__ + ": " + str(exc))
            raise RuntimeError("loop_error_see_private_last_error") from None


def summary(runtime):
    state = load(runtime / "state.json", 1000000)
    goal, _, _, events = read_inputs(runtime)
    metrics = metrics_for(goal, events)
    if state.get("goal_digest") not in (None, digest(goal)):
        raise ValueError("goal_changed_start_a_new_runtime")
    if state["status"] not in RUN_STATUSES or type(state["iterations"]) is not int or state["iterations"] < 0:
        raise ValueError("invalid_summary_state")
    # Intentionally enumerate every exported field. Never export free-form input.
    return {"synthetic": state["mode"] == "synthetic", "status": state["status"],
            "iterations": int(state["iterations"]), "goal_reached": reached(goal, metrics),
            "metrics": metrics, "targets": goal["targets"], "stretch_targets": goal["stretch_targets"]}


def demo_response(runtime, iteration, prompt):
    observations = runtime / "control/observations.md"
    if iteration == 1:
        observations.write_text("Synthetic feedback: an upload request is too much before a conversation.")
    elif iteration == 2:
        if "upload request is too much" not in prompt:
            raise ValueError("feedback_not_carried_forward")
        observations.write_text("Synthetic feedback: a short walkthrough is accepted; one task is provided.")
    else:
        events = []
        for i in range(5):
            kinds = ["workflow_conversation"]
            if i < 2:
                kinds += ["workflow_participation", "playback_delivered", "evidence_backed_revision"]
            for kind in kinds:
                events.append({"id": f"synthetic-{kind}-{i}", "kind": kind,
                               "account_id": f"synthetic-account-{i}", "occurred_at": now().isoformat(),
                               "evidence_ref": "synthetic-receipt", "confirmed_by": "operator"})
        write_json(runtime / "control/verified-events.json", events)
    return {"action": ["prepare_workflow_ask", "reduce_first_ask", "prepare_playback"][min(iteration, 3)-1],
            "rationale": "Synthetic demonstration only.",
            "work_product": "Synthetic offer: bring one recent task to a short walkthrough; uploads can wait.",
            "feedback_used": [] if iteration == 1 else ["Synthetic feedback from the previous iteration"],
            "method": "Ask for one task through a conversation before requesting records.",
            "next_action": "Review the resulting workflow and return a bounded comparison.", "status": "continue"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "run", "demo", "report"])
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model")
    parser.add_argument("--max-iterations", type=int, default=200)
    parser.add_argument("--interval-seconds", type=float, default=300)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args()
    try:
        runtime = runtime_path(args.runtime)
        if args.command == "init":
            init_runtime(runtime)
            result = {"status": "initialized_outside_submission", "model_calls": 0}
        elif args.command == "demo":
            init_runtime(runtime, "synthetic")
            run_loop(runtime, max_iterations=4, interval=0, adapter=demo_response)
            result = summary(runtime)
        elif args.command == "run":
            run_loop(runtime, args.max_iterations, args.interval_seconds, args.timeout_seconds, args.model)
            result = summary(runtime)
        else:
            result = summary(runtime)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") in {"error", "deadline_reached", "iteration_limit"}:
            return 2
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
