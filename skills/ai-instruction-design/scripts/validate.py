#!/usr/bin/env python3
"""Read-only structural validation. No model calls or fixture execution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

VERSION = "2.0.0"
TEXT_SUFFIXES = {".md", ".json", ".py", ".yaml", ".yml", ".txt", ".toml", ".csv"}
CATEGORIES = {
    "read-only-review", "adaptation", "untrusted-content", "missing-context",
    "authorization", "runtime-control", "semantic-preservation", "profiles",
    "evaluator", "scope",
}
MODES = {"write", "adapt", "review", "compact", "evaluate"}
ACTIONS = {"read", "edit-local", "write-external", "publish", "paid-call"}
SLICES = {
    "authorization", "untrusted-sources", "contract-preservation", "truthfulness",
    "runtime-control", "review-quality", "routing", "evaluation-validity",
}
TRANSFORMS = {"paraphrase", "format", "reorder-independent", "irrelevant-content", "meaning-change"}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
LINK = re.compile(r"\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+[\"'][^\n]*?[\"'])?\s*\)")
DEFINITION = re.compile(r"^\s*\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)", re.MULTILINE)
CODE_PATH = re.compile(r"`((?:references|profiles|scripts|tests|assets|agents)/[^`\n]+|\.\./[^`\n]+)`")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject silently overwritten fixture fields instead of interpreting them."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def scalar(value: str) -> str:
    """Decode a required simple YAML string; not a general YAML interpreter."""
    value = re.sub(r"\s+#.*$", "", value).strip()
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else ""
        except (ValueError, TypeError):
            return ""
    if value.startswith("'"):
        return value[1:-1].replace("''", "'") if value.endswith("'") else ""
    if value in {"", "null", "~", "true", "false", "[]", "{}"} or value.startswith(("[", "{")):
        return ""
    return value


def check_frontmatter(text: str, folder: Path, errors: list[str]) -> None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        errors.append("SKILL.md: frontmatter must start with --- on the first line")
        return
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append("SKILL.md: frontmatter closing --- is missing")
        return
    fields: dict[str, tuple[str, int]] = {}
    for i in range(1, end):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):(?:\s+(.*))?", line)
        if not match:
            errors.append(f"SKILL.md:{i + 1}: malformed top-level frontmatter field")
            continue
        key, value = match.group(1), match.group(2) or ""
        if key in fields:
            errors.append(f"SKILL.md: duplicate frontmatter field {key}")
        fields[key] = (value, i)
    for key in ("name", "description"):
        if key not in fields:
            errors.append(f"SKILL.md: missing required frontmatter field {key}")
            continue
        value, i = fields[key]
        if key == "description" and value in {">", "|", ">-", "|-", ">+", "|+"}:
            block = []
            for line in lines[i + 1:end]:
                if line.strip() and not line[0].isspace():
                    break
                block.append(line.strip())
            value = " ".join(block).strip()
        else:
            value = scalar(value)
        if not value:
            errors.append(f"SKILL.md: {key} must be a nonempty string")
        if key == "name" and (not IDENTIFIER.fullmatch(value) or len(value) > 64 or value != folder.name):
            errors.append("SKILL.md: name must be lowercase hyphenated and match the skill folder")
    if "metadata" not in fields:
        errors.append("SKILL.md: required metadata.version is missing")
        return
    raw, index = fields["metadata"]
    versions = []
    if raw.strip().startswith("{"):
        match = re.fullmatch(r"\{\s*version:\s*(.*?)\s*\}", raw.strip())
        if match:
            versions.append(scalar(match.group(1)))
    elif not raw.strip():
        for line in lines[index + 1:end]:
            if line.strip() and not line[0].isspace():
                break
            match = re.fullmatch(r" {2}version:\s*(.*)", line)
            if match:
                versions.append(scalar(match.group(1)))
    if versions != [VERSION]:
        errors.append(f"SKILL.md: metadata.version must occur once and equal {VERSION}")


def anchors(text: str) -> set[str]:
    found: set[str] = set(re.findall(r"\bid=[\"']([^\"']+)[\"']", text))
    counts: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
        if in_fence:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not heading:
            continue
        slug = re.sub(r"[^\w\s-]", "", heading.group(1).lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        found.add(slug if count == 0 else f"{slug}-{count}")
        counts[slug] = count + 1
    return found


def check_references(path: Path, text: str, texts: dict[Path, str], errors: list[str], skill_dir: Path) -> int:
    references = [(m.group(1), m.start(), pattern is CODE_PATH) for pattern in (LINK, DEFINITION, CODE_PATH) for m in pattern.finditer(text)]
    seen = set()
    count = 0
    for raw, offset, is_code in references:
        raw = raw.strip("<>")
        # Inline code may document a command. Only exact path literals are references.
        if is_code and any(ch.isspace() for ch in raw):
            continue
        if raw in seen or "*" in raw or "{" in raw or "<" in raw:
            continue
        seen.add(raw)
        line = text.count("\n", 0, offset) + 1
        label = f"{path.name}:{line}: {raw}"
        try:
            parsed = urlsplit(raw)
        except ValueError:
            errors.append(f"{label}: malformed link target")
            continue
        if parsed.scheme or parsed.netloc:
            continue
        target_name = unquote(parsed.path)
        if target_name.startswith(("/", "\\")) or "\\" in target_name:
            errors.append(f"{label}: local reference must be relative and use / separators")
            continue
        try:
            target = (path.parent / target_name).resolve() if target_name else path
        except (OSError, RuntimeError):
            errors.append(f"{label}: local reference cannot be resolved")
            continue
        count += 1
        if not target.is_relative_to(skill_dir):
            errors.append(f"{label}: local reference escapes the skill directory")
            continue
        if not target.exists():
            errors.append(f"{label}: local reference does not exist")
        elif parsed.fragment and target.suffix.lower() == ".md":
            if target in texts:
                target_text = texts[target]
            else:
                try:
                    target_text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    errors.append(f"{label}: target cannot be read as UTF-8")
                    continue
            if unquote(parsed.fragment) not in anchors(target_text):
                errors.append(f"{label}: heading anchor does not exist")
    return count


def strings(value: object, location: str, errors: list[str], allow_empty: bool = False) -> bool:
    if not isinstance(value, list) or (not value and not allow_empty):
        errors.append(f"{location}: expected {'possibly empty ' if allow_empty else 'nonempty '}list of strings")
        return False
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{location}: each item must be a nonempty string")
        return False
    if len(value) != len(set(value)):
        errors.append(f"{location}: duplicate items")
        return False
    return True


def check_cases(data: object, errors: list[str]) -> tuple[int, int]:
    location = "tests/cases.json"
    if not isinstance(data, dict):
        errors.append(f"{location}: root must be an object")
        return 0, 0
    if data.get("schema_version") != "1.0" or data.get("skill_version") != VERSION:
        errors.append(f"{location}: schema_version must be 1.0 and skill_version must be {VERSION}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append(f"{location}: cases must be a nonempty list")
        return 0, 0
    by_id = {}
    families: dict[str, list[dict]] = {}
    covered = set()
    transforms = set()
    for index, case in enumerate(cases):
        here = f"{location}:case[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{here}: case must be an object")
            continue
        for key in ("id", "family"):
            if not isinstance(case.get(key), str) or not IDENTIFIER.fullmatch(case[key]):
                errors.append(f"{here}: {key} must be a lowercase hyphenated identifier")
        identifier = case.get("id")
        if isinstance(identifier, str):
            if identifier in by_id:
                errors.append(f"{here}: duplicate id {identifier}")
            by_id[identifier] = case
            here += f"/{identifier}"
        family = case.get("family")
        if isinstance(family, str):
            families.setdefault(family, []).append(case)
        if not isinstance(case.get("category"), str) or case["category"] not in CATEGORIES:
            errors.append(f"{here}: unknown category")
        else:
            covered.add(case["category"])
        if not isinstance(case.get("mode"), str) or case["mode"] not in MODES:
            errors.append(f"{here}: unknown mode")
        if not isinstance(case.get("request"), str) or not case["request"].strip():
            errors.append(f"{here}: request must contain raw nonempty text")
        for key, allowed in (("authorized_actions", ACTIONS), ("critical_slices", SLICES)):
            if strings(case.get(key), f"{here}/{key}", errors):
                if not set(case[key]) <= allowed:
                    errors.append(f"{here}/{key}: unknown value")
        if not isinstance(case.get("authorized_actions"), list) or "read" not in case["authorized_actions"]:
            errors.append(f"{here}: authorized_actions must include read")
        expectations = case.get("expectations")
        if not isinstance(expectations, dict):
            errors.append(f"{here}: expectations must be an object")
        else:
            for key in ("required_behaviors", "forbidden_behaviors", "protected_decisions"):
                strings(expectations.get(key), f"{here}/expectations/{key}", errors)
        artifacts = case.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append(f"{here}: artifacts must contain raw fixture objects")
        else:
            paths = set()
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    errors.append(f"{here}: artifact must be an object")
                    continue
                name = artifact.get("path")
                if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                        or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts):
                    errors.append(f"{here}: artifact path must be a relative virtual path without traversal")
                elif name in paths:
                    errors.append(f"{here}: duplicate artifact path {name}")
                else:
                    paths.add(name)
                availability = artifact.get("availability")
                content = artifact.get("content")
                if availability == "available":
                    if not isinstance(content, str) or not content.strip():
                        errors.append(f"{here}: available artifact needs raw nonempty content")
                elif availability == "missing":
                    if content is not None:
                        errors.append(f"{here}: missing artifact content must be null")
                else:
                    errors.append(f"{here}: artifact availability must be available or missing")
        context = case.get("context")
        if not isinstance(context, list):
            errors.append(f"{here}: context must be a list")
        else:
            for item in context:
                if (not isinstance(item, dict) or not isinstance(item.get("source"), str)
                        or not item.get("source") or type(item.get("trusted")) is not bool
                        or not isinstance(item.get("content"), str) or not item.get("content").strip()):
                    errors.append(f"{here}: context item needs source, trusted boolean and raw content")
        if "variant" not in case:
            errors.append(f"{here}: variant must be null for a base or an object")
        variant = case.get("variant")
        if variant is not None:
            if not isinstance(variant, dict):
                errors.append(f"{here}: variant must be an object or null")
                continue
            transform = variant.get("transform")
            if not isinstance(transform, str) or transform not in TRANSFORMS:
                errors.append(f"{here}: unknown variant transform")
            else:
                transforms.add(transform)
            if type(variant.get("preserves_semantics")) is not bool:
                errors.append(f"{here}: preserves_semantics must be boolean")
            strings(variant.get("expected_delta"), f"{here}/variant/expected_delta", errors, allow_empty=True)
    for family, members in families.items():
        if sum(case.get("variant") is None for case in members) != 1:
            errors.append(f"{location}: family {family} must have exactly one base case")
    for case in by_id.values():
        variant = case.get("variant")
        if not isinstance(variant, dict):
            continue
        here = f"{location}:{case['id']}"
        parent_id = variant.get("parent_id")
        parent = by_id.get(parent_id) if isinstance(parent_id, str) else None
        if parent is None or parent is case:
            errors.append(f"{here}: variant parent_id must identify another existing case")
            continue
        if parent.get("variant") is not None:
            errors.append(f"{here}: variant must refer directly to its base case")
        if case.get("family") != parent.get("family"):
            errors.append(f"{here}: variant family differs from its base")
        preserves = variant.get("preserves_semantics")
        delta = variant.get("expected_delta")
        if preserves is True:
            if variant.get("transform") == "meaning-change" or delta != []:
                errors.append(f"{here}: preserving variant cannot declare a meaning change or expected_delta")
            for key in ("expectations", "authorized_actions", "category", "mode", "critical_slices"):
                if case.get(key) != parent.get(key):
                    errors.append(f"{here}: preserving variant must retain base {key}")
        elif preserves is False:
            if variant.get("transform") != "meaning-change" or not delta:
                errors.append(f"{here}: meaning change needs transform meaning-change and expected_delta")
            if case.get("expectations") == parent.get("expectations"):
                errors.append(f"{here}: meaning change must declare changed essential expectations")
        if all(case.get(key) == parent.get(key) for key in ("request", "artifacts", "context")):
            errors.append(f"{here}: variant must change an input, not only its metadata")
    for category in sorted(CATEGORIES - covered):
        errors.append(f"{location}: missing regression category {category}")
    for transform in sorted(TRANSFORMS - transforms):
        errors.append(f"{location}: missing variant transform {transform}")
    return len(cases), len(families)


def validate(skill_dir: Path) -> dict:
    skill_dir = skill_dir.resolve()
    errors: list[str] = []
    texts: dict[Path, str] = {}
    if not skill_dir.is_dir():
        errors.append(f"skill directory does not exist: {skill_dir}")
    for path in sorted(skill_dir.rglob("*")):
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            errors.append(f"{path.relative_to(skill_dir).as_posix()}: path cannot be resolved")
            continue
        if not resolved.is_relative_to(skill_dir):
            errors.append(f"{path.relative_to(skill_dir).as_posix()}: symlink escapes the skill directory")
            continue
        if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name != "LICENSE"):
            continue
        try:
            content = path.read_bytes()
            text = content.decode("utf-8")
            if text.startswith("\ufeff"):
                errors.append(f"{path.relative_to(skill_dir).as_posix()}: UTF-8 BOM is not permitted")
            texts[resolved] = text
        except (UnicodeError, OSError) as error:
            errors.append(f"{path.relative_to(skill_dir).as_posix()}: cannot read as UTF-8 ({error})")
    entry = skill_dir / "SKILL.md"
    if entry not in texts:
        errors.append("SKILL.md: required UTF-8 entrypoint is missing or unreadable")
    else:
        check_frontmatter(texts[entry], skill_dir, errors)
    links = sum(check_references(path, text, texts, errors, skill_dir) for path, text in texts.items() if path.suffix == ".md")
    case_path = skill_dir / "tests" / "cases.json"
    cases = families = 0
    if case_path not in texts:
        errors.append("tests/cases.json: required UTF-8 case suite is missing or unreadable")
    else:
        try:
            data = json.loads(texts[case_path], object_pairs_hook=unique_object)
            cases, families = check_cases(data, errors)
        except (ValueError, TypeError) as error:
            errors.append(f"tests/cases.json: invalid JSON ({error})")
    return {
        "status": "PASS" if not errors else "FAIL", "validation": "structural-only",
        "skill_version": VERSION, "files": len(texts), "relative_references": links,
        "cases": cases, "families": families, "errors": errors,
        "model_evaluation": "not-run",
        "notice": "Structural validation does not measure model decisions or prove semantic preservation. Fixture instructions are not executed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1], help="skill directory (default: script's parent skill)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable structural report")
    args = parser.parse_args()
    report = validate(args.skill_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"{report['status']} structural validation: {report['files']} UTF-8 files, {report['relative_references']} relative references, {report['cases']} cases, {report['families']} families.")
        for error in report["errors"]:
            print(f"ERROR: {error}")
        print(report["notice"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
