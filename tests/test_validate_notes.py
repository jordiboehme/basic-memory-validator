"""Tests for validate_notes.py."""

from __future__ import annotations

import json
import os

import pytest

from validate_notes import (
    BOLD,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    Config,
    Issue,
    Severity,
    _build_summary_markdown,
    _format_issue_table,
    parse_note,
    print_summary,
    report_console,
    report_github,
    validate_format,
    validate_quality,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_note(
    title="Test Note",
    type_="note",
    permalink="test/my-note",
    tags=None,
    body="Line one.\nLine two.\nLine three.\n",
    extra_fields=None,
):
    if tags is None:
        tags = ["test"]
    tag_yaml = "\n".join(f"  - {t}" for t in tags)
    extra = ""
    if extra_fields:
        extra = "\n".join(f"{k}: {v}" for k, v in extra_fields.items()) + "\n"
    return (
        f"---\n"
        f"title: {title}\n"
        f"type: {type_}\n"
        f"permalink: {permalink}\n"
        f"tags:\n{tag_yaml}\n"
        f"{extra}"
        f"---\n"
        f"{body}"
    )


def issue_ids(issues: list[Issue]) -> list[str]:
    return [i.rule_id for i in issues]


# ---------------------------------------------------------------------------
# parse_note: frontmatter
# ---------------------------------------------------------------------------

class TestParseNoteFrontmatter:
    def test_valid(self):
        note = parse_note("a.md", make_note())
        assert note.frontmatter is not None
        assert note.frontmatter["title"] == "Test Note"
        assert note.frontmatter["type"] == "note"
        assert note.frontmatter_end_line > 0

    def test_no_frontmatter(self):
        note = parse_note("a.md", "Just plain text.\n")
        assert note.frontmatter is None
        assert note.frontmatter_end_line == 0

    def test_missing_closing_delimiter(self):
        note = parse_note("a.md", "---\ntitle: Oops\n")
        assert note.frontmatter is None

    def test_yaml_not_dict(self):
        note = parse_note("a.md", "---\n42\n---\n")
        assert note.frontmatter is None

    def test_invalid_yaml(self):
        note = parse_note("a.md", "---\n[unclosed\n---\n")
        assert note.frontmatter is None

    def test_empty_frontmatter(self):
        note = parse_note("a.md", "---\n---\n")
        assert note.frontmatter is None

    def test_empty_content(self):
        note = parse_note("a.md", "")
        assert note.frontmatter is None
        assert note.wikilinks == []
        assert note.content_line_count == 0


# ---------------------------------------------------------------------------
# parse_note: wikilinks
# ---------------------------------------------------------------------------

class TestParseNoteWikilinks:
    def test_basic(self):
        note = parse_note("a.md", make_note(body="See [[Other Note]].\n\n\n"))
        assert len(note.wikilinks) == 1
        assert note.wikilinks[0][1] == "Other Note"

    def test_multiple_per_line(self):
        note = parse_note("a.md", make_note(body="See [[A]] and [[B]].\n\n\n"))
        targets = [w[1] for w in note.wikilinks]
        assert targets == ["A", "B"]
        assert note.wikilinks[0][0] == note.wikilinks[1][0]  # same line

    def test_inside_backtick_fence(self):
        body = "```\n[[Ignored]]\n```\nReal content.\nMore content.\nEven more.\n"
        note = parse_note("a.md", make_note(body=body))
        assert note.wikilinks == []

    def test_inside_tilde_fence(self):
        body = "~~~\n[[Ignored]]\n~~~\nReal content.\nMore content.\nEven more.\n"
        note = parse_note("a.md", make_note(body=body))
        assert note.wikilinks == []

    def test_inside_inline_code(self):
        body = "`[[Ignored]]` but [[Kept]].\n\n\n"
        note = parse_note("a.md", make_note(body=body))
        assert len(note.wikilinks) == 1
        assert note.wikilinks[0][1] == "Kept"

    def test_empty_wikilink_not_matched(self):
        note = parse_note("a.md", make_note(body="See [[]].\n\n\n"))
        assert note.wikilinks == []

    def test_wikilinks_in_frontmatter_skipped(self):
        content = "---\ntitle: '[[Link]]'\ntype: note\npermalink: t/a\ntags:\n  - x\n---\nBody.\n\n\n"
        note = parse_note("a.md", content)
        assert note.wikilinks == []


# ---------------------------------------------------------------------------
# parse_note: content line counting
# ---------------------------------------------------------------------------

class TestParseNoteContentLines:
    def test_normal_count(self):
        note = parse_note("a.md", make_note(body="A\nB\nC\n"))
        assert note.content_line_count == 3

    def test_excludes_blanks(self):
        note = parse_note("a.md", make_note(body="A\n\nB\n\nC\n"))
        assert note.content_line_count == 3

    def test_excludes_frontmatter(self):
        note = parse_note("a.md", make_note(body="A\nB\nC\n"))
        # frontmatter has several non-empty lines but they shouldn't be counted
        assert note.content_line_count == 3

    def test_excludes_fence_lines(self):
        body = "```\ncode\n```\nReal.\nMore.\nEnd.\n"
        note = parse_note("a.md", make_note(body=body))
        # fence openers/closers and content inside are excluded
        assert note.content_line_count == 3


# ---------------------------------------------------------------------------
# validate_format: F001
# ---------------------------------------------------------------------------

class TestF001:
    @pytest.mark.parametrize("content", [
        "no frontmatter at all",
        "---\n[bad yaml\n---\n",
        "---\n42\n---\n",
        "---\n---\n",
    ])
    def test_triggers(self, content):
        note = parse_note("a.md", content)
        issues = validate_format(note, Config())
        assert "F001" in issue_ids(issues)
        # F001 short-circuits: no other F-rules
        assert all(i.rule_id == "F001" for i in issues)

    def test_valid_does_not_trigger(self):
        note = parse_note("a.md", make_note())
        issues = validate_format(note, Config())
        assert "F001" not in issue_ids(issues)


# ---------------------------------------------------------------------------
# validate_format: F002
# ---------------------------------------------------------------------------

class TestF002:
    @pytest.mark.parametrize("field", ["title", "type", "permalink", "tags"])
    def test_missing_field(self, field):
        # Build frontmatter with the target field omitted entirely
        fields = {
            "title": "title: X",
            "type": "type: note",
            "permalink": "permalink: test/x",
            "tags": "tags:\n  - a",
        }
        del fields[field]
        fm = "\n".join(fields.values())
        content = f"---\n{fm}\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        issues = [i for i in validate_format(note, Config()) if i.rule_id == "F002"]
        assert any(field in i.message for i in issues)

    def test_multiple_missing(self):
        content = "---\ntitle: X\ntype: null\npermalink: null\ntags:\n  - a\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        f002s = [i for i in validate_format(note, Config()) if i.rule_id == "F002"]
        assert len(f002s) == 2  # type and permalink


# ---------------------------------------------------------------------------
# validate_format: F003
# ---------------------------------------------------------------------------

class TestF003:
    def test_default_accepts_any_non_empty(self):
        """Default config (no valid_types) accepts any non-empty string."""
        for type_val in ("note", "manifest", "blog", "NOTE", "custom"):
            note = parse_note("a.md", make_note(type_=type_val))
            assert "F003" not in issue_ids(validate_format(note, Config()))

    def test_default_rejects_empty_string(self):
        content = "---\ntitle: X\ntype: ''\npermalink: test/x\ntags:\n  - a\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        assert "F003" in issue_ids(validate_format(note, Config()))

    def test_configured_valid_types(self):
        """When valid_types is set, only those types are accepted."""
        cfg = Config(valid_types=("note", "manifest"))
        note_ok = parse_note("a.md", make_note(type_="note"))
        assert "F003" not in issue_ids(validate_format(note_ok, cfg))
        note_bad = parse_note("b.md", make_note(type_="blog"))
        assert "F003" in issue_ids(validate_format(note_bad, cfg))

    def test_none_type_skipped(self):
        content = "---\ntitle: X\ntype: null\npermalink: test/x\ntags:\n  - a\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        issues = validate_format(note, Config())
        assert "F003" not in issue_ids(issues)


# ---------------------------------------------------------------------------
# validate_format: F004
# ---------------------------------------------------------------------------

class TestF004:
    def test_valid_tags(self):
        note = parse_note("a.md", make_note(tags=["a", "b"]))
        assert "F004" not in issue_ids(validate_format(note, Config()))

    def test_tags_not_a_list(self):
        content = "---\ntitle: X\ntype: note\npermalink: test/x\ntags: not-a-list\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        issues = [i for i in validate_format(note, Config()) if i.rule_id == "F004"]
        assert len(issues) == 1
        assert "must be a list" in issues[0].message

    def test_empty_tags(self):
        content = "---\ntitle: X\ntype: note\npermalink: test/x\ntags: []\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        issues = [i for i in validate_format(note, Config()) if i.rule_id == "F004"]
        assert len(issues) == 1
        assert "empty" in issues[0].message

    def test_null_tags_skipped(self):
        content = "---\ntitle: X\ntype: note\npermalink: test/x\ntags: null\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        assert "F004" not in issue_ids(validate_format(note, Config()))


# ---------------------------------------------------------------------------
# validate_format: F005
# ---------------------------------------------------------------------------

class TestF005:
    @pytest.mark.parametrize("permalink,expect", [
        ("test/my-note", False),
        ("deep/nested/path", False),
        ("a1/b2-c3", False),
        ("UPPER/case", True),
        ("/leading-slash", True),
        ("no-slash", True),
        ("test/my note", True),
        ("-bad/start", True),
        ("test/-bad", True),
    ])
    def test_permalink_format(self, permalink, expect):
        note = parse_note("a.md", make_note(permalink=permalink))
        has_f005 = "F005" in issue_ids(validate_format(note, Config()))
        assert has_f005 == expect

    def test_null_permalink_skipped(self):
        content = "---\ntitle: X\ntype: note\npermalink: null\ntags:\n  - a\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        assert "F005" not in issue_ids(validate_format(note, Config()))


# ---------------------------------------------------------------------------
# validate_format: config overrides
# ---------------------------------------------------------------------------

class TestConfigOverrides:
    def test_custom_valid_types(self):
        cfg = Config(valid_types=("note", "manifest", "registry"))
        note = parse_note("a.md", make_note(type_="registry"))
        assert "F003" not in issue_ids(validate_format(note, cfg))
        note_bad = parse_note("b.md", make_note(type_="blog"))
        assert "F003" in issue_ids(validate_format(note_bad, cfg))

    def test_custom_permalink_pattern(self):
        cfg = Config(permalink_pattern=r".*")  # accept anything
        note = parse_note("a.md", make_note(permalink="ANYTHING GOES"))
        assert "F005" not in issue_ids(validate_format(note, cfg))

    def test_custom_required_fields(self):
        cfg = Config(required_fields=("title",))
        content = "---\ntitle: X\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        assert "F002" not in issue_ids(validate_format(note, cfg))


# ---------------------------------------------------------------------------
# validate_quality: Q001
# ---------------------------------------------------------------------------

class TestQ001:
    def test_broken_wikilink(self):
        note = parse_note("a.md", make_note(body="See [[Missing Note]].\n\n\n"))
        issues = validate_quality([note], Config())
        q001s = [i for i in issues if i.rule_id == "Q001"]
        assert len(q001s) == 1
        assert "Missing Note" in q001s[0].message

    def test_valid_wikilink(self):
        a = parse_note("a.md", make_note(title="Note A", body="See [[Note B]].\n\n\n"))
        b = parse_note("b.md", make_note(title="Note B", permalink="test/note-b"))
        issues = validate_quality([a, b], Config())
        assert "Q001" not in issue_ids(issues)

    def test_no_frontmatter_contributes_no_title(self):
        a = parse_note("a.md", make_note(body="See [[Orphan]].\n\n\n"))
        b = parse_note("b.md", "Just text, no frontmatter.\n")
        issues = validate_quality([a, b], Config())
        q001s = [i for i in issues if i.rule_id == "Q001"]
        assert len(q001s) == 1


# ---------------------------------------------------------------------------
# validate_quality: Q002
# ---------------------------------------------------------------------------

class TestQ002:
    def test_duplicate_permalinks(self):
        a = parse_note("a.md", make_note(permalink="test/dup"))
        b = parse_note("b.md", make_note(title="Other", permalink="test/dup"))
        issues = validate_quality([a, b], Config())
        q002s = [i for i in issues if i.rule_id == "Q002"]
        assert len(q002s) == 2
        assert any("a.md" in i.message for i in q002s)
        assert any("b.md" in i.message for i in q002s)

    def test_unique_permalinks(self):
        a = parse_note("a.md", make_note(permalink="test/one"))
        b = parse_note("b.md", make_note(title="Other", permalink="test/two"))
        issues = validate_quality([a, b], Config())
        assert "Q002" not in issue_ids(issues)

    def test_three_way_duplicate(self):
        notes = [
            parse_note("a.md", make_note(title="A", permalink="test/dup")),
            parse_note("b.md", make_note(title="B", permalink="test/dup")),
            parse_note("c.md", make_note(title="C", permalink="test/dup")),
        ]
        issues = validate_quality(notes, Config())
        q002s = [i for i in issues if i.rule_id == "Q002"]
        assert len(q002s) == 3


# ---------------------------------------------------------------------------
# validate_quality: Q003
# ---------------------------------------------------------------------------

class TestQ003:
    @pytest.mark.parametrize("tag,expect", [
        ("valid-tag", False),
        ("simple", False),
        ("multi-word-tag", False),
        ("123", False),
        ("CamelCase", True),
        ("has_underscore", True),
        ("has space", True),
        ("-leading-hyphen", True),
        ("trailing-", True),
    ])
    def test_tag_format(self, tag, expect):
        note = parse_note("a.md", make_note(tags=[tag]))
        issues = validate_quality([note], Config())
        has_q003 = "Q003" in issue_ids(issues)
        assert has_q003 == expect

    def test_non_list_tags_skipped(self):
        content = "---\ntitle: X\ntype: note\npermalink: test/x\ntags: bad\n---\nA\nB\nC\n"
        note = parse_note("a.md", content)
        issues = validate_quality([note], Config())
        assert "Q003" not in issue_ids(issues)


# ---------------------------------------------------------------------------
# validate_quality: Q004
# ---------------------------------------------------------------------------

class TestQ004:
    def test_short_content(self):
        note = parse_note("a.md", make_note(body="Only one line.\n"))
        issues = validate_quality([note], Config())
        assert "Q004" in issue_ids(issues)

    def test_sufficient_content(self):
        note = parse_note("a.md", make_note(body="A\nB\nC\n"))
        issues = validate_quality([note], Config())
        assert "Q004" not in issue_ids(issues)

    def test_no_frontmatter_still_checked(self):
        note = parse_note("a.md", "One line.\n")
        issues = validate_quality([note], Config())
        assert "Q004" in issue_ids(issues)

    def test_custom_min_content_lines(self):
        cfg = Config(min_content_lines=1)
        note = parse_note("a.md", make_note(body="Just one.\n"))
        issues = validate_quality([note], cfg)
        assert "Q004" not in issue_ids(issues)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults(self):
        cfg = Config()
        assert cfg.valid_types == ()
        assert cfg.required_fields == ("title", "type", "permalink", "tags")
        assert cfg.min_content_lines == 3

    def test_from_json(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"min_content_lines": 5, "valid_types": ["note"]}))
        cfg = Config.from_json(str(p))
        assert cfg.min_content_lines == 5
        assert cfg.valid_types == ("note",)

    def test_unknown_fields_ignored(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"unknown_field": 123}))
        cfg = Config.from_json(str(p))
        assert cfg.min_content_lines == 3  # unchanged default

    def test_tuple_conversion(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"required_fields": ["title", "permalink"]}))
        cfg = Config.from_json(str(p))
        assert isinstance(cfg.required_fields, tuple)
        assert cfg.required_fields == ("title", "permalink")


# ---------------------------------------------------------------------------
# report_console
# ---------------------------------------------------------------------------

class TestReportConsole:
    def test_error_formatting(self, capsys):
        issues = [Issue("a.md", 5, Severity.ERROR, "F003", "Bad type.", "Fix it.")]
        report_console(issues)
        out = capsys.readouterr().out
        assert RED in out
        assert "ERROR" in out
        assert "F003" in out
        assert "Fix it." in out

    def test_warning_formatting(self, capsys):
        issues = [Issue("a.md", 1, Severity.WARNING, "Q001", "Broken.", "Fix.")]
        report_console(issues)
        out = capsys.readouterr().out
        assert YELLOW in out
        assert "WARNING" in out

    def test_groups_by_file(self, capsys):
        issues = [
            Issue("b.md", 1, Severity.ERROR, "F001", "Msg.", "Fix."),
            Issue("a.md", 1, Severity.WARNING, "Q001", "Msg.", "Fix."),
        ]
        report_console(issues)
        out = capsys.readouterr().out
        # a.md should appear before b.md (sorted)
        assert out.index("a.md") < out.index("b.md")
        assert BOLD in out

    def test_none_line_shows_em_dash(self, capsys):
        issues = [Issue("a.md", None, Severity.WARNING, "Q004", "Short.", "Fix.")]
        report_console(issues)
        out = capsys.readouterr().out
        assert "\u2014" in out


# ---------------------------------------------------------------------------
# report_github
# ---------------------------------------------------------------------------

class TestReportGithub:
    def test_error_annotation(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        issues = [Issue("a.md", 5, Severity.ERROR, "F003", "Bad type.", "Fix.")]
        report_github(issues, 1)
        out = capsys.readouterr().out
        assert "::error file=a.md,line=5,title=F003::" in out

    def test_warning_annotation(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        issues = [Issue("a.md", 1, Severity.WARNING, "Q001", "Broken.", "Fix.")]
        report_github(issues, 1)
        out = capsys.readouterr().out
        assert "::warning " in out

    def test_grouping(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        issues = [Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix.")]
        report_github(issues, 1)
        out = capsys.readouterr().out
        assert "::group::a.md" in out
        assert "::endgroup::" in out

    def test_no_line_omits_line_param(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        issues = [Issue("a.md", None, Severity.WARNING, "Q004", "Short.", "Fix.")]
        report_github(issues, 1)
        out = capsys.readouterr().out
        assert "line=" not in out

    def test_step_summary_with_issues(self, capsys, monkeypatch, tmp_path):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        issues = [
            Issue("a.md", 1, Severity.ERROR, "F003", "Bad type.", "Fix type."),
            Issue("a.md", None, Severity.WARNING, "Q004", "Short.", "Add content."),
        ]
        report_github(issues, 3)
        content = summary_file.read_text()
        assert "## Knowledge Base Validation" in content
        assert "<details open>" in content
        assert "<details>" in content
        assert "**1 error**" in content
        assert "**1 warning**" in content
        assert "**3 files**" in content

    def test_step_summary_no_issues(self, capsys, monkeypatch, tmp_path):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        report_github([], 5)
        content = summary_file.read_text()
        assert "All **5 notes** passed validation" in content

    def test_step_summary_not_set(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        report_github([], 1)  # should not raise

    def test_step_summary_pluralization(self, capsys, monkeypatch, tmp_path):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        issues = [Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix.")]
        report_github(issues, 1)
        content = summary_file.read_text()
        assert "**1 error**" in content
        assert "**1 file**" in content
        # Ensure no spurious plural
        assert "errors**" not in content
        assert "files**" not in content

    def test_step_summary_single_note_success(self, capsys, monkeypatch, tmp_path):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        report_github([], 1)
        content = summary_file.read_text()
        assert "**1 note**" in content
        assert "notes**" not in content


# ---------------------------------------------------------------------------
# _build_summary_markdown
# ---------------------------------------------------------------------------

class TestBuildSummaryMarkdown:
    def test_no_issues(self):
        md = _build_summary_markdown([], 5)
        assert "## Knowledge Base Validation" in md
        assert "All **5 notes** passed validation" in md

    def test_single_note_no_issues(self):
        md = _build_summary_markdown([], 1)
        assert "**1 note**" in md
        assert "notes**" not in md

    def test_errors_only(self):
        issues = [Issue("a.md", 1, Severity.ERROR, "F003", "Bad type.", "Fix.")]
        md = _build_summary_markdown(issues, 1)
        assert "**1 error**" in md
        assert "<details open>" in md
        assert "warning" not in md.lower().split("</details>")[-1]

    def test_warnings_only(self):
        issues = [Issue("a.md", 1, Severity.WARNING, "Q001", "Broken.", "Fix.")]
        md = _build_summary_markdown(issues, 1)
        assert "**1 warning**" in md
        assert "<details>" in md

    def test_mixed(self):
        issues = [
            Issue("a.md", 1, Severity.ERROR, "F003", "Bad type.", "Fix type."),
            Issue("a.md", None, Severity.WARNING, "Q004", "Short.", "Add content."),
        ]
        md = _build_summary_markdown(issues, 3)
        assert "**1 error**" in md
        assert "**1 warning**" in md
        assert "**3 files**" in md

    def test_pluralization(self):
        issues = [
            Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix."),
            Issue("b.md", 1, Severity.ERROR, "F002", "Msg.", "Fix."),
        ]
        md = _build_summary_markdown(issues, 2)
        assert "**2 errors**" in md
        assert "**2 files**" in md

    def test_issue_table_content(self):
        issues = [Issue("a.md", 5, Severity.ERROR, "F003", "Bad type.", "Fix it.")]
        md = _build_summary_markdown(issues, 1)
        assert "`a.md`" in md
        assert "F003" in md
        assert "Fix it." in md


class TestSummaryFile:
    def test_writes_summary_file(self, capsys, monkeypatch, tmp_path):
        summary_file = tmp_path / "validation-summary.md"
        monkeypatch.setenv("VALIDATION_SUMMARY_FILE", str(summary_file))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        report_github([], 3)
        assert summary_file.exists()
        content = summary_file.read_text()
        assert "All **3 notes** passed validation" in content

    def test_no_file_when_env_unset(self, capsys, monkeypatch, tmp_path):
        monkeypatch.delenv("VALIDATION_SUMMARY_FILE", raising=False)
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        report_github([], 1)  # should not raise

    def test_summary_file_matches_step_summary(self, capsys, monkeypatch, tmp_path):
        step_summary = tmp_path / "step-summary.md"
        summary_file = tmp_path / "validation-summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
        monkeypatch.setenv("VALIDATION_SUMMARY_FILE", str(summary_file))
        issues = [
            Issue("a.md", 1, Severity.ERROR, "F003", "Bad type.", "Fix."),
            Issue("b.md", None, Severity.WARNING, "Q004", "Short.", "Add."),
        ]
        report_github(issues, 5)
        assert step_summary.read_text() == summary_file.read_text()


# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_console_errors(self, capsys):
        issues = [Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix.")]
        print_summary(issues, 1, "console")
        out = capsys.readouterr().out
        assert RED in out
        assert "1 error" in out
        assert "1 file:" in out

    def test_console_warnings_only(self, capsys):
        issues = [Issue("a.md", 1, Severity.WARNING, "Q001", "Msg.", "Fix.")]
        print_summary(issues, 1, "console")
        out = capsys.readouterr().out
        assert YELLOW in out
        assert "1 warning" in out

    def test_console_mixed(self, capsys):
        issues = [
            Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix."),
            Issue("a.md", 1, Severity.ERROR, "F002", "Msg.", "Fix."),
            Issue("b.md", 1, Severity.WARNING, "Q001", "Msg.", "Fix."),
        ]
        print_summary(issues, 2, "console")
        out = capsys.readouterr().out
        assert RED in out
        assert "2 errors" in out
        assert "1 warning" in out

    def test_console_all_valid(self, capsys):
        print_summary([], 3, "console")
        out = capsys.readouterr().out
        assert GREEN in out
        assert "all valid" in out

    def test_console_plural(self, capsys):
        issues = [
            Issue("a.md", 1, Severity.WARNING, "Q001", "Msg.", "Fix."),
            Issue("b.md", 1, Severity.WARNING, "Q003", "Msg.", "Fix."),
        ]
        print_summary(issues, 2, "console")
        out = capsys.readouterr().out
        assert "2 warnings" in out
        assert "2 files:" in out

    def test_github_error(self, capsys):
        issues = [Issue("a.md", 1, Severity.ERROR, "F001", "Msg.", "Fix.")]
        print_summary(issues, 1, "github")
        out = capsys.readouterr().out
        assert "::error::" in out

    def test_github_warning_only(self, capsys):
        issues = [Issue("a.md", 1, Severity.WARNING, "Q001", "Msg.", "Fix.")]
        print_summary(issues, 1, "github")
        out = capsys.readouterr().out
        assert "::warning::" in out

    def test_github_valid(self, capsys):
        print_summary([], 1, "github")
        out = capsys.readouterr().out
        assert "::notice::" in out
        assert "all valid" in out

    def test_singular_file(self, capsys):
        print_summary([], 1, "console")
        out = capsys.readouterr().out
        assert "1 file:" in out
        assert "files" not in out


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_f001_short_circuits(self):
        note = parse_note("a.md", "no frontmatter")
        issues = validate_format(note, Config())
        assert issue_ids(issues) == ["F001"]

    def test_valid_note_no_issues(self):
        note = parse_note("a.md", make_note())
        format_issues = validate_format(note, Config())
        quality_issues = validate_quality([note], Config())
        all_issues = format_issues + quality_issues
        assert all_issues == []

    def test_mixed_pipeline(self):
        cfg = Config(valid_types=("note", "manifest"))
        bad = parse_note("bad.md", make_note(type_="invalid", body="See [[Ghost]].\n\n\n"))
        good = parse_note("good.md", make_note(title="Good", permalink="test/good"))
        issues = []
        for n in [bad, good]:
            issues.extend(validate_format(n, cfg))
        issues.extend(validate_quality([bad, good], cfg))
        ids = issue_ids(issues)
        assert "F003" in ids
        assert "Q001" in ids
