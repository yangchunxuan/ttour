# macos_agent Agent Map

macos_agent is a macOS VM desktop agent: a reused ReAct brain drives an
Accessibility-based observer/executor through a host-side broker that keeps real
model keys out of the VM.

## Repository Map

- `brain/`: reused pure-Python planner and loop code. Treat behavior as frozen
  unless the spec explicitly authorizes a change.
- `macos/`: macOS observer, executor, prompts, VM guard, session, and AX helpers.
- `broker.py`: host-side key broker and DeepSeek request policy gate.
- `run_agent.py`: VM entrypoint that wires guard, broker config, session, and brain.
- `scripts/`: UTM restore, VM identity seeding, and broker launch scripts.
- `tests/`: offline tests that avoid real VM, pyobjc, AX, and network.
- `docs/`: durable design knowledge; keep details here, not in this map.
- `tools/`: harness lint, knowledge checks, GC scan, and known exception ledger.
- `check.sh`: one-command local harness verification.

## Read Before Editing

- [docs/macos-desktop-agent-spec.md](docs/macos-desktop-agent-spec.md), especially
  `§7A`, before touching any brain integration.
- [docs/INVARIANTS.md](docs/INVARIANTS.md) before changing architecture seams.
- [docs/PRINCIPLES.md](docs/PRINCIPLES.md) before broad refactors.
- Run `./check.sh` before handing work back.

## Iron Rules

- Do not change runtime behavior in `brain/`, `macos/`, `broker.py`, or
  `run_agent.py` for harness-only work; module docstrings are the only allowed
  exception.
- Real API keys never enter code or the VM. They flow through the host broker.
- `macos.prompts.ACTION_SPEC` and `macos.actions._HANDLERS` are one truth source:
  `click`, `type`, `select`, `scroll`, `press_key`, `launch_app`, `close_window`,
  `wait`, `extract`, `done`.
- The VM guard is fail-closed: precise model allowlist, no fuzzy `Virtual`
  substring check, no `--force` bypass.
- Planner and extract prompts must both carry the injection/data isolation
  preamble.

## Local Harness

- `python3 tools/lint_invariants.py`
- `python3 tools/lint_knowledge.py`
- `python3 tools/gc_scan.py`
- `./check.sh`

Known historical debt lives in `tools/known_exceptions.yaml`; exceptions warn
instead of disappearing.
