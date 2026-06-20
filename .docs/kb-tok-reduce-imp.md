# Token-reduction tooling implementation journal

Plan source: `.github/copilot/kb-tok-reduce.md` in the parent workspace.

Branch: `feature/token-reduction-tooling`

## Status

| Milestone | State | Result |
| --- | --- | --- |
| M0 - baseline and safety | Complete | Branch, raw baseline, repeatable runner, and rollback documented |
| M1 - RTK native Windows pilot | Complete (selective) | Correctness/recovery pass; retain only for locally useful commands |
| M2 - RTK agent guidance | In progress | Concise guidance and reliable pytest wrapper added; adoption not measured yet |
| M3 - Graphify code-only pilot | Pending | Corpus boundary added; package/skills not installed |
| M4 - planning/docs graph | Pending | `.docs/` deliberately excluded until M3 passes |
| M5 - query-first integration | Pending | No hooks installed |
| M6 - optional MCP A/B test | Pending | Deferred by design |
| M7 - final measurement | Pending | Requires accepted pilots |

## 2026-06-20 - branch and M0 baseline

Created `feature/token-reduction-tooling` from `main` at `1e89375`. The following pre-existing,
uncommitted product edits were preserved unchanged:

- `app/src/App.tsx`
- `app/src/lib/orchestrator.ts`
- `app/src/styles.css`
- `orchestrator/model_catalog.py`
- `orchestrator/tests/test_model_catalog.py`

Raw baseline from the feature-branch worktree:

| Command | Exit | Elapsed | Output | Result |
| --- | ---: | ---: | ---: | --- |
| `..\..\.venv\Scripts\python.exe -m pytest orchestrator/tests -q` | 0 | 22,259 ms | 15 lines / 1,311 chars | 259 passed, 2 warnings |
| `npm.cmd run build` from `app/` | 0 | 2,078 ms | 13 lines / 561 chars | TypeScript and Vite build passed |
| `git status --short --branch` | 0 | n/a | 6 lines / 183 chars | Five pre-existing modified files |
| `git diff --stat` | 0 | n/a | 11 lines / 938 chars | 205 insertions, 26 deletions; includes line-ending warnings |

The pytest warnings are a Starlette/httpx deprecation and a sandbox-specific denied write to the
existing `.pytest_cache`; neither failed the suite. Host-reported model token use is not available
from this shell baseline, so command characters/lines are the stable local proxy.

The first attempted frontend measurement used PowerShell's `npm` resolution and was interpreted as
`npm pm`; the reproducible command and benchmark script use `npm.cmd` explicitly on Windows.

Added:

- `tools/token-reduction/Measure-TokenTools.ps1` for repeatable raw/RTK measurements;
- `.graphifyignore` with the M3 code-only corpus boundary;
- concise shared `AGENTS.md` guidance and a small Claude import shim.

No RTK/Graphify package, hook, skill, MCP server, or application dependency has been installed yet.
Generated Graphify data and benchmark result files remain local-only.

## 2026-06-20 - M1 RTK native Windows pilot

Installed and verified RTK outside the repository:

| Item | Value |
| --- | --- |
| Version | `rtk 0.42.4` |
| Release asset | `rtk-x86_64-pc-windows-msvc.zip` |
| Verified SHA-256 | `f0ec18963581657173bd6a51f5ba012b093823f844db749fec218581af30a568` |
| Binary | `%USERPROFILE%\.local\bin\rtk.exe` (existing PATH directory) |
| Config | `%APPDATA%\rtk\config.toml` |
| Tracking/tee | `%LOCALAPPDATA%\rtk\` |

`rtk telemetry disable` was run and independently checked: consent remains `never asked`, telemetry
is disabled, and no device salt exists. The default v0.42.4 config already supplies the desired
bounded recovery policy: tee on failures, at most 20 files, at most 1 MiB each.

### Paired benchmark

The final paired run used the same worktree for raw and RTK commands. Its ignored machine-readable
report is `.token-tools/comparison.json`.

| Command | Raw chars | RTK chars | Reduction | Exit parity |
| --- | ---: | ---: | ---: | --- |
| Orchestrator pytest | 826 | 18 | 97.8% | Yes (259 passed) |
| Frontend build | 561 | 528 | 5.9% | Yes |
| Git status | 282 | 281 | 0.4% | Yes |
| Full Git diff | 18,643 | 16,104 | 13.6% | Yes |
| Git diff-stat | 1,091 | 393 | 64.0% | Yes |

The median across all five commands is 13.6%, below the plan's general 50% gate. RTK is therefore
accepted selectively, not as a blanket command prefix. The two locally high-value noisy cases
(pytest and diff-stat) have a median reduction of 80.9%. Already terse commands remain raw unless a
future RTK release materially improves them.

### Windows pytest correction

RTK invokes `pytest.exe`, while Loom's canonical `python -m pytest` implicitly puts the repo root on
`sys.path`. The first RTK attempt therefore returned exit 2 with no collected tests because
`orchestrator` could not be imported. `Invoke-RtkPytest.ps1` fixes this deterministically by:

- prepending Loom's shared venv `Scripts` directory to `PATH`;
- temporarily prepending the repo root to `PYTHONPATH`;
- invoking RTK from the repo root;
- restoring both environment variables and preserving RTK's exit code.

With that correction, RTK reports `Pytest: 259 passed` with exit 0.

### Failure recovery

An ignored, intentional oversized assertion probe returned exit 1. RTK reduced it to 389 characters
and reported the complete output at `%LOCALAPPDATA%\rtk\tee\1781964496_pytest.log` (7,482 bytes),
proving recovery without rerunning. The temporary probe was deleted afterward.

Rollback remains external and simple: remove `%USERPROFILE%\.local\bin\rtk.exe` and, if no history
is wanted, `%APPDATA%\rtk` plus `%LOCALAPPDATA%\rtk`. Do not remove these automatically from a repo
script.
