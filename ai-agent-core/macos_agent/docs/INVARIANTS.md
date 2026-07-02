# macos_agent Invariants

This file is the human mirror of `tools/lint_invariants.py`. Keep the IDs in
sync; `tools/lint_knowledge.py` fails when either side drifts.

## INV-01 File Size

Every non-test Python file stays at or below 500 lines. Oversized files must be
split, or explicitly recorded in `tools/known_exceptions.yaml` with a reason and
review date.

## INV-02 Brain Does Not Import Playwright

Top-level imports in `brain/` must not pull `playwright`, `agent.dom`,
`agent.actions`, or `agent.browser`. macOS injects observer/action implementations
instead.

## INV-03 A8 Brain Import Works

`import brain.agent, brain.llm, brain.utils` must succeed without Playwright.
Local development still needs the lightweight OpenAI client dependencies.

## INV-04 Action Space Single Truth

`macos.prompts.ACTION_SPEC` must match `macos.actions._HANDLERS` plus inert
`wait` and `done`.

## INV-05 Injection Preamble Shared

The planner system prompt and extract system prompt must both carry the
data/instruction isolation preamble.

## INV-06 Broker Uses Constant-Time Token Compare

The broker must compare bearer tokens with `hmac.compare_digest`, not naked
string equality.

## INV-07 No Hard-Coded `sk-*` Keys

No source, script, or doc may contain a real-looking `sk-` secret. Real keys enter
only through environment variables on the host broker.

## INV-08 Guard Exact Allowlist, No Bypass

VM model checks must use exact allowlist membership, never fuzzy `Virtual`
substring logic, and no source path may add a `--force` guard bypass.

## INV-09 Extract Goes Through Broker

`Planner.extract` must pass `base_url=` and `api_key=` through to
`extract_information_json` so extract uses the broker instead of direct upstream
calls.

## INV-10 `rm -rf` Has Path Guards

Shell scripts must guard destructive `rm -rf "$VAR"` targets in the same block
with non-empty or `.utm` path checks.

## INV-11 DomState Contract

`MacDomState` must expose `url`, `title`, `page_text`, `elements`, `get`, and
`to_prompt`; `MacElement` must expose `index`, `text`, `attributes`, and
`render`.

## INV-12 Module Docstrings

Every Python module, except empty `__init__.py`, starts with a docstring so
agents can orient themselves quickly.
