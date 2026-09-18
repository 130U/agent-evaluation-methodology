"""Two-stage descriptive audit: all reviews -> frozen sample -> gates.

Registration is external. Fresh review/gate ledgers allow respectively 1/2
calls per original baseline unit; stopped attempts never resume or retry.
Completed directories are hash-sealed; this detects change, not a malicious
writer replacing the whole store. No acting, optimization or accuracy claim.

Registration adds baseline_manifest_path (included in frozen input hashes),
per_phase_client_limits.review/gate with max_calls exactly 1/2, nonempty salts
diagnosis_sampling_salt/independent_review_sampling_salt, diagnosis cap 2,
and each unit's user_validity plus baseline_classification. Original baseline
identities, order and output paths are preserved. The independent sample is
at most 12 diagnostic cards, not trajectories. Only the offline helper creates
selection.json; implementing this module does not register or run any study.
"""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import uuid
import zipfile

from .admission import audit_diagnosis
from .contracts import canonical_hash
from .subscription_client import CliTextClient
from .subscription_evidence import choose_agent_pool, load, save, verify_cli_call
from .subscription_feedback import _review_contract, audit_native_diagnosis, review_simulation

ORIGINAL_STAGE = "N1_natural_diagnosis_audit"
INTERRUPTED_STAGE = "N1_interrupted_complete_trajectory_audit"
INTERRUPTED_TOTALS = {"cli_invocations": 54, "input_tokens": 2520000,
                      "output_tokens": 108000, "client_seconds_sum": 8640}
INTERRUPTED_PHASE_LIMITS = {
    "review": {"max_calls": 1, "max_input_tokens": 60000, "max_output_tokens": 3000, "max_seconds": 180, "request_timeout": 120},
    "gate": {"max_calls": 2, "max_input_tokens": 80000, "max_output_tokens": 3000, "max_seconds": 300, "request_timeout": 120}}
_CORE_SOURCES = {"episode/request.json", "episode/trajectory.json", "episode/calls.jsonl",
                 "episode/simulation.json", "episode/outcome.json", "client/budget.json"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(root, path):
    value = (Path(root) / path).resolve()
    if not value.is_relative_to(Path(root).resolve()):
        raise ValueError("N1 path escapes its project")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Invalid N1 identifier")
    return value


def _write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def interrupted_cohort(root, *, baseline_manifest_path, stop_review_path, baseline_source_snapshot_path):
    """Read and verify the exact 24 -> 18+1+5 stopped cohort; never run/write.

    Used by registration and every resumed audit invocation. Source hashes are
    checked in the old frozen ZIP because current HTTP code is a new version.
    Qualification comes from bound original semantic reviews, never user labels.
    """
    root = Path(root).resolve()
    baseline_path = within(root, baseline_manifest_path)
    baseline = load(baseline_path)
    if sha(baseline_path) != baseline_path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]:
        raise ValueError("Stopped baseline manifest digest differs")
    identifier(baseline["manifest_id"])
    units = baseline["units"]
    if (len(units) != 24 or len({u["unit_id"] for u in units}) != 24
        or len({u["task_id"] for u in units}) != 12
        or set(Counter(u["task_id"] for u in units).values()) != {2}):
        raise ValueError("Stopped baseline must retain 24 identities and 12 repeated tasks")
    for unit in units:
        identifier(unit["unit_id"])
    folder = within(root, f"results/g1/{baseline['manifest_id']}")
    stop_path = folder / "STOP.json"
    if (folder / "active.lock").exists():
        raise ValueError("Stopped baseline is unexpectedly active")
    stop = load(stop_path)
    review_path = within(root, stop_review_path)
    if review_path != folder / "G1_STOP_INTEGRITY_REVIEW.json":
        raise ValueError("Use the stopped baseline's independent STOP review")
    review = load(review_path)
    if (review.get("review_type") != "independent_research_agent_stop_review"
        or review.get("manifest_id") != baseline["manifest_id"]
        or review.get("manifest_sha256") != sha(baseline_path)
        or review.get("decision") != "keep_v2_stopped_no_retroactive_acceptance_no_original_n1"
        or review.get("planned_units") != 24 or review.get("all_planned_unit_statuses_identified") is not True
        or review.get("all_24_evaluated") is not False
        or review.get("natural_optimization_authorized") is not False
        or review.get("original_n1_registration_authorized") is not False
        or review.get("stop_unit") != stop.get("unit_id")):
        raise ValueError("Independent STOP review does not bind this interrupted cohort")
    identities = lambda rows: [(u["unit_id"], u["task_id"], u["simulation_seed"]) for u in rows]
    if identities(review["units"]) != identities(units):
        raise ValueError("STOP review must account for all original identities in order")
    evidence = review.get("evidence_hashes", {})
    if not isinstance(evidence, dict) or len(evidence) != 151:
        raise ValueError("STOP review must retain its 151 evidence hashes")
    paths = set(evidence)
    def require_source(path):
        path = within(root, path)
        name = path.relative_to(root).as_posix()
        if name not in evidence or sha(path) != evidence[name]:
            raise ValueError("STOP review is missing or differs from mandatory evidence: " + name)
        return path
    require_source(baseline_path)
    require_source(stop_path)
    for name, expected in evidence.items():
        if sha(within(root, name)) != expected:
            raise ValueError("Independent STOP evidence changed: " + name)
    snapshot = within(root, baseline_source_snapshot_path)
    snapshot_digest = snapshot.with_suffix(".zip.sha256")
    if sha(snapshot) != snapshot_digest.read_text(encoding="utf-8").split()[0]:
        raise ValueError("Stopped source snapshot digest differs")
    with zipfile.ZipFile(snapshot) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate frozen snapshot member")
        for name, expected in baseline["source_hashes"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Old source snapshot differs from original manifest: " + name)
        if archive.read(baseline_path.relative_to(root).as_posix()) != baseline_path.read_bytes():
            raise ValueError("Snapshot embeds another baseline manifest")
    if not baseline["source_hashes"]:
        raise ValueError("Baseline source manifest cannot be empty")
    paths.update(p.relative_to(root).as_posix() for p in
                 (baseline_path.with_suffix(".sha256"), review_path, snapshot, snapshot_digest))
    population, eligible, costs = [], [], Counter()
    for unit, stopped in zip(units, review["units"]):
        directory = folder / unit["unit_id"]
        state = stopped["execution"]
        row = {"unit_id": unit["unit_id"], "task_id": unit["task_id"], "simulation_seed": unit["simulation_seed"],
               "execution": state, "qualification": stopped["qualification"]}
        if state == "not_run":
            if directory.exists() or row["qualification"] != "not_run":
                raise ValueError("A not-run unit now has an attempt or wrong status")
            row["disposition"] = "not_run_no_complete_trajectory"
            population.append(row)
            continue
        if state not in {"evaluated", "error"}:
            raise ValueError("Unregistered interrupted cohort execution state")
        request = load(require_source(directory / "episode/request.json"))
        outcome = load(require_source(directory / "episode/outcome.json"))
        budget = load(require_source(directory / "client/budget.json"))
        if (request.get("task_id") != unit["task_id"] or request.get("simulation_seed") != unit["simulation_seed"]
            or request.get("run_id") != outcome.get("run_id") or outcome.get("task_id") != unit["task_id"]
            or outcome.get("status") != state):
            raise ValueError("Original execution identity/status differs from STOP review")
        for out_key, budget_key in (("cli_invocations", "calls_reserved"), ("input_tokens", "known_input_tokens"), ("output_tokens", "known_output_tokens")):
            value = budget.get(budget_key)
            if type(value) is not int or value < 0 or outcome.get(out_key) != value:
                raise ValueError("Stopped cohort usage differs from its ledger")
            costs[out_key] += value
        if type(budget.get("unknown_usage_calls")) is not int or budget["unknown_usage_calls"] < 0:
            raise ValueError("Unknown usage is not a valid count")
        if (stopped.get("structural") != outcome.get("structural_outcome", {}).get("status")
            or stopped.get("unknown_usage_calls") != budget["unknown_usage_calls"]
            or outcome.get("unknown_usage_calls") != budget["unknown_usage_calls"]):
            raise ValueError("STOP structural/usage status differs from original outcome")
        costs["unknown_usage_calls"] += budget["unknown_usage_calls"]
        if state == "error":
            if (unit["unit_id"] != stop["unit_id"] or row["qualification"] != "unknown_not_evaluated"
                or outcome.get("structural_outcome", {}).get("status") != "unknown"
                or review.get("stop_run_id") != outcome["run_id"]):
                raise ValueError("Interrupted unit cannot become a scored task failure")
            row.update(disposition="infrastructure_interrupted_ungraded", run_id=outcome["run_id"])
            population.append(row)
            continue
        if (outcome.get("usage_incomplete") is not False or outcome.get("client_halt_reason")
            or budget.get("halt_reason") or budget["unknown_usage_calls"] != 0):
            raise ValueError("Eligible completed episode has incomplete usage or a client halt")
        semantic_path = require_source(directory / "semantic_review.json")
        semantic = load(semantic_path)
        if (any(semantic.get(k) != unit[k] for k in ("unit_id", "task_id", "simulation_seed"))
            or semantic.get("run_id") != outcome["run_id"]
            or semantic.get("qualified_status") != stopped["qualification"]
            or semantic.get("user_validity") != stopped.get("user_validity")
            or semantic.get("agent_editable_failure") != stopped.get("agent_editable_failure")):
            raise ValueError("Qualification/stratum differs from original semantic review")
        qualified, user_validity = semantic["qualified_status"], semantic["user_validity"]
        if qualified not in {"pass", "fail", "unknown"} or user_validity not in {"pass", "violated", "unknown"}:
            raise ValueError("Completed qualification/user validity must be explicit")
        if (qualified == "pass" and (user_validity != "pass" or semantic.get("agent_editable_failure") is not False
            or outcome.get("structural_outcome", {}).get("status") != "pass")):
            raise ValueError("Qualified pass conflicts with original evidence")
        if not _CORE_SOURCES <= set(semantic.get("source_hashes", {})):
            raise ValueError("Semantic review lacks mandatory six-source bindings")
        for name, expected in semantic["source_hashes"].items():
            original = require_source(directory / name)
            if sha(original) != expected:
                raise ValueError("Semantic source hash differs")
        sim_path = directory / "episode/simulation.json"
        simulation = load(sim_path)
        if (simulation.get("task_id") != unit["task_id"] or simulation.get("id") != outcome["run_id"]
            or simulation.get("messages") != load(directory / "episode/trajectory.json")):
            raise ValueError("Completed original simulation/trajectory identity differs")
        row.update(disposition="eligible_complete_trajectory", user_validity=user_validity,
                   agent_editable_failure=semantic["agent_editable_failure"], run_id=outcome["run_id"])
        population.append(row)
        eligible.append({**unit, "simulation_path": sim_path.relative_to(root).as_posix(), "simulation_id": simulation["id"],
            "task_sha256": request["task_sha256"], "semantic_review_path": semantic_path.relative_to(root).as_posix(),
            "user_validity": user_validity,
            "baseline_classification": {"pass": "qualified_pass", "fail": "explicit_fail", "unknown": "qualified_unknown"}[qualified]})
    counts = dict(Counter(r["execution"] for r in population))
    if counts != {"evaluated": 18, "error": 1, "not_run": 5} or review.get("execution_counts") != counts:
        raise ValueError("This recovery version requires exactly 18+1+5; do not choose an arbitrary subset")
    if [r["execution"] for r in population] != ["evaluated"] * 18 + ["error"] + ["not_run"] * 5:
        raise ValueError("The recovery cohort must preserve the original stopping order")
    if (review.get("cost_known_subtotals") != {k: costs[k] for k in ("cli_invocations", "input_tokens", "output_tokens")}
        or review.get("unknown_usage_calls") != costs["unknown_usage_calls"]):
        raise ValueError("Independent STOP total usage differs from original ledgers")
    if review.get("qualification_counts") != dict(Counter(r["qualification"] for r in population)):
        raise ValueError("Independent STOP qualification counts differ")
    if len({u["task_id"] for u in eligible}) != 12:
        raise ValueError("This completed cohort must retain all 12 task identities")
    return {"units": eligible, "parent_population": population, "parent_execution_counts": counts,
            "parent_cost_known_subtotals": dict(costs), "source_paths": sorted(paths)}


def verify_http_readiness(root, path):
    """Require explicit, hash-bound clearance; this does not run a probe."""
    root, path = Path(root).resolve(), within(root, path)
    value = load(path)
    required_sources = {"src/tau_feedback/subscription_client.py",
                        "src/tau_feedback/subscription_http_client.py", "scripts/probe_subscription_http.py"}
    sources, evidence = value.get("source_hashes", {}), value.get("evidence_hashes", {})
    probes = value.get("probe_paths", [])
    if (value.get("decision") != "allow_n1r1_http_postprocessing" or value.get("scope") != INTERRUPTED_STAGE
        or not isinstance(sources, dict) or not required_sources <= set(sources)
        or not isinstance(evidence, dict) or not evidence
        or not isinstance(probes, list) or len(probes) != 3 or len(set(probes)) != 3
        or not set(probes) <= set(evidence)):
        raise ValueError("HTTP readiness requires explicit scope and three hash-bound probes plus source coverage")
    for hashes in (sources, evidence):
        for name, expected in hashes.items():
            if sha(within(root, name)) != expected:
                raise ValueError("HTTP readiness source/evidence differs: " + name)
    return sorted({path.relative_to(root).as_posix(), *sources, *evidence})


def verify_manifest(root, path):
    root, path = Path(root).resolve(), within(root, path)
    digest = sha(path)
    if digest != path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]:
        raise ValueError("Natural audit manifest digest differs")
    m = load(path)
    interrupted = m.get("stage") == INTERRUPTED_STAGE
    count = 18 if interrupted else 24
    if (m.get("stage") not in {ORIGINAL_STAGE, INTERRUPTED_STAGE} or m.get("execution_authorized") is not True
        or m.get("baseline_unit_count") != 24 or len(m.get("units", [])) != count):
        raise ValueError("A separately registered exact N1 cohort manifest is required (original N1 requires 24)")
    identifier(m["manifest_id"])
    hashes = m["source_and_input_sha256"]
    for name, expected in hashes.items():
        if sha(within(root, name)) != expected:
            raise ValueError("Frozen source or baseline input differs: " + name)
    baseline_name = m["baseline_manifest_path"]
    if baseline_name not in hashes:
        raise ValueError("Baseline manifest must be a frozen input")
    baseline = load(within(root, baseline_name))
    identifier(baseline["manifest_id"])
    identities = [(u["unit_id"], u["task_id"]) for u in m["units"]]
    if interrupted:
        if (m["manifest_id"] != "n1-r1-retail-subscription-http-v1"
            or m.get("eligible_review_count") != 18 or m.get("parent_population_count") != 24
            or m.get("model") != "gpt-5.6-sol" or m.get("effort") != "low"
            or m.get("transport") != "codex_cli_responses_http_only"
            or m.get("per_phase_client_limits") != INTERRUPTED_PHASE_LIMITS
            or m.get("aggregate_thresholds") != INTERRUPTED_TOTALS or m.get("retries") != 0):
            raise ValueError("N1-R1 identity, population, HTTP transport and independent budgets must remain fixed")
        cohort = interrupted_cohort(root, baseline_manifest_path=baseline_name,
            stop_review_path=m["stop_review_path"], baseline_source_snapshot_path=m["baseline_source_snapshot_path"])
        for key in ("units", "parent_population", "parent_execution_counts", "parent_cost_known_subtotals"):
            if m.get(key) != cohort[key]:
                raise ValueError("N1-R1 must include every eligible original unit in order: " + key)
        required = set(cohort["source_paths"]) | set(verify_http_readiness(root, m["http_readiness_path"]))
        if not required <= set(hashes):
            raise ValueError("N1-R1 must freeze all STOP, old snapshot and HTTP readiness evidence")
        for field, name in (("stop_review_sha256", m["stop_review_path"]),
                            ("baseline_source_snapshot_sha256", m["baseline_source_snapshot_path"]),
                            ("http_readiness_sha256", m["http_readiness_path"])):
            if m.get(field) != hashes[name]:
                raise ValueError("N1-R1 explicit evidence digest differs: " + field)
    elif (identities != [(u["unit_id"], u["task_id"]) for u in baseline["units"]]
          or len({i[0] for i in identities}) != 24):
        raise ValueError("N1 must preserve original baseline identities and order")
    for u in m["units"]:
        identifier(u["unit_id"])
        expected_path = f"results/g1/{baseline['manifest_id']}/{u['unit_id']}/episode/simulation.json"
        if u["simulation_path"] != expected_path or expected_path not in hashes:
            raise ValueError("N1 must bind each original frozen baseline simulation path")
        if u.get("user_validity") not in {"pass", "violated", "unknown"}:
            raise ValueError("User validity must remain a separate explicit dimension")
        if u.get("baseline_classification") not in ({"qualified_pass", "explicit_fail", "qualified_unknown"} if interrupted else {"qualified_pass", "explicit_fail"}):
            raise ValueError("Invalid baseline classification")
        if u["baseline_classification"] == "qualified_pass" and u["user_validity"] != "pass":
            raise ValueError("Qualified pass requires qualified user validity")
    for phase, cap in (("review", 1), ("gate", 2)):
        limits = m["per_phase_client_limits"][phase]
        if type(limits.get("max_calls")) is not int or limits["max_calls"] != cap:
            raise ValueError("N1 requires independent review/gate caps of 1/2")
    if type(m.get("per_trajectory_diagnosis_cap")) is not int or m["per_trajectory_diagnosis_cap"] != 2:
        raise ValueError("N1 freezes a maximum of two agent diagnoses per trajectory")
    for key in ("diagnosis_sampling_salt", "independent_review_sampling_salt"):
        if not isinstance(m.get(key), str) or not m[key]:
            raise ValueError("Freeze both nonempty sampling salts")
    return m, digest, root / "results/n1" / m["manifest_id"]


class NativeRuntime:
    """Read official inputs; constructing a runtime makes no model call."""
    def __init__(self, root, manifest):
        from tau2.data_model.simulation import UserInfo
        from tau2.domains.retail.environment import get_tasks, get_environment
        from tau2.user.user_simulator import get_global_user_sim_guidelines
        self.root = root
        self.tasks = {task.id: task for task in get_tasks("train")}
        self.tools = [tool.openai_schema for tool in get_environment().get_tools()]
        self.user_info = UserInfo(implementation="user_simulator", llm="codex-cli/structured-sol", llm_args={},
                                 global_simulation_guidelines=get_global_user_sim_guidelines())

    def load_unit(self, unit):
        from tau2.data_model.simulation import SimulationRun
        return SimpleNamespace(task=self.tasks[unit["task_id"]],
            simulation=SimulationRun.model_validate(load(within(self.root, unit["simulation_path"]))),
            tools=deepcopy(self.tools), user_info=self.user_info)


def _inputs(root, unit, runtime):
    value = runtime.load_unit(unit)
    task, sim = value.task, value.simulation
    if (task.id != unit["task_id"] or sim.task_id != task.id or sim.id != unit["simulation_id"]
        or canonical_hash(task.model_dump(mode="json")) != unit["task_sha256"]):
        raise ValueError("Baseline task/simulation identity or task contents differ")
    from tau2.data_model.simulation import SimulationRun
    disk = SimulationRun.model_validate(load(within(root, unit["simulation_path"])))
    if disk.model_dump(mode="json") != sim.model_dump(mode="json"):
        raise ValueError("Runtime simulation differs from frozen input")
    value.messages = [message.model_dump(mode="json") for message in sim.messages]
    value.binding = {"unit_id": unit["unit_id"], "task_id": task.id, "simulation_id": sim.id,
        "simulation_path": unit["simulation_path"], "simulation_file_sha256": sha(within(root, unit["simulation_path"])),
        "task_sha256": unit["task_sha256"], "trajectory_sha256": canonical_hash(value.messages),
        "policy_sha256": canonical_hash(sim.policy), "tools_sha256": canonical_hash(value.tools),
        "user_guidelines_sha256": canonical_hash(value.user_info.global_simulation_guidelines)}
    return value


@contextmanager
def _exclusive(destination):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "STOP.json").exists():
        raise RuntimeError("This audit version is stopped; no retry")
    lock = destination / "active.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid()}))
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _files(directory):
    return {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob("*"))
            if p.is_file() and p != directory / "seal.json"}


def _seal(directory):
    _write_new(directory / "seal.json", {"artifact_sha256": _files(directory)})


def _verify_seal(directory):
    if load(directory / "seal.json") != {"artifact_sha256": _files(directory)}:
        raise ValueError("Sealed N1 evidence changed: " + str(directory))


def _ledger(client, limits):
    if client is None:
        return {"client_constructed": False, "calls": None, "known_input_tokens": None,
                "known_output_tokens": None, "unknown_usage_calls": None, "records": [], "limits": limits}
    return {"client_constructed": True, "calls": client.calls, "known_input_tokens": client.input_tokens,
            "known_output_tokens": client.output_tokens, "unknown_usage_calls": client.unknown_usage_calls,
            "elapsed_seconds": client.elapsed_seconds, "halt_reason": client.halt_reason,
            "records": deepcopy(client.records), "limits": limits, "generation_token_hard_cap": False}


def _ledger_client(root, directory, phase, manifest):
    ledger = load(directory / "ledger.json")
    if ledger["limits"] != manifest["per_phase_client_limits"][phase]:
        raise ValueError("Phase ledger budget differs from registration")
    if not ledger["client_constructed"] or ledger["unknown_usage_calls"] != 0 or ledger["halt_reason"]:
        raise ValueError("A completed phase requires a healthy, known-usage ledger")
    records = ledger["records"]
    if (type(ledger["calls"]) is not int or ledger["calls"] != len(records)
        or not 0 <= ledger["calls"] <= (1 if phase == "review" else 2)
        or any(r.get("status") != "complete" or r.get("usage_complete") is not True for r in records)):
        raise ValueError("Completed phase call ledger is incomplete")
    for token in ("input_tokens", "output_tokens"):
        values = [r.get("usage", {}).get(token) for r in records]
        if any(type(n) is not int or n < 0 for n in values) or sum(values) != ledger["known_" + token]:
            raise ValueError("Known phase usage differs from actual call records")
    return SimpleNamespace(root=root, output=directory / "client", records=records), ledger


def _verify_review(root, directory, unit, inputs, manifest, *, digest, sealed=True):
    if sealed:
        _verify_seal(directory)
    row = load(directory / "outcome.json")
    if (row.get("status") != "complete" or row.get("binding") != inputs.binding
        or row.get("phase") != "review" or row.get("manifest_sha256") != digest):
        raise ValueError("Review execution/binding is incomplete or changed")
    client, ledger = _ledger_client(root, directory, "review", manifest)
    if ledger["calls"] != 1:
        raise ValueError("Each review requires exactly one original model call")
    review_dir = directory / "native_review"
    outcome, binding = load(review_dir / "outcome.json"), load(review_dir / "binding.json")
    for key in ("task_id", "simulation_id", "task_sha256", "trajectory_sha256", "policy_sha256", "user_guidelines_sha256"):
        if binding.get(key) != inputs.binding[key] or outcome.get(key) != inputs.binding[key]:
            raise ValueError("Native review belongs to another input: " + key)
    verify_cli_call(client, outcome["cli_call_id"], role="llm_judge_review", model_sidecar=review_dir / "model_call")
    raw = load(review_dir / "model_call/outcome.json")["output"]["text"]
    if (review_dir / "model_call/native_response.txt").read_text(encoding="utf-8") != raw:
        raise ValueError("Raw native response differs from accepted CLI text")
    native = load(review_dir / "native_review.json")
    if native != outcome["native_review"]:
        raise ValueError("Saved native parsed review differs")
    from tau2.evaluator import review_llm_judge as upstream
    try:
        summary, ae, ue, cue, he, errors = upstream._parse_review_response(raw)
    except Exception as exc:
        # Replay the unchanged half-duplex review wrapper's parser-failure
        # representation (fixed upstream lines 537-554), without generate().
        summary, ae, ue, cue, he = "", False, False, False, False
        errors = [upstream.ReviewError(source="unknown", turn_idx=None,
            reasoning=f"Failed to parse LLM response: {exc}. Response: {raw}", correct_behavior=None)]
    parsed = {"summary": summary, "agent_error": ae, "user_error": ue,
              "critical_user_error": cue, "has_errors": he, "errors": [e.model_dump(mode="json") for e in errors]}
    if any(native.get(k) != v for k, v in parsed.items()):
        raise ValueError("Native review differs from pure parser replay")
    try:
        _review_contract(raw, native, upstream)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        status, diagnoses = "unknown", None
    else:
        status, diagnoses = "valid", native["errors"]
    if (outcome.get("status") != status or outcome.get("diagnoses") != diagnoses
        or row.get("native_review_status") != status or row.get("diagnoses") != diagnoses):
        raise ValueError("Unknown/valid diagnosis state disagrees with original text")
    if any(row.get(key) != value for key, value in _pool(unit, row, manifest).items()):
        raise ValueError("Review diagnosis denominators differ from the original diagnosis list")
    return row


def _pool(unit, row, manifest):
    diagnoses = row["diagnoses"]
    base = {"unit_id": unit["unit_id"], "task_id": unit["task_id"],
        "user_validity": unit["user_validity"], "baseline_classification": unit["baseline_classification"],
        "binding": row["binding"], "native_review_status": row["native_review_status"]}
    if diagnoses is None:
        return {**base, "native_diagnoses": None, "source_counts": None, "native_agent_count": None,
                "selected_native_indices": None, "selected_pool": None, "not_sampled": None}
    pool, indices, count = choose_agent_pool(diagnoses, trajectory_sha256=row["binding"]["trajectory_sha256"],
        salt=manifest["diagnosis_sampling_salt"], cap=2)
    return {**base, "native_diagnoses": len(diagnoses),
        "source_counts": {source: sum(d["source"] == source for d in diagnoses) for source in ("agent", "user")},
        "native_agent_count": count, "selected_native_indices": indices, "selected_pool": pool,
        "not_sampled": count - len(pool)}


def _selection(root, manifest, digest, destination, runtime):
    pools, reviews, cards = [], {}, []
    interrupted = manifest.get("stage") == INTERRUPTED_STAGE
    priority = "user_nonpass_or_qualification_unknown" if interrupted else "user_nonpass"
    strata = [priority, "qualified_pass", "explicit_fail"]
    for unit in manifest["units"]:
        directory = destination / "review" / unit["unit_id"]
        inputs = _inputs(root, unit, runtime)
        row = _verify_review(root, directory, unit, inputs, manifest, digest=digest)
        reviews[unit["unit_id"]] = sha(directory / "seal.json")
        pool = _pool(unit, row, manifest)
        pools.append(pool)
        for index, diagnosis in zip(pool["selected_native_indices"] or [], pool["selected_pool"] or []):
            stratum = priority if (unit["user_validity"] != "pass" or
                (interrupted and unit["baseline_classification"] == "qualified_unknown")) else unit["baseline_classification"]
            card = {"unit_id": unit["unit_id"], "native_index": index, "diagnosis": diagnosis,
                    "diagnosis_sha256": canonical_hash(diagnosis), "stratum": stratum,
                    "trajectory_sha256": row["binding"]["trajectory_sha256"]}
            card["rank_sha256"] = canonical_hash([manifest["independent_review_sampling_salt"],
                card["unit_id"], card["trajectory_sha256"], index, card["diagnosis_sha256"]])
            cards.append(card)
    ranked = sorted(cards, key=lambda c: (c["rank_sha256"], c["unit_id"], c["native_index"]))
    chosen = []
    for stratum in strata:
        chosen.extend([c for c in ranked if c["stratum"] == stratum][:4])
    chosen += [c for c in ranked if c not in chosen][:12 - len(chosen)]
    return {"schema_version": 1, "manifest_sha256": digest, "review_seals_sha256": reviews,
        "rules": {"per_trajectory_agent_cap": 2, "diagnosis_sampling_salt": manifest["diagnosis_sampling_salt"],
            "independent_review_sampling_salt": manifest["independent_review_sampling_salt"],
            "strata_priority": strata,
            "first_pass_per_stratum": 4, "total_cap": 12, "fill": "global hash among remaining selected-pool cards"},
        "pools": pools, "independent_review_cards": chosen, "independent_review_population_cards": len(cards),
        "boundary": "Pre-gate sample; no semantic labels/gate decisions; strata are not pooled prevalence"}


def freeze_selection(root, manifest_path, *, runtime_factory=NativeRuntime):
    """Offline helper; refuses existing selection or any gate attempt."""
    root = Path(root).resolve()
    m, digest, destination = verify_manifest(root, manifest_path)
    with _exclusive(destination):
        if (destination / "gate").exists() or (destination / "selection.json").exists():
            raise RuntimeError("Selection is immutable and must precede every gate attempt")
        value = _selection(root, m, digest, destination, runtime_factory(root, m))
        path = destination / "selection.json"
        _write_new(path, value)
        with path.with_suffix(".sha256").open("x", encoding="utf-8") as stream:
            stream.write(sha(path) + "  selection.json\n")
        return path


def _locked_selection(root, m, digest, destination, runtime):
    path = destination / "selection.json"
    if sha(path) != path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]:
        raise ValueError("Frozen selection digest differs")
    value = load(path)
    if value != _selection(root, m, digest, destination, runtime):
        raise ValueError("Selection differs from full sealed review population/fixed rule")
    return value, sha(path)


def _locked_independent_labels(destination, selection, selection_hash):
    """Require first-label evidence to be sealed before creating gate clients.

    This checks provenance/coverage, not the correctness or independence of labels.
    """
    folder = destination / "independent_review"
    path = folder / "labels.json"
    labels_hash = sha(path)
    if path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0] != labels_hash:
        raise ValueError("Independent labels changed after locking")
    labels, mapping, seal = (load(path), load(folder / "HOST_MAPPING_DO_NOT_GIVE_REVIEWERS.json"),
                             load(folder / "PACKET_SEAL.json"))
    if (labels.get("selection_sha256") != selection_hash or mapping.get("selection_sha256") != selection_hash
        or labels.get("packet_seal_sha256") != sha(folder / "PACKET_SEAL.json")):
        raise ValueError("Independent labels belong to another selection/packet set")
    files = {p.name: sha(p) for p in sorted((folder / "packets").iterdir()) if p.is_file()}
    if seal.get("files_sha256") != files:
        raise ValueError("Independent review packet changed")
    cards = mapping["cards"]
    expected = sorted((c["unit_id"], c["native_index"], c["diagnosis_sha256"]) for c in selection["independent_review_cards"])
    actual = sorted((c["unit_id"], c["native_index"], c["diagnosis_sha256"]) for c in cards)
    ids = {c["card_id"] for c in cards}
    if actual != expected or len(ids) != len(cards):
        raise ValueError("Independent packets do not cover the locked diagnostic sample")
    packet_states = {}
    for card in cards:
        identifier(card["card_id"])
        packet_path = folder / "packets" / (card["card_id"] + ".json")
        if sha(packet_path) != card["packet_file_sha256"]:
            raise ValueError("Mapped independent packet changed")
        value = load(packet_path)
        if value.get("card_id") != card["card_id"] or canonical_hash(value.get("diagnosis")) != card["diagnosis_sha256"]:
            raise ValueError("Independent packet diagnosis identity differs")
        packet_states[card["card_id"]] = value.get("localization")
    rows = labels.get("cards", [])
    if len(rows) != len(ids) or {row.get("card_id") for row in rows} != ids:
        raise ValueError("Every independent diagnostic card needs exactly one final label row")
    keys = ("claim_support", "agent_responsibility", "correction_observability")
    def check_label(row):
        if row.get("card_id") not in ids:
            raise ValueError("Independent label references an unselected card")
        if row.get("localization") not in {"located", "unlocatable"} or any(row.get(k) not in {"supported", "unsupported", "insufficient"} for k in keys):
            raise ValueError("Independent label dimensions must preserve uncertainty")
        if row["localization"] != packet_states[row["card_id"]]:
            raise ValueError("Independent labels cannot change packet localization")
        if row["localization"] == "unlocatable" and any(row[k] != "insufficient" for k in keys):
            raise ValueError("Do not force a blame label on an unlocatable action")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip() or not isinstance(row.get("evidence"), list):
            raise ValueError("Independent labels require their original rationale and evidence")
    for row in rows:
        check_label(row)
    records = labels.get("reviewer_records", [])
    if rows and not records:
        raise ValueError("Preserve first reviewer labels, not only adjudicated labels")
    originals = {card_id: [] for card_id in ids}
    for record in records:
        source = within(folder, record["path"])
        if source == path or sha(source) != record["sha256"]:
            raise ValueError("First reviewer record changed or points to final labels")
        original = load(source)
        if not original.get("reviewer") or not isinstance(original.get("prior_familiarity"), str):
            raise ValueError("Reviewer identity and prior familiarity must be disclosed")
        first_rows = original.get("cards", [])
        if not first_rows or len({r.get("card_id") for r in first_rows}) != len(first_rows):
            raise ValueError("First reviewer records need distinct selected-card labels")
        for row in first_rows:
            check_label(row)
            originals[row["card_id"]].append(row)
    if any(not first for first in originals.values()):
        raise ValueError("First reviewer records must cover every selected card")
    for row in rows:
        if any(any(first[k] != row[k] for k in (*keys, "localization")) for first in originals[row["card_id"]]):
            note = labels.get("adjudication_notes", {}).get(row["card_id"])
            if not isinstance(note, str) or not note.strip():
                raise ValueError("Final label revisions require explicit adjudication notes")
    return labels_hash


def _verify_gate(root, directory, inputs, pool, selection_hash, manifest, *, digest, labels_hash=None, sealed=True):
    if sealed:
        _verify_seal(directory)
    row = load(directory / "outcome.json")
    if (row.get("status") != "complete" or row.get("binding") != inputs.binding
        or row.get("phase") != "gate" or row.get("manifest_sha256") != digest
        or row.get("selection_sha256") != selection_hash or row.get("pool_sha256") != canonical_hash(pool)
        or row.get("independent_labels_sha256") != labels_hash):
        raise ValueError("Gate does not bind this frozen selection/original input")
    client, ledger = _ledger_client(root, directory, "gate", manifest)
    expected_state = "review_unknown" if pool["selected_pool"] is None else ("empty_agent_pool" if not pool["selected_pool"] else "gated")
    if row.get("gate_status") != expected_state:
        raise ValueError("Gate unknown/empty/gated state differs")
    decisions, calls = [], []
    for offset, (index, diagnosis) in enumerate(zip(pool["selected_native_indices"] or [], pool["selected_pool"] or [])):
        side = directory / f"admission-{offset:04d}"
        request, outcome = load(side / "request.json"), load(side / "outcome.json")
        binding = {"diagnosis_sha256": canonical_hash(diagnosis), "trajectory_sha256": inputs.binding["trajectory_sha256"],
                   "policy_sha256": inputs.binding["policy_sha256"], "tools_sha256": inputs.binding["tools_sha256"]}
        if (request.get("binding") != binding or outcome.get("binding") != binding
            or request.get("diagnosis") != diagnosis or outcome.get("status") != "complete"):
            raise ValueError("Gate original diagnosis/action input binding differs")
        admission, judge_calls = outcome["admission"], outcome["judge_calls"]
        if len(judge_calls) != (1 if admission["judge_called"] else 0):
            raise ValueError("Gate model-call count differs")
        for call in judge_calls:
            verify_cli_call(client, call["cli_call_id"], role="feedback_admission", model_sidecar=call["directory"])
            if load(Path(call["directory"]) / "outcome.json")["output"] != admission["verdict"]:
                raise ValueError("Gate verdict differs from accepted model output")
            calls.append(call["cli_call_id"])
        def replay(_instruction, _payload):
            if not judge_calls:
                raise ValueError("Structural gate unexpectedly called a judge")
            return json.dumps(admission["verdict"])
        replayed = audit_diagnosis(diagnosis=diagnosis, messages=inputs.messages, policy=inputs.simulation.policy,
                                   tool_schemas=inputs.tools, judge=replay)
        if replayed.to_dict() != admission:
            raise ValueError("Gate does not replay on original action view")
        decisions.append({"native_index": index, **admission})
    if (decisions != row["decisions"] or len(calls) != len(set(calls))
        or set(calls) != {r["call_id"] for r in ledger["records"]}):
        raise ValueError("Gate decisions/ledger call coverage differs")
    return row


def _summary_ledger(directory):
    """Recover reservations after a hard interruption without declaring them free.

    This is accounting only, never a route to accepting/resuming an incomplete
    unit. A persisted client budget can precede a final call outcome.
    """
    path = directory / "ledger.json"
    if path.exists():
        return load(path)
    if not directory.exists():
        return None
    budget_path = directory / "client/budget.json"
    if not budget_path.exists():
        return {key: None for key in ("calls", "known_input_tokens", "known_output_tokens", "unknown_usage_calls")}
    budget = load(budget_path)
    calls = budget.get("calls_reserved")
    known = []
    for outcome_path in (directory / "client").glob("*/outcome.json"):
        outcome = load(outcome_path)
        usage = outcome.get("usage", {})
        if (outcome.get("usage_complete") is True and isinstance(usage, dict)
            and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens"))):
            known.append(usage)
    if type(calls) is not int or calls < len(known):
        return {key: None for key in ("calls", "known_input_tokens", "known_output_tokens", "unknown_usage_calls")}
    return {"calls": calls, "unknown_usage_calls": max(calls - len(known), budget.get("unknown_usage_calls", 0)),
        **{"known_" + key: max(sum(u[key] for u in known), budget.get("known_" + key, 0))
           for key in ("input_tokens", "output_tokens")}, "recovered_unfinalized_ledger": True}


def summarize(destination, manifest):
    """Retain all registered units, even unrun/unknown/stopped, in denominators."""
    rows = []
    for unit in manifest["units"]:
        item = {"unit_id": unit["unit_id"], "task_id": unit["task_id"],
                "user_validity": unit["user_validity"], "baseline_classification": unit["baseline_classification"]}
        ledgers = []
        for phase in ("review", "gate"):
            directory = destination / phase / unit["unit_id"]
            item[phase] = load(directory / "outcome.json") if (directory / "outcome.json").exists() else {"status": "unrun"}
            ledger = _summary_ledger(directory)
            if ledger is not None:
                ledgers.append(ledger)
        item["usage"] = {key: sum(l[key] for l in ledgers if type(l.get(key)) is int)
                         for key in ("calls", "known_input_tokens", "known_output_tokens", "unknown_usage_calls")}
        item["usage"]["ledgers_with_unavailable_usage"] = sum(l.get("calls") is None for l in ledgers)
        item["usage"]["recovered_unfinalized_ledgers"] = sum(l.get("recovered_unfinalized_ledger") is True for l in ledgers)
        rows.append(item)
    valid = [r["review"] for r in rows if r["review"].get("status") == "complete" and r["review"].get("native_review_status") == "valid"]
    gated = [r["gate"] for r in rows if r["gate"].get("status") == "complete"]
    decisions = [d for r in gated for d in r["decisions"]]
    result = {"trajectory_denominator": len(manifest["units"]), "task_denominator": len({u["task_id"] for u in manifest["units"]}),
        "rows": rows, "review_execution_counts": dict(Counter(r["review"]["status"] for r in rows)),
        "review_validity_counts": dict(Counter(r["review"].get("native_review_status", "not_complete") for r in rows)),
        "gate_execution_counts": dict(Counter(r["gate"]["status"] for r in rows)),
        "diagnosis_denominators": {"valid_review_trajectories": len(valid),
            "unknown_review_trajectories": sum(r["review"].get("native_review_status") == "unknown" for r in rows),
            "native_diagnoses_among_valid_reviews": sum(r["native_diagnoses"] for r in valid),
            "source_counts_among_valid_reviews": {s: sum(r["source_counts"][s] for r in valid) for s in ("agent", "user")},
            "selected_cards_among_valid_reviews": sum(len(r["selected_pool"]) for r in valid),
            "not_sampled_agent_cards_among_valid_reviews": sum(r["not_sampled"] for r in valid),
            "completed_gate_cards": len(decisions), "gate_decision_counts": dict(Counter(d["decision"] for d in decisions)),
            "structural_abstentions_without_model": sum(not d["judge_called"] for d in decisions),
            "completed_gate_unit_states": dict(Counter(r["gate_status"] for r in gated))},
        "usage": {key: sum(r["usage"][key] for r in rows) for key in rows[0]["usage"]},
        "scope": "Descriptive repeated-trajectory audit; no Agent uplift/semantic accuracy established"}
    if manifest.get("stage") == INTERRUPTED_STAGE:
        repeats = Counter(u["task_id"] for u in manifest["units"])
        result.update(parent_population_denominator=24, parent_task_denominator=12,
            parent_population=deepcopy(manifest["parent_population"]),
            parent_execution_counts=deepcopy(manifest["parent_execution_counts"]),
            parent_qualification_counts=dict(Counter(r["qualification"] for r in manifest["parent_population"])),
            parent_generation_cost_separate=deepcopy(manifest["parent_cost_known_subtotals"]),
            completed_task_repeat_counts=dict(Counter(repeats.values())),
            inference_limit="All 18 completed trajectories; 12 tasks (six twice, six once), not independent. "
                "Availability, fixed order and stopping time select this cohort. No extrapolation to the original 24, "
                "no 24-unit performance rate, no natural optimization comparison. The interrupted unit is ungraded, not a model failure.")
    return result


def run_phase(root, manifest_path, phase, max_new_units=2, *, client_factory=None, runtime_factory=NativeRuntime):
    if phase not in {"review", "gate"} or type(max_new_units) is not int or not 1 <= max_new_units <= 24:
        raise ValueError("Use review|gate and checkpoint size 1..24")
    root = Path(root).resolve()
    m, digest, destination = verify_manifest(root, manifest_path)
    if max_new_units > len(m["units"]):
        raise ValueError("Checkpoint size exceeds this registered cohort")
    if client_factory is None:
        if m["stage"] == INTERRUPTED_STAGE:
            from .subscription_http_client import HttpCliTextClient
            client_factory = HttpCliTextClient
        else:
            client_factory = CliTextClient
    with _exclusive(destination):
        current_unit = None
        try:
            runtime = runtime_factory(root, m)
            selection, selection_hash = _locked_selection(root, m, digest, destination, runtime) if phase == "gate" else (None, None)
            labels_hash = _locked_independent_labels(destination, selection, selection_hash) if phase == "gate" else None
            completed = 0
            for offset, unit in enumerate(m["units"]):
                current_unit = unit["unit_id"]
                directory = destination / phase / current_unit
                inputs = _inputs(root, unit, runtime)
                pool = selection["pools"][offset] if selection else None
                if directory.exists():
                    if phase == "review":
                        _verify_review(root, directory, unit, inputs, m, digest=digest)
                    else:
                        _verify_gate(root, directory, inputs, pool, selection_hash, m, digest=digest, labels_hash=labels_hash)
                    continue
                if completed >= max_new_units:
                    break
                if phase == "review" and (destination / "selection.json").exists():
                    raise RuntimeError("Cannot create new review after selection is frozen")
                directory.mkdir(parents=True, exist_ok=False)
                limits = m["per_phase_client_limits"][phase]
                row = {"status": "started", "phase": phase, "manifest_sha256": digest, "binding": inputs.binding,
                       "native_review_status": None, "diagnoses": None}
                client = None
                save(directory / "outcome.json", row)
                try:
                    client = client_factory(root, directory / "client", **limits)
                    if phase == "review":
                        review = review_simulation(client, simulation=inputs.simulation, task=inputs.task,
                            user_info=inputs.user_info, output=directory / "native_review")
                        row.update(native_review_status=review.status, diagnoses=None if review.diagnoses is None else list(review.diagnoses))
                        row.update(_pool(unit, row, m))
                    else:
                        row.update(selection_sha256=selection_hash, independent_labels_sha256=labels_hash, pool_sha256=canonical_hash(pool),
                            native_review_status=pool["native_review_status"],
                            gate_status="review_unknown" if pool["selected_pool"] is None else ("empty_agent_pool" if not pool["selected_pool"] else "gated"),
                            decisions=[])
                        for index, diagnosis in zip(pool["selected_native_indices"] or [], pool["selected_pool"] or []):
                            admission = audit_native_diagnosis(client, diagnosis=diagnosis, messages=inputs.messages,
                                policy=inputs.simulation.policy, tool_schemas=inputs.tools,
                                output=directory / f"admission-{len(row['decisions']):04d}")
                            row["decisions"].append({"native_index": index, **admission.to_dict()})
                    row["status"] = "complete"
                    save(directory / "ledger.json", _ledger(client, limits))
                    save(directory / "outcome.json", row)
                    if phase == "review":
                        _verify_review(root, directory, unit, inputs, m, digest=digest, sealed=False)
                    else:
                        _verify_gate(root, directory, inputs, pool, selection_hash, m, digest=digest, labels_hash=labels_hash, sealed=False)
                    _seal(directory)
                except BaseException as exc:
                    row.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
                    save(directory / "ledger.json", _ledger(client, limits))
                    save(directory / "outcome.json", row)
                    raise
                completed += 1
                print(json.dumps({"event": "n1_unit_complete", "phase": phase, "unit_id": current_unit}), flush=True)
        except BaseException as exc:
            _write_new(destination / "STOP.json", {"phase": phase, "unit_id": current_unit,
                "error_type": type(exc).__name__, "error": str(exc), "no_automatic_retry": True})
            raise
        finally:
            checkpoints = destination / "checkpoints"
            checkpoints.mkdir(exist_ok=True)
            _write_new(checkpoints / f"{phase}-{uuid.uuid4().hex}.json", summarize(destination, m))
    return summarize(destination, m)
