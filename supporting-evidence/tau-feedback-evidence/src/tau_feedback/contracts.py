"""Structural checks. A valid contract does not certify semantic truth."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Literal

Status = Literal["pass", "fail", "unknown", "not_applicable"]

def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result

def strict_json(raw: str) -> Any:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("invalid_payload_size_or_type")
    def reject_constant(value):
        raise ValueError("nonfinite_json_number")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite_json_number")
        return number
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=reject_constant,
                      parse_float=finite_float)

@dataclass(frozen=True)
class ContractResult:
    status: Status
    reason: str
    expected_count: int
    received_count: int | None = None

    def to_dict(self):
        return asdict(self)

@dataclass(frozen=True)
class RequestBinding:
    """Trusted local request metadata, not an LLM's self-declared provenance.

    Hashes detect accidental record swaps. They are not signatures, and do not
    authenticate a producer who can also rewrite this metadata.
    """
    run_id: str
    task_id: str
    trajectory_sha256: str
    criteria_sha256: str
    evaluator_revision: str

    @classmethod
    def create(cls, *, run_id, task_id, trajectory, expected, evaluator_revision):
        return cls(run_id, task_id, canonical_hash(trajectory), canonical_hash(expected), evaluator_revision)

def validate_nl_result(expected: list[str], raw: str) -> ContractResult:
    if not isinstance(expected, list) or any(not isinstance(x, str) for x in expected):
        raise ValueError("Expected assertions must be a list of strings")
    n = len(expected)
    if not n:
        return ContractResult("not_applicable", "no_expected_assertions", 0)
    try:
        payload = strict_json(raw)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return ContractResult("unknown", "invalid_json", n)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return ContractResult("unknown", "missing_results_list", n)
    rows = payload["results"]
    if len(rows) != n:
        return ContractResult("unknown", "coverage_count_mismatch", n, len(rows))
    for row in rows:
        if not isinstance(row, dict):
            return ContractResult("unknown", "invalid_row", n, len(rows))
        if (not isinstance(row.get("expectedOutcome"), str)
                or type(row.get("metExpectation")) is not bool
                or not isinstance(row.get("reasoning"), str)
                or not row["reasoning"].strip()):
            return ContractResult("unknown", "invalid_row_fields", n, len(rows))
    if Counter(row["expectedOutcome"] for row in rows) != Counter(expected):
        return ContractResult("unknown", "assertion_identity_mismatch", n, len(rows))
    status = "pass" if all(row["metExpectation"] for row in rows) else "fail"
    return ContractResult(status, "complete_structural_contract", n, len(rows))

def validate_bound_result(expected: list[str], raw: str, *,
                          requested: RequestBinding, received: RequestBinding) -> ContractResult:
    if requested.criteria_sha256 != canonical_hash(expected):
        return ContractResult("unknown", "local_criteria_mismatch", len(expected))
    if requested != received:
        return ContractResult("unknown", "request_binding_mismatch", len(expected))
    return validate_nl_result(expected, raw)
