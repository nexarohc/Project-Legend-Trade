#!/usr/bin/env python3
"""Fill the placeholders in PRIVACY.md and TERMS.md, and refuse to half-do it.

The two policy documents are templates: they are accurate about the software
but carry `{{PLACEHOLDERS}}` for the facts only the operator knows — legal name,
contact addresses, governing law. Filling those in by hand across two files is
the kind of job that gets 90% done, and the missing 10% is a policy served to
the public with `{{OPERATOR_LEGAL_NAME}}` in it.

So: put the answers in one file, run one command, and get rendered documents.
If any answer is missing the render is refused outright rather than producing
something publishable-looking with a hole in it.

    python scripts/legal.py --init          # write an answers file to fill in
    python scripts/legal.py                 # render into legal/
    python scripts/legal.py --check         # exit 1 if anything is unanswered

`--check` transmits nothing and writes nothing, so it belongs in a deployment
pipeline as a gate: no launch with an unfilled policy.

What this does NOT do is decide whether the documents are correct. It fills in
blanks. The "not yet reviewed by a lawyer" banner is left exactly where it is,
because whether that is still true is not something a script can know.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "PRIVACY.md", ROOT / "TERMS.md"]
OUTPUT_DIR = ROOT / "legal"
ANSWERS_PATH = OUTPUT_DIR / "answers.json"

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")

# These two appear *inside prose about placeholders* — "Placeholders in
# `{{BRACES}}` must be filled in" — rather than marking a blank. Substituting
# them would produce a sentence that reads like an instruction to fill in
# something that is already filled in, so the line carrying them is dropped from
# the rendered output instead: once rendered, it is no longer true.
META = {"BRACES", "PLACEHOLDER"}

# What each placeholder is for, shown in the generated answers file. Anything
# found in the documents but missing from here still gets an entry — the
# description is a convenience, not the source of truth for which keys exist.
DESCRIPTIONS = {
    "OPERATOR_LEGAL_NAME": "The legal entity running this service — the name a "
                           "regulator would write on a letter, not a brand name.",
    "SERVICE_URL": "Where the service is served, e.g. https://legendtrade.example",
    "PRIVACY_CONTACT_EMAIL": "Address for data access, correction and erasure requests.",
    "SUPPORT_CONTACT_EMAIL": "Address for account and service questions. May be the same.",
    "GOVERNING_LAW_JURISDICTION": "Whose law governs the terms, e.g. 'the laws of England "
                                  "and Wales'. A lawyer question — do not guess.",
    "LIABILITY_CAP": "The limit of liability, e.g. 'the greater of GBP 100 or fees paid "
                     "in the preceding 12 months'. A lawyer question — do not guess.",
    "MINIMUM_AGE": "Minimum age for an account, as a number, e.g. 18.",
    "SOFTWARE_LICENCE": "The licence the software itself is provided under, e.g. "
                        "'the MIT Licence' or 'a proprietary licence'.",
    "DATE": "Last-updated date. Leave blank to use today's date at render time.",
}

# Answers that need a lawyer rather than a decision. Filling these in from a
# template someone found online is the failure this flag exists to make visible.
NEEDS_A_LAWYER = {"GOVERNING_LAW_JURISDICTION", "LIABILITY_CAP"}


def display(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise.

    `--answers` and `--out` accept any path, including one outside the repo, so
    `relative_to(ROOT)` is not safe to call unguarded — it raises rather than
    falling back, and crashing while reporting a validation failure would hide
    the very thing being reported.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def placeholders_in(text: str) -> set[str]:
    return {name for name in PLACEHOLDER.findall(text) if name not in META}


def required_keys() -> list[str]:
    found: set[str] = set()
    for path in SOURCES:
        found |= placeholders_in(path.read_text(encoding="utf-8"))
    return sorted(found)


def load_answers(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object of KEY: value pairs.")
    # Comment keys let the generated file explain itself without a JSON5 parser.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def problems_with(answers: dict, keys: list[str]) -> list[str]:
    """Everything wrong with the answers, all at once.

    Reporting one problem per run turns filling in nine values into nine
    round-trips, so every check runs and every failure is listed.
    """
    issues = []
    for key in keys:
        value = str(answers.get(key, "")).strip()

        if not value:
            if key == "DATE":
                continue        # defaulted at render time
            hint = "  (this one needs a lawyer, not a guess)" if key in NEEDS_A_LAWYER else ""
            issues.append(f"{key} is unanswered{hint}")
            continue

        # A value that is still a placeholder, or is obviously the example text,
        # is worse than a blank: it renders and looks finished.
        if PLACEHOLDER.search(value) or value.lower() in {"tbd", "todo", "changeme", "n/a"}:
            issues.append(f"{key} is still a placeholder: {value!r}")
            continue

        if key.endswith("_EMAIL") and "@" not in value:
            issues.append(f"{key} does not look like an email address: {value!r}")
        if key == "SERVICE_URL" and not value.startswith(("http://", "https://")):
            issues.append(f"{key} should start with http:// or https://: {value!r}")
        if key == "MINIMUM_AGE" and not value.isdigit():
            issues.append(f"{key} should be a number of years: {value!r}")
        if key == "DATE":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                issues.append(f"{key} should be an ISO date like 2026-08-06: {value!r}")

    return issues


def render(text: str, answers: dict) -> str:
    # Drop the line that tells the reader placeholders remain — after this
    # substitution none do, so leaving it in would be a false statement.
    lines = [line for line in text.splitlines()
             if not (PLACEHOLDER.search(line) and set(PLACEHOLDER.findall(line)) <= META)]
    joined = "\n".join(lines) + "\n"
    return PLACEHOLDER.sub(lambda m: str(answers.get(m.group(1), m.group(0))), joined)


def write_template(path: Path, keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"{path} already exists — edit it, or delete it to start over.")

    body: dict = {
        "_readme": "Fill in every value, then run: python scripts/legal.py",
        "_lawyer": sorted(NEEDS_A_LAWYER),
    }
    for key in keys:
        body[key] = ""
        body[f"_about_{key}"] = DESCRIPTIONS.get(key, "")

    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {display(path)} with {len(keys)} values to fill in.")
    print("Two of them need a lawyer rather than a decision:")
    for key in sorted(NEEDS_A_LAWYER):
        print(f"  {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--answers", type=Path, default=ANSWERS_PATH)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--init", action="store_true", help="write an answers file to fill in")
    parser.add_argument("--check", action="store_true",
                        help="report unanswered values and exit non-zero; writes nothing")
    args = parser.parse_args()

    keys = required_keys()

    if args.init:
        write_template(args.answers, keys)
        return 0

    answers = load_answers(args.answers)
    if not answers and not args.answers.exists():
        print(f"No answers file at {display(args.answers)}.")
        print("Run: python scripts/legal.py --init")
        return 1

    issues = problems_with(answers, keys)
    if issues:
        print(f"NOT READY — {len(issues)} of {len(keys)} values need attention:\n")
        for issue in issues:
            print(f"  [FAIL] {issue}")
        print(f"\nEdit {display(args.answers)} and run this again.")
        return 1

    answers.setdefault("DATE", "")
    if not str(answers["DATE"]).strip():
        answers["DATE"] = dt.date.today().isoformat()

    if args.check:
        print(f"READY — all {len(keys)} values answered.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for source in SOURCES:
        rendered = render(source.read_text(encoding="utf-8"), answers)
        remaining = placeholders_in(rendered)
        if remaining:
            # Belt and braces: a placeholder added to a document after `keys`
            # was computed would otherwise reach the output.
            print(f"[FAIL] {source.name} still contains {sorted(remaining)}")
            return 1
        destination = args.out / source.name
        destination.write_text(rendered, encoding="utf-8")
        print(f"  wrote {display(destination)}")

    print(f"\nRendered as of {answers['DATE']}.")
    print("Both documents still carry the 'not yet reviewed by a lawyer' banner.")
    print("Delete that banner yourself once it stops being true — not before.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
