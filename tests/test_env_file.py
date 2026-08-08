"""Loading `.env` into the process environment.

This exists because of a trap rather than a feature. `.env` is read by
pydantic-settings into a Settings *object*, while broker adapters read
`os.environ` directly — so credentials documented as belonging in `.env` were
invisible to the adapters unless Docker happened to inject them. The symptom
was a broker reporting itself unconfigured with a correctly filled-in file
sitting right there.
"""
import os
from pathlib import Path

import pytest

from trading.env import load_env_file, parse_env_file

ROOT = Path(__file__).resolve().parent.parent


def test_plain_assignments():
    parsed = parse_env_file("ALPACA_PAPER_KEY_ID=PK123\nALPACA_PAPER_SECRET_KEY=abc\n")
    assert parsed == {"ALPACA_PAPER_KEY_ID": "PK123", "ALPACA_PAPER_SECRET_KEY": "abc"}


def test_comments_and_blank_lines_are_ignored():
    parsed = parse_env_file("# a comment\n\n   \nKEY=value\n")
    assert parsed == {"KEY": "value"}


def test_export_prefix_is_accepted():
    """`.env` files are routinely `source`d by shells, so this form is common."""
    assert parse_env_file("export KEY=value") == {"KEY": "value"}


@pytest.mark.parametrize("line,expected", [
    ('KEY="value"', "value"),
    ("KEY='value'", "value"),
    ('KEY="with spaces"', "with spaces"),
    ("KEY=", ""),
])
def test_quotes_delimit_the_value_and_are_not_part_of_it(line, expected):
    assert parse_env_file(line)["KEY"] == expected


def test_a_hash_inside_a_value_is_kept():
    """Truncating a secret at a '#' would fail authentication invisibly.

    Stripping trailing comments is a common dotenv behaviour and the wrong
    trade here: '#' is a plausible character in a generated secret, and the
    resulting failure looks like a wrong key rather than a mangled one.
    """
    assert parse_env_file("SECRET=abc#def")["SECRET"] == "abc#def"


def test_a_value_containing_equals_is_kept_whole():
    """Base64 secrets end in '=' padding."""
    assert parse_env_file("SECRET=YWJjZGVm==")["SECRET"] == "YWJjZGVm=="


def test_malformed_lines_are_skipped_rather_than_raising():
    """A stray line must not stop the credentials below it from loading."""
    parsed = parse_env_file("this line has no equals sign\n=novalue\nKEY=value\n")
    assert parsed == {"KEY": "value"}


def test_load_populates_os_environ(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LEGEND_TEST_LOADED=yes\n", encoding="utf-8")
    monkeypatch.delenv("LEGEND_TEST_LOADED", raising=False)

    assert load_env_file(env) == ["LEGEND_TEST_LOADED"]
    assert os.environ["LEGEND_TEST_LOADED"] == "yes"


def test_the_real_environment_wins(tmp_path, monkeypatch):
    """In a container the orchestrator is the authority, not a stale file."""
    env = tmp_path / ".env"
    env.write_text("LEGEND_TEST_PRECEDENCE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LEGEND_TEST_PRECEDENCE", "from-environment")

    assert load_env_file(env) == []
    assert os.environ["LEGEND_TEST_PRECEDENCE"] == "from-environment"


def test_override_is_available_but_not_the_default(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LEGEND_TEST_OVERRIDE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LEGEND_TEST_OVERRIDE", "from-environment")

    assert load_env_file(env, override=True) == ["LEGEND_TEST_OVERRIDE"]
    assert os.environ["LEGEND_TEST_OVERRIDE"] == "from-file"


def test_an_empty_environment_value_is_treated_as_unset(tmp_path, monkeypatch):
    """`.env.example` ships `ALPACA_PAPER_KEY_ID=` — an empty var must not win.

    Copying the example file and exporting nothing leaves the name defined and
    blank. If blank counted as "already set", the real value would be ignored.
    """
    env = tmp_path / ".env"
    env.write_text("LEGEND_TEST_BLANK=real-value\n", encoding="utf-8")
    monkeypatch.setenv("LEGEND_TEST_BLANK", "")

    assert load_env_file(env) == ["LEGEND_TEST_BLANK"]
    assert os.environ["LEGEND_TEST_BLANK"] == "real-value"


def test_a_missing_file_is_not_an_error(tmp_path):
    """The normal case for a container deployment."""
    assert load_env_file(tmp_path / "nonexistent") == []


def test_the_adapter_sees_credentials_that_only_exist_in_a_dotenv(tmp_path, monkeypatch):
    """The end-to-end point of all of the above.

    Constructing the adapter is what previously reported `configured == False`
    for a correctly filled-in file, because it reads os.environ at construction.
    """
    from trading.execution.alpaca import AlpacaAdapter
    from trading.execution.base import ExecutionMode

    for name in ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    adapter = AlpacaAdapter(ExecutionMode.BROKER_PAPER)
    assert adapter.configured is False, "precondition: no credentials yet"

    env = tmp_path / ".env"
    env.write_text(
        "ALPACA_PAPER_KEY_ID=PKTESTONLY\nALPACA_PAPER_SECRET_KEY=secret-test-only\n",
        encoding="utf-8",
    )
    load_env_file(env)

    assert AlpacaAdapter(ExecutionMode.BROKER_PAPER).configured is True


# --- the template must actually be in the repository -------------------------
#
# `.gitignore` carried `.env*`, which matched `.env.example` as well as `.env`.
# The template was therefore never committed, and nothing complained: the file
# existed locally, `git status` stayed clean, and the omission only surfaced
# when someone cloned the repo and found `copy .env.example .env` had nothing to
# copy. A rule that hides a file from `git status` cannot be caught by reading
# `git status`, so it gets a test.

def _git(*args) -> str:
    import subprocess
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=False).stdout.strip()


@pytest.fixture(scope="module")
def in_a_git_checkout():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")


def test_the_env_template_is_tracked_by_git(in_a_git_checkout):
    """A fresh clone must contain .env.example — it is the only enumeration of
    which variables exist, and the runbook tells operators to copy it."""
    assert _git("ls-files", "--error-unmatch", ".env.example") == ".env.example", (
        ".env.example is not tracked; check .gitignore has not swallowed it again"
    )


def test_a_real_env_file_is_still_ignored(in_a_git_checkout):
    """The negation for the template must not have opened a hole for the real one."""
    assert _git("check-ignore", ".env") == ".env"


def test_the_template_holds_no_credentials():
    """Committing the template is only safe while every secret slot is empty."""
    import re

    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    secretish = re.compile(r"^([A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*)=(.*)$")

    populated = []
    for line in template.splitlines():
        match = secretish.match(line.strip())
        if match and match.group(2).strip():
            populated.append(match.group(1))

    assert populated == [], f"credential slots carry values in .env.example: {populated}"


def test_the_template_documents_the_alpaca_paper_pair():
    """The runbook's first broker step depends on these two names being present."""
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ALPACA_PAPER_KEY_ID=" in template
    assert "ALPACA_PAPER_SECRET_KEY=" in template
