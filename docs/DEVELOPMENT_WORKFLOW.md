# Development Workflow

This project is too large to generate from a single prompt. It is built as a
staged software project: architecture first, then one feature at a time, each
one tested and documented before the next begins.

The rule that has kept it honest: **one genuinely-buildable feature at a time,
tested against reality, with what is unverified said out loud.** A feature is
not done when the code exists — it is done when it has tests, has been run, and
its limitations are written down where the next person will find them.

## The loop

Every feature goes through the same six steps.

### 1. Decide what is actually buildable

Not every requested feature is software. Triage each one into:

- **Not software** — a business, legal or operational decision (regulatory
  registration, a market-data licence, a privacy policy). Say so; don't fake it.
- **Requires abandoning the current architecture** — possible, but the cost is
  the rewrite, not the feature. Name the cost.
- **Genuinely buildable now** — build this one.

### 2. Verify the assumptions before writing code

If a feature depends on an external service, test that service first, by hand,
with `curl` or a WebSocket client. Provider documentation routinely describes a
free tier that the free tier does not actually serve. This project has been
wrong in that direction before (see decision 12 in `PROJECT_STATE.md`), which is
why "the docs say it works" is not evidence and "I called it and got data" is.

### 3. Build it

Match the surrounding code. Comments explain *why*, never *what* — the code
already says what. Where a decision could reasonably have gone another way,
write down why it went this way.

### 4. Test it against reality

Unit tests for the logic, and where possible a real run: a live provider call,
a browser session against the actual UI, a load test that reproduces the bug
before the fix and shows it gone after. A test that only proves the code does
what the code does is not worth the lines.

### 5. Document what is true, including what isn't

Update `docs/PROJECT_STATE.md` with the change, the decision behind it, and any
new limitation. If something is unverified — an adapter never run against a real
account, a provider tier never load-tested — say that in the same paragraph
where the feature is described, not in a footnote.

### 6. Commit, push, open a draft PR

Small commits with messages that explain the reasoning, not the diff.

## The documents, and which one to read

- **`docs/PROJECT_STATE.md`** — the authoritative document. Current state,
  every decision and why, known gaps, next actions. Read this first when
  resuming work.
- **`docs/TRADING_TERMINAL.md`** — how the platform works: market coverage,
  analysis pipeline, backtester, execution, full API reference.
- **`PROJECT_SPEC.md`** — architecture, stack, folder structure, design rules.
- **`README.md`** — what this is and how to run it.

## Practical expectations

Real-time market data, a deterministic analysis pipeline, a backtester with
no-look-ahead guarantees, an options pricing engine, guardrailed broker
execution and multi-user authentication are a large engineering effort. Expect
many iterations, review of generated code at every step, and real testing — not
a one-shot build.
