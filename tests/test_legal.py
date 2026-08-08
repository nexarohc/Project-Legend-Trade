"""The placeholder renderer for PRIVACY.md and TERMS.md.

The thing worth testing here is the *refusal*. A renderer that produces output
is easy; one that reliably declines to produce publishable-looking output with a
hole in it is the point, because the failure mode is a privacy policy served to
the public with `{{OPERATOR_LEGAL_NAME}}` in the header and nobody noticing.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Loaded by path: `scripts/` is a directory of entry points, not an importable
# package, and adding an `__init__.py` to make one test tidier would change how
# the scripts are meant to be run.
_spec = importlib.util.spec_from_file_location("legal_script", ROOT / "scripts" / "legal.py")
legal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legal)


COMPLETE = {
    "OPERATOR_LEGAL_NAME": "Example Holdings Ltd",
    "SERVICE_URL": "https://legendtrade.example",
    "PRIVACY_CONTACT_EMAIL": "privacy@legendtrade.example",
    "SUPPORT_CONTACT_EMAIL": "support@legendtrade.example",
    "GOVERNING_LAW_JURISDICTION": "the laws of England and Wales",
    "LIABILITY_CAP": "GBP 100",
    "MINIMUM_AGE": "18",
    "SOFTWARE_LICENCE": "the MIT Licence",
    "DATE": "2026-01-01",
}


def test_the_real_documents_are_the_source_of_the_key_list():
    """The keys come from the documents, not from a list that can drift."""
    keys = legal.required_keys()
    assert set(keys) == set(COMPLETE)
    # The prose about placeholders must not be mistaken for one.
    assert "BRACES" not in keys
    assert "PLACEHOLDER" not in keys


def test_complete_answers_leave_no_placeholder_behind():
    for source in legal.SOURCES:
        rendered = legal.render(source.read_text(encoding="utf-8"), COMPLETE)
        assert legal.placeholders_in(rendered) == set()
        assert "{{" not in rendered, f"{source.name} still has raw braces"
        assert COMPLETE["OPERATOR_LEGAL_NAME"] in rendered


def test_rendering_keeps_the_not_reviewed_by_a_lawyer_banner():
    """Filling in a name is not legal review, and the output must not imply it."""
    for source in legal.SOURCES:
        rendered = legal.render(source.read_text(encoding="utf-8"), COMPLETE)
        assert "Not yet reviewed by a lawyer" in rendered


def test_rendering_drops_the_line_about_placeholders_remaining():
    """Once rendered, 'placeholders must be filled in' is no longer true."""
    for source in legal.SOURCES:
        original = source.read_text(encoding="utf-8")
        rendered = legal.render(original, COMPLETE)
        if "Placeholders in" in original:
            assert "Placeholders in" not in rendered


def test_every_missing_answer_is_reported_in_one_pass():
    """Nine missing values must produce nine complaints, not the first one."""
    issues = legal.problems_with({}, legal.required_keys())
    # DATE is exempt: it defaults to today rather than blocking a render.
    assert len(issues) == len(COMPLETE) - 1
    assert any("OPERATOR_LEGAL_NAME" in issue for issue in issues)


def test_the_lawyer_questions_say_so_when_unanswered():
    issues = legal.problems_with({}, legal.required_keys())
    lawyerly = [issue for issue in issues if "needs a lawyer" in issue]
    assert {issue.split()[0] for issue in lawyerly} == legal.NEEDS_A_LAWYER


@pytest.mark.parametrize("key,bad", [
    ("SERVICE_URL", "legendtrade.example"),           # no scheme
    ("PRIVACY_CONTACT_EMAIL", "privacy-at-example"),  # no @
    ("SUPPORT_CONTACT_EMAIL", "nobody"),
    ("MINIMUM_AGE", "eighteen"),                      # not a number
    ("DATE", "06/08/2026"),                           # not ISO
])
def test_malformed_answers_are_refused(key, bad):
    answers = dict(COMPLETE, **{key: bad})
    issues = legal.problems_with(answers, legal.required_keys())
    assert any(issue.startswith(key) for issue in issues), issues


@pytest.mark.parametrize("value", ["{{OPERATOR_LEGAL_NAME}}", "TBD", "todo", "N/A"])
def test_an_answer_that_is_still_a_placeholder_is_refused(value):
    """Worse than blank: it renders, and the result looks finished."""
    answers = dict(COMPLETE, OPERATOR_LEGAL_NAME=value)
    issues = legal.problems_with(answers, legal.required_keys())
    assert any("still a placeholder" in issue for issue in issues)


def test_a_blank_date_is_allowed_because_it_defaults_to_today():
    assert legal.problems_with(dict(COMPLETE, DATE=""), legal.required_keys()) == []


def test_init_writes_an_answers_file_covering_every_key(tmp_path):
    target = tmp_path / "answers.json"
    legal.write_template(target, legal.required_keys())

    body = json.loads(target.read_text(encoding="utf-8"))
    for key in COMPLETE:
        assert key in body
        assert body[key] == ""
        assert body[f"_about_{key}"], f"{key} has no description to guide the answer"


def test_init_refuses_to_overwrite_answers_already_filled_in(tmp_path):
    target = tmp_path / "answers.json"
    legal.write_template(target, legal.required_keys())
    with pytest.raises(SystemExit):
        legal.write_template(target, legal.required_keys())


def test_underscore_keys_are_comments_not_answers(tmp_path):
    target = tmp_path / "answers.json"
    target.write_text(json.dumps({"_readme": "hello", **COMPLETE}), encoding="utf-8")
    assert "_readme" not in legal.load_answers(target)


def test_display_falls_back_to_an_absolute_path_outside_the_repo(tmp_path):
    """Reporting a validation failure must not itself crash."""
    assert legal.display(ROOT / "PRIVACY.md") == "PRIVACY.md"
    assert legal.display(tmp_path / "answers.json") == str(tmp_path / "answers.json")
