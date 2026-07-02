# macos_agent Golden Principles

These are the principles `tools/gc_scan.py` uses as its drift backdrop. Static
signals are clues, not verdicts; high and medium risk findings need human review.

## A8 Brain Zero Regression

Harness work must not alter runtime behavior in `brain/`, `macos/`, `broker.py`,
or `run_agent.py`. If a brain change is unavoidable in future work, brain imports
must still pass and the semantic diff must be reviewed deliberately.

## Action Space Single Truth

The action names promised to the model and the action handlers executable by the
runtime must stay aligned.

## Keys Never Enter Code Or VM

Real upstream keys live only in the host broker environment. The VM receives only
a per-run broker bearer token.

## Small Modules With Docstrings

Target modules are under 400 lines. The hard lint ceiling is 500 lines. Every
module explains itself with a top-level docstring.

## Guard Fail-Closed

The VM guard uses exact model allowlists, required VM identity signals, and no
manual bypass. Unknown states refuse.

## Injection Preamble Stays Shared

Planner and extract prompts both treat observed UI text as untrusted data, never
as instructions.
