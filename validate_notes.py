#!/usr/bin/env python3
"""Validate Basic Memory knowledge base notes.

Checks that markdown files adhere to the Basic Memory format (correct YAML
frontmatter, valid fields) and flags quality issues (broken wikilinks,
duplicate permalinks).

Exit code 0 if no errors (warnings are OK), 1 if any errors.

Usage:
    python validate_notes.py memory/
    python validate_notes.py memory/ notes/ --format github
    python validate_notes.py memory/ --config validation.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass
from enum import Enum

import yaml


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    valid_types: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ("title", "type", "permalink", "tags")
    permalink_pattern: str = r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9/-]*$"
    tag_pattern: str = r"^[a-z0-9]+(-[a-z0-9]+)*$"
    min_content_lines: int = 3

    @classmethod
    def from_json(cls, path: str) -> Config:
        with open(path) as f:
            overrides = json.load(f)
        cfg = cls()
        for key, value in overrides.items():
            if hasattr(cfg, key):
                if isinstance(getattr(cfg, key), tuple):
                    value = tuple(value)
                setattr(cfg, key, value)
        return cfg


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Issue:
    file_path: str
    line: int | None
    severity: Severity
    rule_id: str
    message: str
    fix: str


@dataclass
class NoteFile:
    path: str
    raw_content: str
    frontmatter: dict | None
    frontmatter_end_line: int
    wikilinks: list[tuple[int, str]]
    content_line_count: int
    lines: list[str]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

FRONTMATTER_DELIM = "---"
WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`]+`")


def parse_note(path: str, content: str) -> NoteFile:
    lines = content.splitlines()
    frontmatter = None
    frontmatter_end_line = 0

    # --- Parse frontmatter ---
    if lines and lines[0].strip() == FRONTMATTER_DELIM:
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == FRONTMATTER_DELIM:
                end_idx = i
                break
        if end_idx is not None:
            frontmatter_end_line = end_idx + 1  # 1-based
            raw_yaml = "\n".join(lines[1:end_idx])
            try:
                frontmatter = yaml.safe_load(raw_yaml)
                if not isinstance(frontmatter, dict):
                    frontmatter = None
            except yaml.YAMLError:
                frontmatter = None

    # --- Collect wikilinks (outside code fences) ---
    wikilinks: list[tuple[int, str]] = []
    in_fence = False
    content_line_count = 0

    for i, line in enumerate(lines):
        # Track code fences
        fence_match = FENCE_RE.match(line.strip())
        if fence_match:
            in_fence = not in_fence
            continue

        if in_fence:
            continue

        # Skip frontmatter lines
        if i < frontmatter_end_line:
            continue

        # Count meaningful content lines
        if line.strip():
            content_line_count += 1

        # Collect wikilinks (strip inline code first to avoid false positives)
        line_without_code = INLINE_CODE_RE.sub("", line)
        for m in WIKILINK_RE.finditer(line_without_code):
            wikilinks.append((i + 1, m.group(1)))  # 1-based line

    return NoteFile(
        path=path,
        raw_content=content,
        frontmatter=frontmatter,
        frontmatter_end_line=frontmatter_end_line,
        wikilinks=wikilinks,
        content_line_count=content_line_count,
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Tier 1: Format validation (errors)
# ---------------------------------------------------------------------------

def validate_format(note: NoteFile, config: Config) -> list[Issue]:
    issues: list[Issue] = []

    # F001: Valid YAML frontmatter
    if note.frontmatter is None:
        issues.append(Issue(
            file_path=note.path,
            line=1,
            severity=Severity.ERROR,
            rule_id="F001",
            message="Invalid or missing YAML frontmatter — cannot parse header between '---' delimiters.",
            fix="Use write_note() to create the note, or edit_note() to regenerate the frontmatter.",
        ))
        return issues  # Can't check further without frontmatter

    fm = note.frontmatter

    # F002: Required fields
    for field_name in config.required_fields:
        if field_name not in fm or fm[field_name] is None:
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F002",
                message=f"Missing required field '{field_name}'.",
                fix=f"Use edit_note() to update the note — Basic Memory will generate the correct {field_name}.",
            ))

    # F003: Valid type
    note_type = fm.get("type")
    if note_type is not None:
        if config.valid_types and note_type not in config.valid_types:
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F003",
                message=f"Invalid type '{note_type}' — must be one of: {', '.join(config.valid_types)}.",
                fix="Use edit_note() to set the correct type.",
            ))
        elif not config.valid_types and (not isinstance(note_type, str) or not note_type.strip()):
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F003",
                message="Type must be a non-empty string.",
                fix="Use edit_note() to set the correct type.",
            ))

    # F004: Tags is a non-empty list
    tags = fm.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F004",
                message=f"'tags' must be a list, got {type(tags).__name__}.",
                fix="Use edit_note() to fix the tags — Basic Memory formats them as a YAML list.",
            ))
        elif len(tags) == 0:
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F004",
                message="'tags' list is empty — at least one tag is required.",
                fix="Use edit_note() to add relevant tags to the note.",
            ))

    # F005: Permalink format
    permalink = fm.get("permalink")
    if permalink is not None and isinstance(permalink, str):
        if not re.match(config.permalink_pattern, permalink):
            issues.append(Issue(
                file_path=note.path,
                line=1,
                severity=Severity.ERROR,
                rule_id="F005",
                message=f"Permalink '{permalink}' does not match expected format (lowercase, slash-separated path segments).",
                fix="Use edit_note() to update the note — Basic Memory will generate the correct permalink.",
            ))

    return issues


# ---------------------------------------------------------------------------
# Tier 2: Quality validation (warnings, cross-file)
# ---------------------------------------------------------------------------

def validate_quality(notes: list[NoteFile], config: Config) -> list[Issue]:
    issues: list[Issue] = []

    # Build lookup sets
    all_titles: set[str] = set()
    permalink_to_files: dict[str, list[str]] = {}

    for note in notes:
        if note.frontmatter:
            title = note.frontmatter.get("title")
            if title:
                all_titles.add(title)

            permalink = note.frontmatter.get("permalink")
            if permalink:
                permalink_to_files.setdefault(permalink, []).append(note.path)

    for note in notes:
        # Q001: Broken wikilinks
        for line_num, target in note.wikilinks:
            if target not in all_titles:
                issues.append(Issue(
                    file_path=note.path,
                    line=line_num,
                    severity=Severity.WARNING,
                    rule_id="Q001",
                    message=f"Broken wikilink [[{target}]] — no note with this title exists.",
                    fix="Check the title spelling, or create the missing note with write_note().",
                ))

        if note.frontmatter:
            # Q003: Tag format
            tags = note.frontmatter.get("tags")
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str) and not re.match(config.tag_pattern, tag):
                        issues.append(Issue(
                            file_path=note.path,
                            line=1,
                            severity=Severity.WARNING,
                            rule_id="Q003",
                            message=f"Tag '{tag}' does not follow lowercase-with-hyphens format.",
                            fix="Use edit_note() to update the tag to lowercase with hyphens (e.g., 'my-tag').",
                        ))

        # Q004: Empty content
        if note.content_line_count < config.min_content_lines:
            issues.append(Issue(
                file_path=note.path,
                line=None,
                severity=Severity.WARNING,
                rule_id="Q004",
                message=f"File has very little content ({note.content_line_count} non-empty lines outside frontmatter).",
                fix="Use edit_note() to add meaningful content to this note.",
            ))

    # Q002: Duplicate permalinks
    for permalink, files in permalink_to_files.items():
        if len(files) > 1:
            for file_path in files:
                others = [f for f in files if f != file_path]
                issues.append(Issue(
                    file_path=file_path,
                    line=1,
                    severity=Severity.WARNING,
                    rule_id="Q002",
                    message=f"Duplicate permalink '{permalink}' — also used by {', '.join(others)}.",
                    fix="Use edit_note() on one of the files to assign a unique permalink.",
                ))

    return issues


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def report_console(issues: list[Issue]) -> None:
    by_file: dict[str, list[Issue]] = {}
    for issue in issues:
        by_file.setdefault(issue.file_path, []).append(issue)

    for file_path in sorted(by_file):
        print(f"\n{BOLD}{file_path}{RESET}")
        file_issues = sorted(by_file[file_path], key=lambda i: i.line or 0)
        for issue in file_issues:
            color = RED if issue.severity == Severity.ERROR else YELLOW
            severity = issue.severity.value.upper()
            line = issue.line or "\u2014"
            print(f"  {color}{severity}{RESET} [{issue.rule_id}] line {line}: {issue.message}")
            print(f"    {DIM}Fix: {issue.fix}{RESET}")


def _format_issue_table(issues: list[Issue]) -> str:
    lines = ["| File | Line | Rule | Issue | Fix |", "|------|------|------|-------|-----|"]
    for i in issues:
        line = str(i.line) if i.line else "\u2014"
        lines.append(f"| `{i.file_path}` | {line} | {i.rule_id} | {i.message} | {i.fix} |")
    return "\n".join(lines) + "\n"


def _build_summary_markdown(issues: list[Issue], files_checked: int) -> str:
    parts_out: list[str] = ["## Knowledge Base Validation\n"]
    if not issues:
        noun = "note" if files_checked == 1 else "notes"
        parts_out.append(f"> All **{files_checked} {noun}** passed validation\n")
    else:
        errors = [i for i in issues if i.severity == Severity.ERROR]
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        desc = []
        if errors:
            desc.append(f"**{len(errors)} error{'s' if len(errors) != 1 else ''}**")
        if warnings:
            desc.append(f"**{len(warnings)} warning{'s' if len(warnings) != 1 else ''}**")
        parts_out.append(f"> {' and '.join(desc)} found across **{files_checked} file{'s' if files_checked != 1 else ''}**\n")

        if errors:
            parts_out.append("<details open>")
            parts_out.append(f"<summary><strong>Errors ({len(errors)})</strong></summary>\n")
            parts_out.append(_format_issue_table(errors))
            parts_out.append("</details>\n")

        if warnings:
            parts_out.append("<details>")
            parts_out.append(f"<summary><strong>Warnings ({len(warnings)})</strong></summary>\n")
            parts_out.append(_format_issue_table(warnings))
            parts_out.append("</details>\n")

    return "\n".join(parts_out)


def report_github(issues: list[Issue], files_checked: int) -> None:
    by_file: dict[str, list[Issue]] = {}
    for issue in issues:
        by_file.setdefault(issue.file_path, []).append(issue)

    # Emit annotations with title parameter, grouped by file
    for file_path in sorted(by_file):
        print(f"::group::{file_path}")
        file_issues = sorted(by_file[file_path], key=lambda i: i.line or 0)
        for issue in file_issues:
            severity = "error" if issue.severity == Severity.ERROR else "warning"
            loc_parts = [f"file={issue.file_path}"]
            if issue.line:
                loc_parts.append(f"line={issue.line}")
            loc = ",".join(loc_parts)
            msg = issue.message.rstrip(".")
            print(f"::{severity} {loc},title={issue.rule_id}::{msg}. Fix: {issue.fix}")
        print("::endgroup::")

    # Build summary markdown
    summary_md = _build_summary_markdown(issues, files_checked)

    # Write step summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(summary_md)

    # Write summary file for PR comment step
    summary_file = os.environ.get("VALIDATION_SUMMARY_FILE")
    if summary_file:
        with open(summary_file, "w") as f:
            f.write(summary_md)


def print_summary(issues: list[Issue], files_checked: int, output_format: str) -> None:
    errors = sum(1 for i in issues if i.severity == Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity == Severity.WARNING)
    parts = []
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    status = ", ".join(parts) if parts else "all valid"

    if output_format == "github":
        severity = "error" if errors else ("warning" if warnings else "notice")
        print(f"::{severity}::Checked {files_checked} file{'s' if files_checked != 1 else ''}: {status}")
    else:
        color = RED if errors else (YELLOW if warnings else GREEN)
        print(f"\n{color}Checked {files_checked} file{'s' if files_checked != 1 else ''}: {status}{RESET}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Basic Memory knowledge base notes"
    )
    parser.add_argument(
        "memory_dirs",
        nargs="+",
        help="One or more directories containing .md notes to validate",
    )
    parser.add_argument(
        "--format",
        choices=["console", "github"],
        default="console",
        dest="output_format",
        help="Output format (github = workflow annotations)",
    )
    parser.add_argument(
        "--config",
        help="Path to JSON config override file",
        default=None,
    )
    args = parser.parse_args()

    config = Config.from_json(args.config) if args.config else Config()

    # Collect .md files from all specified directories
    md_files: list[str] = []
    for memory_dir in args.memory_dirs:
        found = sorted(glob.glob(os.path.join(memory_dir, "**/*.md"), recursive=True))
        md_files.extend(found)

    if not md_files:
        dirs = ", ".join(args.memory_dirs)
        print(f"No .md files found in: {dirs}")
        sys.exit(0)

    notes = []
    for path in md_files:
        with open(path) as f:
            content = f.read()
        notes.append(parse_note(path, content))

    # Run validators
    issues: list[Issue] = []
    for note in notes:
        issues.extend(validate_format(note, config))
    issues.extend(validate_quality(notes, config))

    # Report
    if args.output_format == "github":
        report_github(issues, len(notes))
    else:
        report_console(issues)

    print_summary(issues, len(notes), args.output_format)

    has_errors = any(i.severity == Severity.ERROR for i in issues)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
