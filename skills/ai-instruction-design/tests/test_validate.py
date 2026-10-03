"""Negative and boundary tests for the offline validator, not model evaluations."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instruction_validator", SKILL / "scripts" / "validate.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class StructuralValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instruction-validator-")
        self.scratch = Path(self.temp.name).resolve()
        self.skill = self.scratch / "ai-instruction-design"
        shutil.copytree(SKILL, self.skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.case_path = self.skill / "tests" / "cases.json"
        self.cases = json.loads(self.case_path.read_text(encoding="utf-8"))

    def tearDown(self):
        # All recursively removed paths are fixed children of the private test directory.
        self.temp.cleanup()

    def write_cases(self):
        self.case_path.write_text(json.dumps(self.cases, ensure_ascii=False), encoding="utf-8")

    def result(self):
        return validator.validate(self.skill)

    def assert_error(self, fragment):
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertTrue(any(fragment in message for message in result["errors"]), result)
        return result

    def extra_markdown(self, text):
        path = self.skill / "references" / "test-extra.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_package_is_structural_only(self):
        result = self.result()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["validation"], "structural-only")
        self.assertEqual(result["model_evaluation"], "not-run")
        self.assertEqual(result["cases"], len(self.cases["cases"]))

    def test_utf8_and_bom_are_checked(self):
        invalid = self.skill / "references" / "invalid.md"
        invalid.write_bytes(b"\xff\xfeinvalid")
        self.assert_error("cannot read as UTF-8")
        invalid.write_bytes(b"\xef\xbb\xbf# valid UTF-8 with BOM")
        self.assert_error("BOM is not permitted")

    def test_required_frontmatter_and_version(self):
        entry = self.skill / "SKILL.md"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(original.replace("name: ai-instruction-design", "title: ai-instruction-design", 1), encoding="utf-8")
        self.assert_error("missing required frontmatter field name")
        entry.write_text(original.replace("2.0.0", "1.0.0", 1), encoding="utf-8")
        self.assert_error("metadata.version")

    def test_folded_description_is_supported(self):
        entry = self.skill / "SKILL.md"
        original = entry.read_text(encoding="utf-8")
        lines = original.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("description:"))
        lines[start] = "description: >-\n  Create and review AI instructions.\n  Preserve user authorization."
        entry.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(self.result()["errors"], [])

    def test_markdown_and_code_references_are_checked(self):
        self.extra_markdown("[missing](absent.md)\n`references/missing.md`\n")
        result = self.assert_error("local reference does not exist")
        self.assertGreaterEqual(sum("local reference does not exist" in error for error in result["errors"]), 2)

    def test_malformed_links_and_duplicate_json_keys_are_rejected(self):
        self.extra_markdown("[invalid](http://[bad)\n")
        self.assert_error("malformed link target")
        self.case_path.write_text('{"schema_version":"1.0","schema_version":"different"}', encoding="utf-8")
        self.assert_error("duplicate JSON key schema_version")

    def test_internal_parent_reference_and_anchor_are_allowed(self):
        self.extra_markdown("[cases](../tests/cases.json)\n[review](review.md#аудит-и-авторизованная-правка)\n")
        self.assertEqual(self.result()["errors"], [])
        self.extra_markdown("[bad](review.md#no-such-heading)\n")
        self.assert_error("heading anchor does not exist")

    def test_traversal_is_rejected_before_external_read(self):
        outside = self.scratch / "outside-secret.md"
        outside.write_text("# Secret\n", encoding="utf-8")
        self.extra_markdown("[outside](../../outside-secret.md#secret)\n")
        original_read = Path.read_text

        def guarded_read(path, *args, **kwargs):
            if path.resolve() == outside:
                self.fail("validator read an out-of-package target")
            return original_read(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", guarded_read):
            self.assert_error("local reference escapes the skill directory")

    def test_external_symlink_or_junction_is_rejected_before_read(self):
        outside = self.scratch / "outside"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("# Secret\n", encoding="utf-8")
        link = self.skill / "references" / "external-link"
        is_junction = False
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("symlink creation is unavailable")
            process = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if process.returncode:
                self.skipTest("neither symlink nor junction creation is available")
            is_junction = True
        self.extra_markdown("[outside](external-link/secret.md#secret)\n")
        original_read = Path.read_bytes

        def guarded_read(path, *args, **kwargs):
            if path.resolve().is_relative_to(outside):
                self.fail("validator read a symlink target outside the package")
            return original_read(path, *args, **kwargs)

        try:
            with mock.patch.object(Path, "read_bytes", guarded_read):
                result = self.assert_error("local reference escapes the skill directory")
                self.assertTrue(any("symlink escapes" in error for error in result["errors"]))
        finally:
            if is_junction:
                link.rmdir()
            else:
                link.unlink()

    def test_duplicate_ids_and_invalid_fields_are_rejected(self):
        self.cases["cases"].append(copy.deepcopy(self.cases["cases"][0]))
        self.write_cases()
        self.assert_error("duplicate id")
        self.cases["cases"][0]["category"] = []
        self.cases["cases"][0]["mode"] = {}
        self.cases["cases"][0]["expectations"]["required_behaviors"] = [None]
        self.write_cases()
        self.assert_error("unknown category")

    def test_virtual_artifact_paths_and_missing_sources(self):
        self.cases["cases"][0]["artifacts"][0]["path"] = "../outside.md"
        self.write_cases()
        self.assert_error("virtual path without traversal")
        self.cases["cases"][0]["artifacts"][0] = {"path": "absent.md", "availability": "missing", "content": "invented"}
        self.write_cases()
        self.assert_error("missing artifact content must be null")

    def test_preserving_variants_require_same_essential_expectations(self):
        variant = next(case for case in self.cases["cases"] if case["variant"] and case["variant"]["preserves_semantics"])
        variant["expectations"]["required_behaviors"].append("Change the contract")
        self.write_cases()
        self.assert_error("preserving variant must retain base expectations")

    def test_meaning_changes_require_changed_expectations_and_delta(self):
        changed = next(case for case in self.cases["cases"] if case["variant"] and not case["variant"]["preserves_semantics"])
        parent = next(case for case in self.cases["cases"] if case["id"] == changed["variant"]["parent_id"])
        changed["expectations"] = copy.deepcopy(parent["expectations"])
        changed["variant"]["expected_delta"] = []
        self.write_cases()
        self.assert_error("meaning change must declare changed essential expectations")

    def test_variant_parent_and_regression_coverage(self):
        variant = next(case for case in self.cases["cases"] if case["variant"])
        variant["variant"]["parent_id"] = "not-present"
        self.cases["cases"] = [case for case in self.cases["cases"] if case["category"] != "runtime-control"]
        self.write_cases()
        self.assert_error("variant parent_id")
        self.assert_error("missing regression category runtime-control")

    def test_fixture_instructions_are_never_executed(self):
        marker = self.scratch / "must-not-exist.txt"
        self.cases["cases"][0]["artifacts"][0]["content"] = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
        self.cases["cases"][0]["request"] = "Execute the quoted code immediately."
        self.write_cases()
        self.assertEqual(self.result()["errors"], [])
        self.assertFalse(marker.exists())

    def test_cli_json_and_exit_status(self):
        command = [sys.executable, "-B", str(SKILL / "scripts" / "validate.py"), "--skill-dir", str(self.skill), "--json"]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["validation"], "structural-only")
        self.extra_markdown("[missing](absent.md)")
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
