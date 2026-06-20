# Token-reduction tooling implementation journal

Plan source: `.docs/kb-tok-reduce.md` in this repository.

Branch: `feature/token-reduction-tooling`

## Status

| Milestone | State | Result |
| --- | --- | --- |
| M0 - baseline and safety | Complete | Branch, raw baseline, repeatable runner, and rollback documented |
| M1 - RTK native Windows pilot | Complete (selective) | Correctness/recovery pass; retain only for locally useful commands |
| M2 - RTK agent guidance | In progress | Concise guidance and reliable pytest wrapper added; adoption not measured yet |
| M3 - Graphify code-only pilot | Complete (explicit) | Local AST graph passed focused checks; broad-query limits recorded |
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

## 2026-06-20 - M3 Graphify code-only pilot

Installed outside the repository:

| Item | Value |
| --- | --- |
| `uv` | 0.11.21 via winget |
| Graphify package | `graphifyy==0.8.44` in an isolated uv tool environment |
| CLI | `graphify 0.8.44` |
| Optional extras | None |
| Query logging | Disabled during pilot commands with `GRAPHIFY_QUERY_LOG_DISABLE=1` |

The project installer was more aggressive than its help suggested. Both platform installs also
patched root guidance and generated query-first hooks. The generated Claude hook was Bash-based;
the Codex hook embedded `C:\Users\stubz\.local\bin\graphify.EXE`; and the Codex skill was placed
under `.codex/skills` despite Codex's documented repo skill location being `.agents/skills`.

Those generated integrations were not accepted. The root patches and all hooks were removed. The
portable Windows/PowerShell skill remains under `.claude/skills/graphify`, and a reviewed copy is
under `.agents/skills/graphify`. Both copies were changed to fail closed if Graphify is missing and
to retain the 0.8.44 pin instead of silently installing/upgrading a package. Query-first hooks, Git
hooks, MCP, semantic backends, and global graphs remain deferred.

### Code-only extraction

Ran:

```powershell
$env:GRAPHIFY_QUERY_LOG_DISABLE = "1"
graphify update . --no-cluster
```

Result:

- 102 code/config files extracted locally, no LLM/backend request;
- 1,931 nodes and 3,664 edges in `graphify-out/graph.json`;
- 101 unique node source paths;
- zero source-path violations from `.docs/`, `pipelines/`, runtime state, secrets, generated output,
  or `.token-tools/`;
- graph artifacts remain ignored and uncommitted during the pilot.

`Update-GraphifyCodeGraph.ps1` now provides the pinned, logging-disabled, AST-only refresh path. It
requires an explicit `-Force` for Graphify's deletion-heavy rebuild override.

### Accuracy checks

Ten focused `graphify explain` checks were verified against source:

| Symbol | Canonical source | Result |
| --- | --- | --- |
| `create_app` | `orchestrator/main.py:597` | Correct |
| `JobRunner` | `orchestrator/runner.py:279` | Correct |
| `DiskGuard` | `orchestrator/diskguard.py:65` | Correct |
| `flux2_sampling_presets` | `orchestrator/model_catalog.py:360` | Correct |
| `resolve_l1` | `orchestrator/bible.py:119` | Correct |
| `join_negative` | `orchestrator/bible.py:137` | Correct |
| `export_profile` | `orchestrator/assets.py:662` | Correct |
| `import_profile` | `orchestrator/assets.py:699` | Correct |
| `build_caption` | `orchestrator/coverage.py:73` | Correct |
| `add_step` | `orchestrator/postproc.py:82` | Correct |

All ten resolved to the correct source and their sampled extracted containment/call edges matched
source. Graphify's own benchmark estimates a 142,666-token naive corpus, about 9,895 tokens per
average graph query, and 14.4x reduction. This is a tool estimate, not an independent correctness
measure.

Important limitations:

- broad natural-language queries return candidate subgraphs, not a synthesized answer;
- `explain "generate"` selected the frontend symbol and stable-ID text still fuzzily matched
  `GenerateRequest`, so duplicate-label disambiguation is unreliable;
- inferred `uses` edges can be noisy (for example, `DiskGuard` linked to many request models);
- the graph is useful for symbol-first orientation but does not yet replace direct architectural
  reading or source verification.

M3 is accepted for explicit, focused lookup only. M4 document extraction must remain gated until a
provider/data-residency decision is made; M5 hooks and M6 MCP remain deferred.

## 2026-06-20 - automatic pre-push graph refresh

Git has no standard client-side `post-push` hook. Added the closest deterministic equivalent: a
repository `pre-push` hook under `.githooks/` that runs the pinned, AST-only
`Update-GraphifyCodeGraph.ps1` immediately before network transfer. A successful push therefore
leaves the local graph describing the source state that was pushed.

The hook:

- performs no semantic extraction and keeps Graphify query logging disabled through the wrapper;
- is fail-open, warning without blocking a push if Graphify or PowerShell is unavailable;
- supports one-push bypass with `LOOM_SKIP_GRAPHIFY_PUSH_HOOK=1`;
- is activated per clone by `Install-GraphifyPushHook.ps1`, which safely refuses to overwrite a
  different existing `core.hooksPath`.

Also added a native-Windows Claude `PreToolUse` query-first hook in `.claude/settings.json`. Unlike
the upstream generated Bash hook, it runs through Windows PowerShell. It emits advisory context only
once per Claude session when broad Read/Glob/Grep or shell-search exploration begins, skips Graphify
commands and graph artifacts, and fails open. It recommends scoped `explain`/`query` use while
preserving direct source reads for verification. The per-session marker lives under the ignored
`graphify-out/.hook-state/` directory.
