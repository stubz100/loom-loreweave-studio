# Token Reduction for Loreweave Development - RTK and Graphify

Created: 2026-06-20
Status: research and implementation plan; no tools installed yet
Target repository: `loom/loom-loreweave-studio/`
Audience: local development with Claude Code and OpenAI Codex on Windows

---

## 1. Executive summary

Loreweave is now large enough that development sessions repeatedly spend context on two kinds of
material:

1. verbose command output such as tests, builds, git status/diffs, and search results; and
2. rediscovering architecture by reopening the same source and planning documents.

RTK and Graphify address those different costs:

| Tool | Primary job | Where tokens are saved | Best Loreweave use |
| --- | --- | --- | --- |
| **RTK (Rust Token Killer)** | Run common development commands through command-specific output filters | Before terminal output reaches the model | Tests, builds, git, file/search output, dependency listings |
| **Graphify** | Extract code/docs into a queryable knowledge graph | Avoid repeatedly reading broad sets of files | Architecture discovery, dependency tracing, cross-phase/document questions |

They are complementary. RTK compresses the evidence produced during work; Graphify helps select
which evidence needs to be opened at all.

**Recommendation:** adopt them incrementally, in this order:

1. pilot RTK manually on native Windows;
2. add concise project instructions for Claude Code and Codex;
3. build a code-only Graphify pilot for the Loom app repository;
4. add the large `.docs/` corpus only after code-query quality is proven;
5. enable always-on Graphify hooks, git hooks, or MCP only after measurement.

Do not put either tool into the Loreweave application or Story Bundle runtime. They are development
tooling only.

---

## 2. Research scope and evidence status

Research was performed against the upstream repositories, package metadata, current official
Claude Code/Codex documentation, and the local machine/repository state on 2026-06-20.

Claims such as "60-90%" RTK reduction and Graphify's "71.5x" mixed-corpus benchmark are upstream
benchmarks, not independent guarantees. The implementation plan therefore includes a local
benchmark and explicit go/no-go gates.

### 2.1 Current upstream versions checked

| Project | Verified current/pinned candidate | Notes |
| --- | --- | --- |
| RTK | **v0.42.4** latest non-prerelease GitHub release checked | Newer `dev-0.43.0-rc.*` builds exist; do not pilot on an RC. Windows zip and `checksums.txt` are published. |
| Graphify | **graphifyy 0.8.44** on PyPI | Package name has two `y` characters; CLI remains `graphify`. Active source branch is `v8`. |

Both projects are young and changing quickly. Pin versions during the pilot and re-read release
notes before upgrading.

---

## 3. Local baseline

### 3.1 Repository facts

The real app path is `loom/loom-loreweave-studio/` (without the extra `r` in `loreweave`). It is a
separate Git repository inside the larger workspace.

Current tracked corpus:

| Type | Files | Approx. bytes | Approx. lines |
| --- | ---: | ---: | ---: |
| Python | 127 | 1,262,540 | 24,663 |
| Markdown | 12 | 718,367 | 7,970 |
| TSX | 2 | 136,208 | 3,007 |
| JSON | 17 | 92,283 | 2,527 |
| TypeScript | 3 | 43,440 | 990 |
| CSS | 1 | 25,293 | 438 |
| Rust | 3 | 7,937 | 172 |

There are 224 tracked files in total. The large Markdown corpus is especially relevant: it contains
the roadmap, implementation journals, and cross-phase decisions that are expensive to reread but
valuable for architectural questions.

At research time, the app repository already had user changes in:

- `app/src/App.tsx`
- `app/src/lib/orchestrator.ts`
- `app/src/styles.css`
- `orchestrator/model_catalog.py`
- `orchestrator/tests/test_model_catalog.py`

Do not run either installer in that dirty worktree without first preserving/committing the current
work and reviewing every generated file.

### 3.2 Local tool state

| Component | Local state |
| --- | --- |
| Windows | Native Windows/PowerShell workflow |
| Python | 3.12.10 available as `python` and `py` |
| Rust/Cargo | Cargo 1.96.0 available |
| Codex | `codex-cli 0.142.0-alpha.6`, supplied by the OpenAI VS Code extension |
| Claude Code | VS Code extension 2.1.183; binary exists inside the extension, but `claude` is not on `PATH` |
| RTK | Not installed / not on `PATH` |
| Graphify | Not installed / not on `PATH` |
| `uv` / `pipx` | Neither is installed |

The Loom app repo currently has no `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/`, `.codex/`, or
`.graphifyignore`. The user-level Claude settings contain no hooks. The Codex config has a
`[features]` table but no explicit `multi_agent` entry.

---

## 4. RTK - technology findings

### 4.1 What RTK is

RTK, or Rust Token Killer, is a command-line proxy from `rtk-ai/rtk`. It executes an underlying
development command, filters that command's stdout/stderr using a command-aware formatter, preserves
the underlying exit status, and sends the compact result back to the caller.

It is a single Rust binary with no runtime language dependencies. Upstream publishes a native
Windows MSVC zip.

Do not confuse it with the unrelated Rust Type Kit package that also uses the `rtk` binary name.
The verification command is `rtk gain`: Rust Token Killer implements it; Rust Type Kit does not.

### 4.2 How it works internally

RTK's command lifecycle is approximately:

1. parse the RTK command and the wrapped command arguments;
2. execute the real tool as a subprocess;
3. capture stdout, stderr, and exit code;
4. apply a command-specific parser/filter;
5. optionally retain the full output through the tee subsystem;
6. estimate input/output tokens and store usage metrics;
7. print the compact output and preserve the wrapped tool's exit code.

Its filtering strategies include:

- noise removal (progress bars, boilerplate, repeated success lines);
- grouping by file, rule, package, or error type;
- deduplication of repeated log lines;
- truncation with a recovery path to full output;
- structured parsing where tools expose JSON/NDJSON;
- state-machine parsing for output such as pytest lifecycle events;
- language-aware file reading at different detail levels.

RTK estimates tokens at roughly four characters per token and stores tracking data in SQLite. That
estimate is useful for trend comparison, but it is not the exact tokenizer used by Claude or Codex.

### 4.3 Command families relevant to Loom

Useful commands for this repository include:

```powershell
rtk git status
rtk git diff
rtk pytest orchestrator/tests -q
rtk npm run build
rtk cargo test
rtk grep "pattern" .
rtk read orchestrator/main.py
rtk ls .
rtk gain --history
```

RTK also has dedicated filters for Jest/Vitest/Playwright, Ruff, TypeScript, GitHub CLI, Docker,
package managers, logs, JSON structure, and generic error-only or test wrappers.

### 4.4 Failure recovery and correctness

The tee feature is important for code review. With tee enabled, a failed command keeps the full raw
output in a local file and reports that path in the compact result. The agent can inspect the raw
file without rerunning an expensive test.

Expected safety properties from upstream:

- wrapped exit codes are preserved;
- filtering failures fall back to raw behavior;
- hooks fail open if RTK is missing or cannot parse their payload;
- `-v`, `-vv`, and `-vvv` expose progressively more detail;
- `rtk proxy <command>` bypasses filtering while retaining tracking;
- `RTK_DISABLED=1` bypasses automatic rewriting for one command.

These properties must be tested locally. Output compression can hide context even if the exit code
is correct, particularly for unusual test plugins or new command versions.

### 4.5 Windows behavior

The RTK binary and filters work on native Windows. The supplied transparent Claude rewrite hook,
however, is a Bash script and requires a Unix shell. On native Windows, `rtk init -g` falls back to
instruction injection: Claude is asked to call RTK, but commands are not mechanically rewritten.

WSL enables the full RTK Bash hook, but switching Loreweave development into WSL would also change
path handling, Python environments, model paths, GPU tooling, and access to the existing Windows
workspace. WSL should not be introduced solely for RTK during the first pilot.

Native Windows recommendation:

- use the native `rtk.exe`;
- use explicit RTK commands or project instructions;
- consider WSL hook mode only after the manual pilot proves value.

### 4.6 Claude Code and Codex integration models

RTK provides different integration tiers:

| Host | Upstream mechanism | Local consequence |
| --- | --- | --- |
| Claude Code on Linux/WSL | `PreToolUse` Bash hook that rewrites Bash input | Transparent and mechanically enforced |
| Claude Code on native Windows | `CLAUDE.md` instruction fallback | Best-effort model behavior, not enforcement |
| Codex | `AGENTS.md` plus `RTK.md` awareness instructions | Best-effort model behavior; RTK ships no Codex rewrite hook |

Current official Codex documentation does support project lifecycle hooks in `.codex/hooks.json`,
but RTK does not currently ship a Codex hook implementation. Do not imply that `rtk init --codex`
provides transparent rewriting; upstream describes it as instruction-only.

### 4.7 Privacy and telemetry

RTK stores local tracking and tee files. It also supports optional anonymous telemetry. Upstream
documentation is inconsistent: the README says telemetry is disabled until explicit consent, while
the sample configuration shows `[telemetry] enabled = true`.

For a deterministic local setup:

```toml
[telemetry]
enabled = false
```

Also set `RTK_TELEMETRY_DISABLED=1` in the user environment if a hard override is desired, and verify
with `rtk telemetry status` after installation.

Tee output may contain source paths, test data, prompts, or secrets printed by a failing process.
Keep tee files local, rotate them, and never commit the RTK data directory.

### 4.8 RTK strengths and limitations

Strengths:

- immediate savings on verbose, repetitive output;
- small native binary and low overhead;
- no application integration;
- exit-code preservation and raw-output recovery;
- measurable per-command adoption/savings.

Limitations:

- upstream percentage claims vary with command and project size;
- filters can omit details needed for novel failures;
- native Windows lacks RTK's transparent Claude hook;
- Codex integration is prompt-level only;
- PowerShell cmdlets and complex scripts are not universally filterable;
- exact-output tasks still require raw commands or tee inspection.

---

## 5. Graphify - technology findings

### 5.1 What Graphify is

Graphify (`safishamsi/graphify`) is a Python package and coding-assistant skill that turns a folder
into a NetworkX knowledge graph. It produces:

```text
graphify-out/
|-- graph.html
|-- GRAPH_REPORT.md
|-- graph.json
|-- manifest.json
|-- cache/
`-- cost.json
```

The exact output set depends on the command/options. `graph.json` is the queryable source;
`GRAPH_REPORT.md` is a human-readable overview; HTML/export outputs are secondary views.

### 5.2 Extraction pipeline

Graphify uses three main passes:

1. **Code structure:** tree-sitter extracts classes, functions, imports, calls, comments, and
   language-specific relationships locally. Code-only extraction requires no model API.
2. **Video/audio:** optional faster-whisper transcription runs locally and caches transcripts.
3. **Documents/papers/images:** an assistant model or configured backend extracts semantic nodes and
   edges. This consumes model tokens and may send those files to the selected provider.

The core pipeline is:

```text
detect -> extract -> build_graph -> cluster -> analyze -> report -> export
```

Extractors return plain dictionaries which are validated before being merged into a NetworkX graph.
Graphify fingerprints files with SHA-256 and can skip unchanged files on subsequent updates.

### 5.3 Graph and clustering model

Nodes include stable identifiers, labels, source file/location, and file type. Edges include source,
target, a verb-like relation, source provenance, and confidence.

Confidence classes are:

- `EXTRACTED`: explicit source relationship, confidence 1.0;
- `INFERRED`: model or second-pass inference with a confidence score;
- `AMBIGUOUS`: uncertain relationship requiring review.

Community detection uses graph structure (Leiden where available), not a mandatory vector database.
Semantic edges extracted by the model influence clustering.

This means Graphify is knowledge-graph retrieval, but it is not the same as the GraphRAG runtime
planned for Loreweave. It has no mandatory embedding/vector layer and should remain a developer map,
not become the application's canonical project-context implementation.

### 5.4 Query and access modes

The lowest-overhead access path is the CLI:

```powershell
graphify query "how does the queue launch a pipeline worker?"
graphify path "GenerateRequest" "run_pipeline"
graphify explain "ModelCatalog"
```

Graphify also supports:

- generated reports and HTML views;
- update/watch and post-commit rebuilds;
- graph merge and cross-project global graphs;
- Neo4j/FalkorDB export or push;
- an optional MCP server exposing `query_graph`, node/neighbor/path tools, and PR tools.

MCP is not automatically the most token-efficient choice. MCP tool schemas consume startup context,
and broad MCP results can themselves be large. Start with the skill plus CLI query path; A/B test MCP
later.

### 5.5 Token economics

Graph creation has an up-front cost. Code AST extraction is local/free, but semantic extraction of
Markdown and other documents consumes model tokens. Savings only appear after repeated queries avoid
reopening the corpus.

Upstream reports:

- roughly 71.5x fewer tokens/query on a 52-file mixed corpus;
- about 5.4x on a small code-plus-paper corpus;
- about 1x on a six-file synthetic library.

The Loom corpus is large enough to be a credible fit, especially the 12 large planning/journal
documents. The benchmark must still compare answer correctness, not only input size.

### 5.6 Privacy and local processing

Upstream behavior:

- code is parsed locally with tree-sitter;
- video/audio transcription is local when that extra is installed;
- docs, PDFs, and images use the current assistant or configured backend;
- Graphify reports no telemetry/analytics;
- query metadata is logged by default to `~/.cache/graphify-queries.log`;
- full query responses are not logged unless separately enabled.

Disable query logging if desired:

```powershell
$env:GRAPHIFY_QUERY_LOG_DISABLE = "1"
```

Never run Graphify at the parent monorepo root without a deliberate ignore policy. That could include
model artifacts, generated outputs, unrelated projects, local logs, and secrets.

### 5.7 Windows and packaging

Graphify requires Python 3.10+; local Python 3.12.10 is compatible. Upstream recommends `uv tool`
or `pipx`, especially on Windows, to avoid interpreter/PATH mismatches. `uv` is not yet installed.

Pinned pilot installation:

```powershell
winget install astral-sh.uv
uv tool install "graphifyy==0.8.44"
graphify --version
```

Do not use a similarly named PyPI package. The official package is `graphifyy`.

Optional extras such as `mcp`, `pdf`, `office`, `video`, `neo4j`, and `ollama` should not be installed
until needed. Loom's first pilot needs only the base package.

### 5.8 Proposed Loom corpus policy

Graphify respects `.gitignore` and an additional `.graphifyignore`. Start with a narrow, code-first
corpus:

```gitignore
# Runtime/generated/local state
.dev_out/
.loom_state/
.pytest_cache/
app/dist/
app/src-tauri/gen/
app/src-tauri/target/
**/__pycache__/

# Secrets, machine config, and heavy artifacts
.env*
*.safetensors
*.gguf
*.ckpt
*.pt
*.bin
*.png
*.ico
*.icns

# Vendored pipeline copies are useful at runtime but duplicate upstream code.
# Exclude for the first graph and add selectively if cross-pipeline tracing needs them.
pipelines/

# Phase 1 only: defer semantic doc extraction until code graph quality is accepted.
.docs/
```

After the code-only pilot, remove `.docs/` from `.graphifyignore` and run an update through one agent
session. Do not exclude `.docs/` permanently: the phase specs and journals are likely Graphify's
highest-value corpus for scope/decision questions.

### 5.9 Graphify strengths and limitations

Strengths:

- deterministic local AST extraction for the main Python/TS/Rust code;
- source provenance and explicit confidence classes;
- incremental hash cache;
- query/path/explain access instead of full-report reads;
- repo-scoped skills for both Claude and Codex;
- good conceptual fit for cross-document roadmap questions.

Limitations:

- semantic extraction has an up-front token cost;
- inferred edges can be wrong or stale;
- generated graphs need refresh discipline;
- the graph is an index, not an authoritative source;
- large `GRAPH_REPORT.md` reads can erase the intended savings;
- always-on hooks can over-nudge simple exact-file tasks;
- optional MCP adds tool-schema/context overhead;
- upstream interfaces are moving quickly.

---

## 6. Combined operating model

Use the cheapest authoritative path for each question:

| Task | Preferred path |
| --- | --- |
| Run tests/build/lint/git and inspect normal results | RTK wrapper |
| Investigate an unfamiliar failure | RTK compact result, then tee/raw output if needed |
| Ask architecture/dependency/ownership questions | Scoped `graphify query` first |
| Find exact text, line, schema key, or current implementation | `rg` and direct file read |
| Verify a Graphify answer before editing | Open the cited canonical files |
| Long-running/interactive server or exact stream | Raw command; do not filter |
| Update knowledge after material code/docs changes | `graphify update`, then spot-check |

Suggested durable rule:

> Use Graphify to locate and relate; use source files to verify; use RTK to compress verbose command
> output; use raw output whenever exact detail matters.

This avoids two failure modes: treating a stale graph as source of truth, and treating compressed
terminal output as complete diagnostic evidence.

---

## 7. Local Claude Code integration

### 7.1 Installation scope

Use project-scoped files in `loom/loom-loreweave-studio/` during the pilot. Avoid global behavior
changes until the setup is proven.

Claude Code officially supports:

- project instructions in `CLAUDE.md` or `.claude/CLAUDE.md`;
- project skills under `.claude/skills/<name>/SKILL.md`;
- project hooks in `.claude/settings.json`;
- local-only hooks/settings in `.claude/settings.local.json`;
- stdio MCP servers through `claude mcp add` or `.mcp.json`.

### 7.2 RTK for Claude Code

Native Windows first:

1. install the pinned `rtk.exe` release and verify its checksum;
2. verify `rtk --version` and `rtk gain`;
3. explicitly disable telemetry;
4. use `rtk init` or manually add a concise RTK section to project `CLAUDE.md`;
5. do not expect transparent rewriting on native Windows;
6. verify Claude actually emits `rtk pytest`, `rtk git status`, and `rtk npm run build`.

If WSL is later adopted, `rtk init --global` can install RTK's Bash `PreToolUse` hook. Inspect the
generated hook/settings diff before accepting it. The upstream hook fails open, but it still runs on
every matching Bash call.

Use targeted guidance rather than "prefix every shell command":

- use RTK for supported, verbose development commands;
- do not wrap PowerShell scripts, interactive servers, exact-output checks, or unsupported commands;
- inspect tee/raw output when a failure summary is insufficient.

### 7.3 Graphify for Claude Code

From the clean app repo root:

```powershell
graphify install --project --platform windows
```

This should install a project skill under `.claude/skills/graphify/`. Claude invokes it as
`/graphify`; in a normal PowerShell terminal, use `graphify .` because `/graphify` is parsed as a
path.

After building and validating the first graph, optionally add query-first behavior:

```powershell
graphify claude install --project
```

This may add/patch `CLAUDE.md` and a `PreToolUse` hook that nudges searches/reads toward the graph.
Review `.claude/settings.json` and use Claude's `/hooks` view to verify the source and matcher.

### 7.4 Shared instructions without duplication

Keep common rules in `AGENTS.md`. Claude Code does not read `AGENTS.md` directly, but official
Claude documentation supports importing it from `CLAUDE.md`:

```markdown
@AGENTS.md

# Claude-specific notes

- `/graphify` is the explicit Graphify skill invocation.
- Native Windows RTK use is instruction-based; no transparent Bash hook is assumed.
```

On Windows, prefer this import over a symlink. Keep `CLAUDE.md` concise because it is loaded into
every session.

### 7.5 Optional Graphify MCP for Claude

Only after CLI/skill measurement:

```powershell
uv tool install --force "graphifyy[mcp]==0.8.44"
claude mcp add --transport stdio --scope project graphify -- graphify-mcp graphify-out/graph.json
```

The current Claude binary lives inside a versioned VS Code extension path and is not on `PATH`.
Do not commit that absolute extension path. Make the `claude` command available normally or write a
portable `.mcp.json` instead.

---

## 8. Local OpenAI Codex integration

### 8.1 Codex surfaces verified

Official Codex documentation confirms:

- repo guidance belongs in `AGENTS.md`;
- repo skills belong in `.agents/skills/` and load progressively;
- repo hooks can live in `.codex/hooks.json`;
- non-managed hooks must be reviewed/trusted, using `/hooks`;
- stdio MCP servers use `[mcp_servers.<name>]` in `config.toml`;
- `multi_agent` and `hooks` are stable feature flags and currently documented as enabled by default.

Graphify nevertheless explicitly asks Codex users to set `multi_agent = true`. Add it to the
existing local `[features]` table rather than creating a duplicate table:

```toml
[features]
js_repl = false
multi_agent = true
```

### 8.2 RTK for Codex

Project-scoped setup:

```powershell
rtk init --codex
```

Upstream says this injects guidance into project `AGENTS.md` with an `RTK.md` reference. It does not
mechanically rewrite Codex shell calls.

Review the generated instructions. Replace an unconditional "always prefix shell commands" rule
with the targeted combined operating rule in Section 6 if needed. Codex frequently uses PowerShell
scripts and exact-output inspection where an RTK wrapper is inappropriate.

### 8.3 Graphify for Codex

Install the repo skill:

```powershell
graphify install --project --platform codex
```

Expected project output includes `.agents/skills/graphify/SKILL.md` plus references. Codex invokes
the skill as `$graphify`.

After the graph is proven, install query-first guidance:

```powershell
graphify codex install --project
```

Upstream currently says this writes `AGENTS.md` and `.codex/hooks.json`. Official Codex docs confirm
that project `PreToolUse` hooks are supported, but a new/changed hook is skipped until reviewed and
trusted. Open `/hooks`, inspect the exact command and hash, and trust it deliberately.

If the project is not marked trusted, Codex ignores project `.codex/` hooks/config while still
reading the normal instruction chain appropriate to the session.

### 8.4 Optional Graphify MCP for Codex

Only after A/B testing the skill/CLI path, a project or user Codex config may add:

```toml
[mcp_servers.graphify]
command = "graphify-mcp"
args = ["graphify-out/graph.json"]
cwd = "F:\\source\\repos\\stubz-002-tripo-sf\\loom\\loom-loreweave-studio"
```

The absolute `cwd` belongs in user-local config, not a committed project file. For a shared project
config, use a portable launcher script or omit MCP.

### 8.5 Hook interaction

Codex runs all matching hooks from all active layers; matching hooks can run concurrently. A
Graphify hook should only nudge query selection, while RTK remains instruction-only in Codex. If a
future RTK Codex hook is added, test concurrent behavior rather than assuming hook order.

---

## 9. Proposed repository artifacts

After a successful project-scoped rollout, the app repo may contain:

```text
loom/loom-loreweave-studio/
|-- AGENTS.md                         # shared concise RTK + Graphify operating rules
|-- CLAUDE.md                         # imports AGENTS.md + Claude-specific notes
|-- RTK.md                            # generated RTK command reference, if retained
|-- .graphifyignore                   # explicit corpus boundary
|-- .agents/skills/graphify/          # Codex skill
|-- .claude/skills/graphify/          # Claude skill
|-- .claude/settings.json             # only portable reviewed hooks
|-- .codex/hooks.json                 # reviewed Graphify hook, if accepted
`-- graphify-out/                     # generated graph artifacts
```

Commit policy recommendation:

| Artifact | Pilot | After acceptance |
| --- | --- | --- |
| `AGENTS.md`, `CLAUDE.md` | Review locally | Commit |
| Graphify skills/references | Review generated content | Commit pinned generated version |
| `.graphifyignore` | Commit immediately after review | Commit |
| Agent hook files | Keep local until verified | Commit only if portable and desired |
| `graphify-out/graph.json`, report, manifest | Keep untracked for first pilot | Consider committing for reproducible shared context |
| `graphify-out/cost.json`, caches | Never commit | Ignore/local only |
| RTK config/history/tee | User-local only | Never commit |

Upstream recommends committing `graphify-out/` for teams. Loom is currently a solo/local workflow,
so generated graph churn should earn its place before becoming repository history.

---

## 10. Implementation plan

### M0 - baseline and safety

**Goal:** establish measurements and a reversible starting point before installing anything.

Work:

- finish/preserve the current dirty Loom changes;
- create a dedicated tooling branch;
- record current Claude/Codex versions and config backups;
- select 8-10 representative tasks;
- capture baseline raw output characters/lines and host token use where available;
- define correctness answers for architecture queries.

Representative tasks:

1. run the full orchestrator test suite and summarize failures;
2. run the UI production build;
3. inspect git status and a medium diff;
4. trace a request from React client to FastAPI route to adapter;
5. explain the model catalog and download path;
6. find which P2 decisions changed after implementation work;
7. locate all code that owns postprocessing lineage;
8. review one cross-module change for regressions.

Acceptance:

- baseline saved without source/config changes;
- all expected answers and command exit codes recorded;
- rollback paths documented.

### M1 - RTK native Windows manual pilot

**Goal:** prove command compression independently from agent hooks.

Work:

- download pinned RTK v0.42.4 Windows MSVC zip and `checksums.txt`;
- verify checksum and place `rtk.exe` in a user-local PATH directory;
- verify `rtk gain` identifies Rust Token Killer;
- disable telemetry explicitly;
- configure tee for failures and a small retention limit;
- run the baseline command set both raw and through RTK.

Acceptance:

- every wrapped exit code matches raw execution;
- no test/build failure is misreported as success;
- full failure output is recoverable without rerunning;
- median terminal-output reduction is at least 50% on the selected verbose commands;
- unsupported/exact-output commands remain raw.

Rollback:

- remove `rtk.exe` from the user-local bin directory;
- remove local RTK config/history/tee data if desired.

### M2 - RTK agent guidance

**Goal:** make Claude and Codex use RTK appropriately without forcing unsupported commands.

Work:

- add project-scoped RTK guidance for Codex;
- add/import the same concise rule for Claude Code;
- test native Windows behavior in both hosts;
- verify that complex PowerShell and exact-output commands are not incorrectly wrapped;
- inspect `rtk gain --history` for actual adoption.

Acceptance:

- at least 80% of eligible verbose commands use RTK in a representative session;
- no blanket wrapping of every shell/PowerShell action;
- instructions add little persistent context;
- both agents know how to request raw/tee detail.

### M3 - Graphify code-only pilot

**Goal:** validate deterministic architecture retrieval without per-file semantic model extraction.

Work:

- install `uv` and pinned `graphifyy==0.8.44`;
- create `.graphifyignore` with the narrow code-first policy;
- install project-scoped Claude and Codex Graphify skills;
- explicitly enable Codex `multi_agent` as requested by Graphify;
- build the graph while `.docs/`, media, and vendored pipelines are excluded;
- inspect `GRAPH_REPORT.md`, ambiguous edges, and graph size;
- run at least ten scoped queries and verify answers against source.

Acceptance:

- code extraction is local and does not request a cloud backend for the code-only corpus (the host
  assistant session still consumes its normal tokens);
- at least 90% of test-query relationships are correct after source verification;
- every accepted answer points to canonical source files;
- graph queries reduce broad file reads by at least 30% on architecture tasks;
- query output is smaller than the raw files it replaces.

Rollback:

- `graphify uninstall --project --platform codex` and corresponding Claude uninstall;
- delete `graphify-out/` and generated skill/config files after reviewing diffs;
- uninstall the uv tool.

### M4 - planning/docs graph

**Goal:** make the roadmap and implementation journals queryable without repeatedly loading them in
full.

Work:

- remove `.docs/` from `.graphifyignore`;
- run one controlled semantic update through the chosen agent/backend;
- record extraction token cost;
- review high-impact inferred/ambiguous edges;
- test questions about scope drift, milestone status, decision supersession, and code-to-plan links;
- keep docs/provider data-residency behavior explicit.

Acceptance:

- Graphify correctly distinguishes phase spec from implementation journal;
- superseded decisions are not presented as current without source verification;
- five representative roadmap questions require materially fewer raw document reads;
- one-time extraction cost has a plausible break-even point for expected use.

### M5 - query-first integration and freshness

**Goal:** introduce always-on assistance without allowing a stale graph to become authority.

Work:

- compose concise shared `AGENTS.md` and Claude import;
- optionally install Graphify's Claude/Codex query-first hooks;
- inspect and trust Codex hooks through `/hooks`;
- inspect Claude hooks through `/hooks` and verify settings source;
- define manual freshness rule: update after milestone merges or major refactors;
- defer `graphify hook install` until generated churn is understood;
- add a visible stale-graph fallback: query, then verify source.

Acceptance:

- simple exact lookups still use `rg`/direct reads;
- architecture questions try a scoped graph query first;
- both agents cite/verify source before edits;
- stale graph behavior is detected in a deliberate refactor test;
- hooks fail open and do not block ordinary commands.

### M6 - optional MCP A/B test

**Goal:** determine whether structured graph tools save more tokens than their persistent schema cost.

Work:

- install only the Graphify `mcp` extra;
- configure one host first, not both;
- compare CLI query versus MCP for the same five tasks;
- measure startup/context overhead, output size, latency, and answer quality;
- enable the second host only if the result is positive.

Acceptance:

- MCP provides measurable net savings or materially better graph navigation;
- tool output remains scoped;
- server startup and path handling are reliable on native Windows;
- otherwise remove MCP and retain skill/CLI queries.

### M7 - final measurement and maintenance policy

**Goal:** decide what becomes permanent.

Work:

- rerun the M0 benchmark with RTK only, Graphify only, and both;
- compare host token usage, terminal bytes, file reads, latency, and correctness;
- document accepted versions and update cadence;
- decide whether `graphify-out/` and hook configs are committed;
- schedule manual graph refreshes at milestone completion;
- re-run Graphify skill/hook installation after package upgrades;
- re-run RTK initialization after upgrades if hook files are used.

Success targets:

| Metric | Target |
| --- | ---: |
| RTK median output reduction on eligible commands | >= 50% |
| Eligible-command RTK adoption | >= 80% |
| Graphify verified architecture-query accuracy | >= 90% |
| Reduction in broad raw-file reads for graph-suited tasks | >= 30% |
| Missed failures / incorrect exit statuses | 0 |
| Source edits made solely from unverified graph claims | 0 |

Final decision options:

- **Keep both:** likely outcome if RTK safely compresses tests and Graphify helps with cross-document
  reasoning.
- **Keep RTK only:** if Graphify extraction/query upkeep does not repay its cost.
- **Keep Graphify only:** if terminal output is already compact but architecture rediscovery dominates.
- **Keep neither:** if instructions/hooks create more friction or context than they save.

---

## 11. Risks and guardrails

| Risk | Guardrail |
| --- | --- |
| RTK hides a diagnostic detail | Tee failures; raw/proxy fallback; compare baseline failures |
| RTK wrong-package name collision | Require `rtk gain` verification |
| Native Windows hook assumptions | Treat RTK as explicit/instruction-based unless running inside WSL |
| Tool instructions inflate every session | Keep `AGENTS.md`/`CLAUDE.md` short; details live in skills/reference files |
| Graphify graph is stale | Update at defined milestones; verify against source before edits |
| Inferred graph edges are wrong | Inspect confidence/provenance; never treat inferred edges as canonical |
| Semantic extraction leaks data | Strict repo root + `.graphifyignore`; explicit backend/data-residency decision |
| Generated files churn in git | Local pilot first; commit only stable, useful artifacts |
| Hook conflict or trust failure | Install one integration at a time; inspect host hook views and diffs |
| MCP schemas erase savings | Defer MCP and A/B test against CLI query |
| Upstream breaking changes | Pin RTK/Graphify versions; upgrade deliberately |

---

## 12. Recommended initial decision

Proceed, but do not install everything at once.

The first practical slice should be M0-M1 only: preserve current Loom work, install pinned RTK
v0.42.4 natively, disable telemetry, and benchmark tests/build/git manually. This has the lowest
blast radius and gives immediate evidence.

If that succeeds, Graphify is worth a code-only pilot. Loom's 31K+ measured lines of source plus
nearly 8K lines of planning/journal Markdown are large enough for graph retrieval to be useful, but
the docs should be added only after the offline AST graph proves accurate.

---

## 13. Sources

### RTK primary sources

- [RTK repository](https://github.com/rtk-ai/rtk)
- [RTK v0.42.4 release](https://github.com/rtk-ai/rtk/releases/tag/v0.42.4)
- [RTK README](https://github.com/rtk-ai/rtk/blob/develop/README.md)
- [RTK architecture](https://github.com/rtk-ai/rtk/blob/develop/docs/contributing/ARCHITECTURE.md)
- [RTK supported agents](https://github.com/rtk-ai/rtk/blob/develop/docs/guide/getting-started/supported-agents.md)
- [RTK configuration](https://github.com/rtk-ai/rtk/blob/develop/docs/guide/getting-started/configuration.md)
- [RTK Claude hook](https://github.com/rtk-ai/rtk/tree/develop/hooks/claude)
- [RTK Codex guidance](https://github.com/rtk-ai/rtk/tree/develop/hooks/codex)

### Graphify primary sources

- [Graphify repository, v8 branch](https://github.com/safishamsi/graphify/tree/v8)
- [Graphify README](https://github.com/safishamsi/graphify/blob/v8/README.md)
- [Graphify package on PyPI](https://pypi.org/project/graphifyy/)
- [How Graphify works](https://github.com/safishamsi/graphify/blob/v8/docs/how-it-works.md)
- [Graphify architecture](https://github.com/safishamsi/graphify/blob/v8/ARCHITECTURE.md)

### Official agent integration sources

- [Codex AGENTS.md guidance](https://developers.openai.com/codex/guides/agents-md)
- [Codex skills](https://developers.openai.com/codex/skills)
- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Claude Code memory and CLAUDE.md](https://code.claude.com/docs/en/memory)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
