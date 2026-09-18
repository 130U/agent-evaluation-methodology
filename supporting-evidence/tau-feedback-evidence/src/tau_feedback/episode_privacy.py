"""Host-only lexical privacy inventory from public text-mode tau messages.

extract_episode_privacy(messages).literals can be supplied as the existing
EpisodeInput/EpisodeResult.protected_literals. No database, Task, policy, hidden
answer or model is read. Only public message content and tool arguments are
inspected; raw_data and other message/call metadata are ignored.

This is intentionally not NER or a semantic leakage guarantee. Isolated ASCII
name words (e.g. Will/May/Brown) and broad location words are not globally masked:
first+last names, explicit compound names, specific address lines and qualified
IDs are retained instead. Omitted fragments and unparsed tool data are reported.
Explicit ID fields may still collide with general words/numbers. The unchanged
GEPA projector also has its own broader ID/number rules; these remain separate.

The inventory contains sensitive literals and source paths: keep it in host
sidecars, never feed it to the reflection model. scan_instance_text reports value
hashes/spans, not raw literals or text. It uses the projector's case-insensitive
literal boundaries, not semantic matching; 'none_detected' never means safe.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re

from .contracts import strict_json


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,}(?![\w-])")
_ASCII_WORD = re.compile(r"[A-Za-z]+")
_ID_KEYS = {
    "id", "userid", "userids", "orderid", "orderids", "itemid", "itemids",
    "newitemid", "newitemids", "productid", "productids", "paymentid", "paymentids",
    "paymentmethodid", "paymentmethodids", "exchangepaymentmethodid", "returnpaymentmethodid",
    "trackingid", "trackingids", "transactionid", "transactionids",
}
_ID_LIST_KEYS = {"orders", "exchangeitems", "exchangenewitems", "returnitems"}
_ID_MAP_KEYS = {"paymentmethods", "variants"}
_PERSON_KEYS = {"fullname", "customername", "recipientname", "username", "loginname"}
_ADDRESS_KEYS = {"address", "shippingaddress", "billingaddress", "newaddress", "deliveryaddress"}
_LINE_KEYS = {"address1", "address2", "addressline1", "addressline2", "streetaddress", "street", "street1", "street2"}
_POSTAL_KEYS = {"zip", "zipcode", "postalcode", "postcode"}
_USER_LOOKUPS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
_LIMITATIONS = (
    "Only supplied public messages are inspected; completeness/provenance is the caller's responsibility.",
    "No semantic name/address recognition in free prose; unstructured names, aliases and paraphrases may remain.",
    "Standalone ASCII name parts and broad city/state/country words may remain to avoid masking ordinary policy words.",
    "Known literal matches use the existing projector's boundaries; punctuation/spacing/encoding changes may evade them.",
    "Explicit IDs can collide with ordinary words or small numbers; the existing projector has additional broader rules.",
)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PrivacyLiteral:
    value: str
    categories: tuple[str, ...]
    locations: tuple[str, ...]


@dataclass(frozen=True)
class PrivacyIssue:
    code: str
    location: str
    severity: str  # limitation or incomplete; neither is a semantic verdict.


@dataclass(frozen=True)
class EpisodePrivacy:
    literals: tuple[str, ...]
    findings: tuple[PrivacyLiteral, ...]
    issues: tuple[PrivacyIssue, ...]
    message_count: int

    def to_dict(self) -> dict:
        """Host-only inventory including raw sensitive values. Do not prompt it."""
        return {"schema_version": "episode-privacy-v1", **asdict(self),
                "semantic_leakage_guarantee": False, "limitations": list(_LIMITATIONS)}


def extract_episode_privacy(messages: list[dict]) -> EpisodePrivacy:
    """Read public user/assistant/tool text messages; never mutate their contents.

    Supports native tool_calls[].arguments and OpenAI function.arguments as dict
    or strict JSON. Tool content is strict JSON where available. Root scalar user
    IDs are recognized only when bound to a known find_user_id tool call/name.
    Emails are recognized in public prose; ordinary numbers/product names are not.
    Invalid nested payloads yield incomplete issues rather than silent omission.
    """
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise TypeError("Pass only a list of public message dictionaries, not a Task or database")
    values: dict[str, dict] = {}
    issues: list[PrivacyIssue] = []
    tool_names: dict[str, str] = {}

    def issue(code, location, severity="limitation"):
        item = PrivacyIssue(code, location, severity)
        if item not in issues:
            issues.append(item)

    def add(value, category, path):
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip()
        entry = values.setdefault(value.casefold(), {"value": value, "categories": set(), "locations": set()})
        entry["categories"].add(category)
        entry["locations"].add(path)

    def email_text(text, path):
        for match in _EMAIL.finditer(text):
            add(match.group(), "email", path)

    def identifier(value, path, category="identifier"):
        if isinstance(value, list):
            for i, item in enumerate(value):
                identifier(item, f"{path}[{i}]", category)
        elif isinstance(value, str) and value.strip():
            add(value, category, path)
            if _ASCII_WORD.fullmatch(value.strip()) or value.strip().isdigit() and len(value.strip()) < 3:
                issue("explicit_identifier_may_collide_with_general_text", path)
        elif type(value) is int and value >= 0:
            add(str(value), category, path)
            if value < 100:
                issue("explicit_identifier_may_collide_with_general_text", path)
        elif value is not None:
            issue("unsupported_identifier_value", path, "incomplete")

    def person(value, path, *, compound=False):
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip()
        # A whole first+last phrase is meaningful, but never globally suppress
        # every occurrence of the individual English words Will/May/etc.
        if len(value) < 2 or _ASCII_WORD.fullmatch(value):
            issue("standalone_name_or_username_omitted", path)
        else:
            add(value, "full_name" if compound else "name_or_username", path)

    def address_line(value, path):
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip()
        if len(value) < 3 or _ASCII_WORD.fullmatch(value) or value.isdigit():
            issue("ambiguous_address_fragment_omitted", path)
        else:
            add(value, "address_line", path)

    def walk(value, path, parent="", depth=0):
        if depth > 32:
            issue("structured_depth_limit", path, "incomplete")
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]", parent, depth + 1)
            return
        if isinstance(value, str):
            email_text(value, path)
            return
        if not isinstance(value, dict):
            return
        if any(not isinstance(k, str) for k in value):
            issue("non_string_object_key", path, "incomplete")
            return
        normalized = {_key(k): v for k, v in value.items()}
        first, last = normalized.get("firstname"), normalized.get("lastname")
        if isinstance(first, str) and first.strip() and isinstance(last, str) and last.strip():
            person(f"{first.strip()} {last.strip()}", path, compound=True)
            # This common rendering is a deterministic spelling variant, not NER.
            person(f"{last.strip()}, {first.strip()}", path, compound=True)
        is_person = parent in {"user", "customer", "recipient", "profile"} or bool(
            set(normalized) & {"userid", "email", "firstname", "lastname"})
        is_address = parent in _ADDRESS_KEYS or bool(set(normalized) & _LINE_KEYS)
        if is_address:
            city, state, postal = normalized.get("city"), normalized.get("state"), normalized.get("zip", normalized.get("postalcode"))
            if all(isinstance(part, str) and part.strip() for part in (city, state, postal)):
                add(f"{city.strip()}, {state.strip()} {postal.strip()}", "address_locality", path)
        for key, item in value.items():
            normal, child = _key(key), f"{path}.{key}"
            if normal in _ID_KEYS or normal in _ID_LIST_KEYS and isinstance(item, list) and all(not isinstance(v, dict) for v in item):
                identifier(item, child)
            elif normal in _ID_MAP_KEYS and isinstance(item, dict):
                for map_key in item:
                    identifier(map_key, f"{child}.<key>")
            elif normal in {"firstname", "lastname"}:
                person(item, child)
            elif normal in _PERSON_KEYS or normal == "name" and is_person and isinstance(item, str):
                person(item, child, compound=normal in {"fullname", "customername", "recipientname"})
            elif normal in _LINE_KEYS or normal in _ADDRESS_KEYS and isinstance(item, str):
                address_line(item, child)
            elif normal in _POSTAL_KEYS:
                if isinstance(item, str) and len(item.strip()) >= 3 and any(ch.isdigit() for ch in item):
                    add(item, "postal_code", child)
                elif type(item) is int and item >= 100:
                    add(str(item), "postal_code", child)
                elif item not in (None, ""):
                    issue("ambiguous_postal_code_omitted", child)
            elif normal in {"phone", "phonenumber", "telephone", "lastfour"}:
                if isinstance(item, (str, int)) and not isinstance(item, bool):
                    text = str(item)
                    if len(re.sub(r"\D", "", text)) >= (4 if normal == "lastfour" else 7):
                        add(text, "payment_suffix" if normal == "lastfour" else "phone", child)
            elif is_address and normal in {"city", "state", "country"}:
                if isinstance(item, str) and item.strip():
                    issue("broad_location_fragment_omitted", child)
            walk(item, child, normal, depth + 1)

    for i, message in enumerate(messages):
        path = f"messages[{i}]"
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("Only public user/assistant/tool messages are accepted")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("Only text-mode public content is supported")
        if message.get("is_audio") or message.get("audio") is not None or message.get("audio_content") is not None:
            raise ValueError("Audio content is outside this text-only extraction contract")
        if isinstance(content, str):
            email_text(content, f"{path}.content")
        calls = message.get("tool_calls")
        if calls is not None:
            if role != "assistant" or not isinstance(calls, list):
                raise ValueError("Expected an assistant tool-call list")
            for j, call in enumerate(calls):
                source = f"{path}.tool_calls[{j}]"
                if not isinstance(call, dict):
                    issue("invalid_tool_call", source, "incomplete")
                    continue
                body = call.get("function", call)
                if not isinstance(body, dict) or "function" in call and "arguments" in call:
                    issue("ambiguous_tool_call_shape", source, "incomplete")
                    continue
                name, call_id = body.get("name"), call.get("id")
                if isinstance(call_id, str) and isinstance(name, str):
                    if call_id in tool_names:
                        issue("duplicate_tool_call_id", source, "incomplete")
                        tool_names[call_id] = ""
                    else:
                        tool_names[call_id] = name
                arguments = body.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = strict_json(arguments)
                    except (ValueError, TypeError, RecursionError, UnicodeError):
                        issue("unparsed_tool_arguments", source, "incomplete")
                        email_text(body["arguments"], source)
                        continue
                if not isinstance(arguments, dict):
                    issue("non_object_tool_arguments", source, "incomplete")
                    continue
                walk(arguments, f"{source}.arguments")
        if role == "tool" and isinstance(content, str) and content.strip():
            source = f"{path}.content"
            link = message.get("tool_call_id") or message.get("id")
            linked_name = tool_names.get(link) if isinstance(link, str) else None
            name = linked_name if linked_name is not None else message.get("name")
            if linked_name is not None and message.get("name") not in (None, linked_name):
                issue("tool_name_link_mismatch", source, "incomplete")
                name = None
            parsed_ok = True
            try:
                parsed = strict_json(content)
            except (ValueError, TypeError, RecursionError, UnicodeError):
                parsed = None
                parsed_ok = False
                if isinstance(name, str) and name in _USER_LOOKUPS and not message.get("error") and re.fullmatch(r"[\w.@#+-]{3,}", content.strip()):
                    identifier(content.strip(), source, "user_identifier")
                else:
                    issue("unparsed_tool_content", source, "incomplete")
            if isinstance(parsed, str) and isinstance(name, str) and name in _USER_LOOKUPS and not message.get("error"):
                identifier(parsed, source, "user_identifier")
            elif isinstance(parsed, (dict, list)):
                walk(parsed, source)
            elif parsed_ok:
                issue("unstructured_tool_scalar_not_classified", source)

    findings = tuple(sorted((PrivacyLiteral(v["value"], tuple(sorted(v["categories"])), tuple(sorted(v["locations"])))
                             for v in values.values()), key=lambda item: (-len(item.value), item.value)))
    return EpisodePrivacy(tuple(item.value for item in findings), findings, tuple(issues), len(messages))


def scan_instance_text(text: str, privacy: EpisodePrivacy, *, label: str = "prompt",
                       max_spans_per_literal: int = 20) -> dict:
    """Scan actual rendered prompt or candidate text using GEPA's literal rules.

    Report-only: does not change text, invoke a model, certify privacy, or silently
    approve an artifact. Offsets are Python character positions, end exclusive.
    Matches for overlapping literals are counted separately, not as unique people.
    """
    if not isinstance(text, str) or not isinstance(privacy, EpisodePrivacy) or not isinstance(label, str):
        raise TypeError("Expected text, an EpisodePrivacy inventory and a label")
    if type(max_spans_per_literal) is not int or max_spans_per_literal < 1:
        raise ValueError("max_spans_per_literal must be a positive integer")
    matches = []
    for item in privacy.findings:
        pattern = re.escape(item.value)
        if item.value.isalnum():
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        count, spans = 0, []
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            count += 1
            if len(spans) < max_spans_per_literal:
                spans.append([match.start(), match.end()])
        if count:
            matches.append({"literal_sha256": _digest(item.value), "categories": list(item.categories),
                            "count": count, "spans": spans, "spans_truncated": count > len(spans)})
    return {"schema_version": "instance-literal-scan-v1", "label": label,
            "text_sha256": _digest(text), "text_characters": len(text),
            "status": "detected" if matches else "none_detected", "known_literal_count": len(privacy.literals),
            "matched_literal_count": len(matches), "match_occurrences": sum(m["count"] for m in matches),
            "matches": matches, "extraction_issue_count": len(privacy.issues),
            "incomplete_extraction_issue_count": sum(i.severity == "incomplete" for i in privacy.issues),
            "matching": "case-insensitive literals with unchanged GEPA _project boundary rules",
            "semantic_leakage_guarantee": False, "limitations": list(_LIMITATIONS)}
