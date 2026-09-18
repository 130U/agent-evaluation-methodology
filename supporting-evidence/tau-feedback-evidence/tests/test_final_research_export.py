"""Temporary fake public package and transforms; never export real research."""
import ast
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_final_research_package as export


class FinalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="final-export-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("subprocess.run", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("No process, model, Git or network"))
            guard.start(); self.addCleanup(guard.stop)

    def put(self, name, raw):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def inherited_fixture(self):
        name = str(export.PACKAGE / "README.md")
        data = b"# Public fixture\n"
        manifest = {"files": [{"destination": name, "export_sha256": export.original.digest(data),
            "original_sha256": export.original.digest(data), "source": "README.md", "transformations": []}]}
        manifest_raw = export.encoded(manifest); manifest_sha = export.original.digest(manifest_raw)
        files = {name: data, str(export.MANIFEST_PATH): manifest_raw,
            str(export.SIDECAR_PATH): (manifest_sha + "  PUBLIC_EXPORT_MANIFEST.json\n").encode()}
        edits = {"README.md": b"# Original ALE index plus one tau row\n",
                 export.repository.MANIFEST: b'{"ALE":"preserved"}\n'}
        proposal = {"base_commit": export.repository.BASE_COMMIT, "base_tree": export.repository.BASE_TREE,
            "new_files": [{"path": n, "sha256": export.original.digest(b)} for n, b in files.items()],
            "existing_file_modifications": [{"path": n, "after_sha256": export.original.digest(b)} for n, b in edits.items()]}
        raw = export.encoded(proposal)
        for n, b in {**files, **edits}.items(): self.put(export.PREVIOUS + "/repository-overlay/" + n, b)
        self.put(export.PREVIOUS + "/PROPOSED_CHANGE.json", raw)
        return export.original.digest(raw), manifest_sha

    def test_inherited_exact_public_bytes_and_tamper_rejection(self):
        proposal, manifest = self.inherited_fixture()
        files, records, identity = export.inherit_verified(self.root, proposal_sha256=proposal, manifest_sha256=manifest)
        self.assertEqual(len(files), 3)
        self.assertEqual(len(records), 1)
        self.assertEqual(identity["original_overlay_files"], 5)
        self.put(export.PREVIOUS + "/repository-overlay/" + str(export.PACKAGE / "README.md"), b"changed")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            export.inherit_verified(self.root, proposal_sha256=proposal, manifest_sha256=manifest)

    def test_unlisted_inherited_file_rejected(self):
        proposal, manifest = self.inherited_fixture()
        self.put(export.PREVIOUS + "/repository-overlay/unlisted.txt", b"unexpected")
        with self.assertRaisesRegex(ValueError, "inventory"):
            export.inherit_verified(self.root, proposal_sha256=proposal, manifest_sha256=manifest)

    def test_report_redaction_records_original_hash_and_preserves_numbers(self):
        # Use this temp root, including JSON escaping; no real client file read.
        name = "results/C1_SYNTHETIC_REPORT.json"
        raw = export.encoded({"transport_audit": {"client_directory": str(self.root / "private-client")}, "count": 12})
        output, changes = export.redact_text(self.root, name, raw, {name: export.PACKAGE / name})
        value = json.loads(output)
        self.assertEqual(value["count"], 12)
        self.assertNotIn(str(self.root), output.decode())
        self.assertIn("LOCAL_RESEARCH", value["transport_audit"]["client_directory"])
        self.assertEqual(value["public_derivation"]["original_sha256"], export.original.digest(raw))
        self.assertTrue(changes)

    def test_source_code_and_frozen_label_transform_fail_closed(self):
        name = "src/tau_feedback/example.py"
        raw = b"def answer():\n    return 42\n"
        output, _ = export.redact_text(self.root, name, raw, {name: export.PACKAGE / name})
        self.assertEqual(ast.dump(ast.parse(raw)), ast.dump(ast.parse(output)))
        label = "research/G4_COHORT_RELATED_BLIND_LABELS.json"
        raw = export.encoded({"path": str(self.root / "fixture")})
        with self.assertRaisesRegex(ValueError, "bytes need separate"):
            export.redact_text(self.root, label, raw, {label: export.PACKAGE / label})

    def test_patterns_bad_python_json_empty_and_path_rejected(self):
        unsafe = ("C:" + "/" + "Users/" + "example/private").encode()
        for name, raw in (("a.md", unsafe), ("a.py", b"def :"), ("a.json", b"{"), ("a.txt", b""), ("bad name.txt", b"x")):
            with self.subTest(name=name), self.assertRaises((ValueError, SyntaxError)):
                export.scan_public(name, raw)

    def test_only_explicit_allowlist_excludes_original_sensitive_categories(self):
        names = [*export.FIXED, *export.RESULTS]
        self.assertFalse(any("HOST_MAPPING" in n or n.startswith(("results/g4/", "results/c1/", "models/", "vendor/")) for n in names))
        self.assertFalse(any(n.endswith(".zip") or n.endswith("MANIFEST.json") for n in names))
        self.assertEqual(len(export.CORE_UPDATES), 4)

    def test_write_once_owned_overlay_and_byte_readback(self):
        planned = {PurePosixPath("core/03-tau-feedback-evidence/fixture.md"): b"# Fixture only\n"}
        destination = export.write_overlay(self.root, planned)
        self.assertEqual((destination / next(iter(planned))).read_bytes(), next(iter(planned.values())))
        with self.assertRaisesRegex(ValueError, "no overwrite"):
            export.write_overlay(self.root, planned)


if __name__ == "__main__":
    unittest.main()
