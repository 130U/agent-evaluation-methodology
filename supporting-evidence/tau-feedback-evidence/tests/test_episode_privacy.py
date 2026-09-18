"""Public-message fake fixtures only: no Task, database, network or model reads."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tau_feedback.episode_privacy import extract_episode_privacy, scan_instance_text
from tau_feedback.gepa_adapter import _collect_private, _project


def tool_data(value, **extra):
    return [{"role": "tool", "content": json.dumps(value, ensure_ascii=False), **extra}]


def arguments(value, *, function=False):
    body = {"name": "lookup_or_modify", "arguments": value}
    call = {"id": "call-a", "function": body} if function else {"id": "call-a", **body}
    return [{"role": "assistant", "content": None, "tool_calls": [call]}]


class ExtractionTests(unittest.TestCase):
    def test_retail_json_result_extracts_names_addresses_ids_without_product_or_policy_words(self):
        messages = tool_data({
            "user_id": "customer_123", "name": {"first_name": "Will", "last_name": "May"},
            "email": "will@example.com", "username": "will_may", "orders": ["#12345", "order_alpha"],
            "address": {"address1": "123 Main Street", "address2": "Apt 7B", "city": "Boston",
                        "state": "MA", "country": "United States", "zip": "02116"},
            "payment_methods": {"credit_card_alpha": {"id": "credit_card_alpha", "source": "credit_card",
                                                     "brand": "visa", "last_four": "7788"}},
            "items": [{"item_id": "item_alpha", "product_id": "product_alpha", "name": "Blue Shirt",
                       "price": 39.99, "options": {"color": "blue", "size": "large"}}],
            "policy": "Ask for confirmation before changing an address.", "status": "pending", "quantity": 2,
        })
        privacy = extract_episode_privacy(messages)
        for literal in ("customer_123", "Will May", "May, Will", "will@example.com", "will_may", "#12345",
                        "order_alpha", "123 Main Street", "Apt 7B", "Boston, MA 02116", "02116",
                        "credit_card_alpha", "7788", "item_alpha", "product_alpha"):
            self.assertIn(literal, privacy.literals)
        for word in ("Will", "May", "Boston", "MA", "United States", "39.99", "2", "pending", "visa",
                     "credit_card", "blue", "large", "Blue Shirt", "Ask for confirmation before changing an address."):
            self.assertNotIn(word, privacy.literals)
        self.assertEqual(privacy.message_count, 1)

    def test_native_and_openai_arguments_support_objects_and_strict_json(self):
        payload = {"first_name": "May", "last_name": "Brown", "address1": "11 Garden Road",
                   "address2": "Suite 4", "user_id": "may_brown", "payment_id": "payment_alpha"}
        expected = extract_episode_privacy(arguments(payload)).literals
        for function in (False, True):
            for raw in (payload, json.dumps(payload)):
                with self.subTest(function=function, raw_type=type(raw).__name__):
                    self.assertEqual(extract_episode_privacy(arguments(raw, function=function)).literals, expected)

    def test_case_separator_key_variants_are_recognized(self):
        privacy = extract_episode_privacy(arguments({"First_Name": "John", "lastName": "Smith",
                                                    "Address_1": "10 Blue Lane", "addressLine2": "Floor 3",
                                                    "PAYMENT-ID": "pay_alpha", "postalCode": "A1A 1A1"}))
        self.assertTrue({"John Smith", "10 Blue Lane", "Floor 3", "pay_alpha", "A1A 1A1"}.issubset(privacy.literals))

    def test_id_arrays_map_keys_and_nested_values_are_extracted(self):
        privacy = extract_episode_privacy(tool_data({
            "item_ids": ["item_first", "item_second"], "new_item_ids": ["item_new"],
            "exchange_items": ["exchange_alpha"], "exchange_new_items": ["exchange_beta"],
            "return_items": ["return_alpha"], "exchange_payment_method_id": "payment_exchange",
            "return_payment_method_id": "payment_return", "tracking_id": ["tracking_alpha"],
            "variants": {"variant_key": {"item_id": "variant_item", "price": 1.25}},
            "payment_methods": {"payment_key": {"source": "paypal"}},
        }))
        for literal in ("item_first", "item_second", "item_new", "exchange_alpha", "exchange_beta", "return_alpha",
                        "payment_exchange", "payment_return", "tracking_alpha", "variant_key", "variant_item", "payment_key"):
            self.assertIn(literal, privacy.literals)
        self.assertNotIn("paypal", privacy.literals)
        self.assertNotIn("1.25", privacy.literals)

    def test_explicit_small_numeric_ids_are_not_all_numbers_and_collisions_are_disclosed(self):
        privacy = extract_episode_privacy(arguments({"item_id": 42, "quantity": 3, "price": 17.5, "balance": 1000}))
        self.assertEqual(privacy.literals, ("42",))
        self.assertIn("explicit_identifier_may_collide_with_general_text", [i.code for i in privacy.issues])
        self.assertEqual(scan_instance_text("Need 3 items costing 17.5, balance 1000.", privacy)["matches"], [])

    def test_boolean_or_float_ids_are_reported_not_stringified(self):
        privacy = extract_episode_privacy(arguments({"item_id": True, "product_id": 4.5}))
        self.assertEqual(privacy.literals, ())
        self.assertEqual(sum(i.code == "unsupported_identifier_value" for i in privacy.issues), 2)

    def test_email_in_public_prose_is_extracted_without_guessing_names(self):
        privacy = extract_episode_privacy([{"role": "user", "content": "I am Alice Green, email alice.green+shop@example.com."}])
        self.assertEqual(privacy.literals, ("alice.green+shop@example.com",))
        self.assertFalse(privacy.to_dict()["semantic_leakage_guarantee"])

    def test_generic_product_name_is_not_a_person_name(self):
        privacy = extract_episode_privacy(tool_data({"name": "Blue Garden Shirt", "product_id": "product_delta"}))
        self.assertEqual(privacy.literals, ("product_delta",))

    def test_compound_name_and_non_ascii_names_are_preserved_but_single_english_names_are_reported(self):
        privacy = extract_episode_privacy(tool_data({"full_name": "Mary Ann Smith-Jones", "first_name": "Mary Ann",
                                                   "last_name": "Smith-Jones", "username": "summer",
                                                   "customer_name": "王明", "recipient_name": "Will"}))
        for value in ("Mary Ann Smith-Jones", "Mary Ann", "Smith-Jones", "王明"):
            self.assertIn(value, privacy.literals)
        for value in ("summer", "Will"):
            self.assertNotIn(value, privacy.literals)
        self.assertTrue(any(i.code == "standalone_name_or_username_omitted" for i in privacy.issues))

    def test_common_name_words_do_not_erase_policy_sentences(self):
        privacy = extract_episode_privacy(arguments({"first_name": "Will", "last_name": "May"}))
        text = "The agent may ask, and will confirm before a return."
        self.assertEqual(_project(text, privacy.literals), text)
        self.assertEqual(scan_instance_text(text, privacy)["matches"], [])
        # Explicitly expose the separate legacy behavior, not claim to fix it.
        self.assertEqual(_collect_private({"first_name": "Will", "last_name": "May"}), {"Will", "May"})

    def test_ambiguous_address_fragments_are_not_global_literals(self):
        privacy = extract_episode_privacy(arguments({"address1": "Main", "address2": "2", "zip": "AB"}))
        self.assertEqual(privacy.literals, ())
        self.assertEqual(len(privacy.issues), 3)

    def test_full_string_address_phone_and_payment_suffix_are_supported(self):
        privacy = extract_episode_privacy(tool_data({"shipping_address": "15 Oak Road, Apt 4",
                                                    "phone_number": "+1 (617) 555-0199", "last_four": "0012"}))
        self.assertEqual(set(privacy.literals), {"15 Oak Road, Apt 4", "+1 (617) 555-0199", "0012"})

    def test_metadata_is_ignored_and_inputs_remain_unchanged(self):
        messages = [{"role": "user", "content": "Please help.", "raw_data": {"user_id": "HIDDEN_METADATA"},
                     "evaluation_criteria": {"order_id": "HIDDEN_GOLD"}, "usage": {"user_id": "HIDDEN_USAGE"}},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "call-a", "name": "lookup",
                        "arguments": {"user_id": "public_customer"}, "raw_data": {"user_id": "HIDDEN_CALL"}}]}]
        before = deepcopy(messages)
        privacy = extract_episode_privacy(messages)
        self.assertEqual(messages, before)
        self.assertEqual(privacy.literals, ("public_customer",))

    def test_known_user_lookup_scalar_is_linked_to_actual_call(self):
        for content in ("customer_alpha", json.dumps("customer_alpha")):
            messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "lookup-a",
                         "name": "find_user_id_by_email", "arguments": {"email": "a@example.com"}}]},
                        {"role": "tool", "id": "lookup-a", "content": content}]
            self.assertIn("customer_alpha", extract_episode_privacy(messages).literals)

    def test_scalar_status_and_error_messages_are_not_assumed_to_be_identifiers(self):
        for message in ({"role": "tool", "content": "Done"},
                        {"role": "tool", "content": '"pending"', "name": "get_order_details"},
                        {"role": "tool", "content": "USER_NOT_FOUND", "name": "find_user_id_by_email", "error": True}):
            with self.subTest(message=message):
                privacy = extract_episode_privacy([message])
                self.assertEqual(privacy.literals, ())
                self.assertTrue(privacy.issues)

    def test_duplicate_or_mismatched_tool_links_do_not_classify_scalar_identity(self):
        for duplicate in (True, False):
            messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "lookup-a",
                "name": "find_user_id_by_email", "arguments": {}}]}]
            if duplicate:
                messages.append(deepcopy(messages[0]))
            messages.append({"role": "tool", "id": "lookup-a", "name": "get_order_details", "content": '"customer_alpha"'})
            privacy = extract_episode_privacy(messages)
            self.assertNotIn("customer_alpha", privacy.literals)
            self.assertTrue(any(i.severity == "incomplete" for i in privacy.issues))

    def test_malformed_duplicate_key_or_nonfinite_json_is_not_silently_accepted(self):
        for raw in ('{"user_id": "alice",', '{"user_id":"alice","user_id":"bob"}', '{"user_id":NaN}'):
            for message in (arguments(raw)[0], {"role": "tool", "content": raw}):
                with self.subTest(raw=raw, role=message["role"]):
                    privacy = extract_episode_privacy([message])
                    self.assertEqual(privacy.literals, ())
                    self.assertTrue(any(i.severity == "incomplete" for i in privacy.issues))

    def test_no_eval_of_tool_content_or_arguments(self):
        raw = "__import__('os').system('model_call')"
        privacy = extract_episode_privacy(arguments(raw) + [{"role": "tool", "content": raw}])
        self.assertEqual(privacy.literals, ())
        self.assertEqual(sum(i.severity == "incomplete" for i in privacy.issues), 2)

    def test_only_public_text_message_list_is_accepted(self):
        for invalid in ({"messages": []}, [{"role": "system", "content": "policy"}],
                        [{"role": "tool", "content": {"user_id": "raw"}}],
                        [{"role": "user", "content": "hello", "audio_content": "data"}],
                        [{"role": "user", "content": "hello", "tool_calls": []}]):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, TypeError)):
                extract_episode_privacy(invalid)

    def test_findings_deduplicate_case_insensitively_and_preserve_multiple_locations(self):
        privacy = extract_episode_privacy(arguments({"user_id": "Mixed_Customer"}) + tool_data({"user_id": "mixed_customer"}))
        self.assertEqual(privacy.literals, ("Mixed_Customer",))
        self.assertEqual(len(privacy.findings[0].locations), 2)
        self.assertEqual(json.loads(json.dumps(privacy.to_dict()))["literals"], ["Mixed_Customer"])


class ActualTextScanTests(unittest.TestCase):
    def test_actual_prompt_and_candidate_report_hashes_positions_without_raw_sensitive_text(self):
        privacy = extract_episode_privacy(arguments({"first_name": "Will", "last_name": "May", "user_id": "known_customer"}))
        text = "Feedback: WILL MAY asked for an update. Strategy: remember known_customer."
        report = scan_instance_text(text, privacy, label="candidate")
        self.assertEqual(report["status"], "detected")
        self.assertEqual(report["matched_literal_count"], 2)
        self.assertEqual(report["text_sha256"], hashlib.sha256(text.encode()).hexdigest())
        serialized = json.dumps(report)
        self.assertNotIn("Will May", serialized)
        self.assertNotIn("known_customer", serialized)
        self.assertNotIn(text, serialized)
        self.assertFalse(report["semantic_leakage_guarantee"])
        clean = _project(text, privacy.literals)
        self.assertEqual(scan_instance_text(clean, privacy, label="reflection_prompt")["matches"], [])

    def test_alphanumeric_boundaries_match_existing_projector(self):
        privacy = extract_episode_privacy(arguments({"user_id": "Ann", "item_id": 42}))
        text = "Ann annex Annabelle ANN 42 142 42x x42"
        report = scan_instance_text(text, privacy)
        self.assertEqual(report["match_occurrences"], 3)
        self.assertEqual(_project(text, privacy.literals), "[REDACTED] annex Annabelle [REDACTED] [REDACTED] [REDACTED_ID] 42x [REDACTED_ID]")

    def test_punctuation_matching_follows_frozen_projector_even_inside_longer_text(self):
        privacy = extract_episode_privacy(arguments({"user_id": "a+b"}))
        text = "xa+by A+B"
        report = scan_instance_text(text, privacy)
        self.assertEqual(report["match_occurrences"], 2)
        self.assertEqual(_project(text, privacy.literals), "x[REDACTED]y [REDACTED]")

    def test_span_cap_does_not_hide_total_match_count(self):
        privacy = extract_episode_privacy(arguments({"user_id": "known_customer"}))
        report = scan_instance_text("known_customer " * 5, privacy, max_spans_per_literal=2)
        self.assertEqual(report["match_occurrences"], 5)
        self.assertEqual(len(report["matches"][0]["spans"]), 2)
        self.assertTrue(report["matches"][0]["spans_truncated"])

    def test_none_detected_retains_uncovered_and_parse_limitations(self):
        privacy = extract_episode_privacy(arguments('{"first_name":'))
        report = scan_instance_text("A paraphrased hidden detail can still appear.", privacy)
        self.assertEqual(report["status"], "none_detected")
        self.assertEqual(report["matches"], [])
        self.assertEqual(report["incomplete_extraction_issue_count"], 1)
        self.assertFalse(report["semantic_leakage_guarantee"])
        self.assertTrue(report["limitations"])

    def test_empty_public_messages_and_empty_text_are_valid_without_safety_claim(self):
        privacy = extract_episode_privacy([])
        report = scan_instance_text("", privacy)
        self.assertEqual(report["known_literal_count"], 0)
        self.assertEqual(report["matches"], [])
        self.assertNotIn("safe", report)

    def test_invalid_scan_parameters_are_rejected(self):
        privacy = extract_episode_privacy([])
        for kwargs in ({"max_spans_per_literal": 0}, {"max_spans_per_literal": True}, {"label": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                scan_instance_text("", privacy, **kwargs)


if __name__ == "__main__":
    unittest.main()
