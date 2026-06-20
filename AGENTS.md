# Loreweave agent guidance

## Token-efficient development

- Use `tools/token-reduction/Invoke-RtkPytest.ps1` for the backend suite and RTK for genuinely noisy
  supported output. The local pilot showed strong savings for pytest and diff-stat, but little value
  for the already-compact frontend build and Git status.
- On native Windows RTK use is instruction-driven; do not assume transparent command rewriting.
- Keep PowerShell scripts, interactive processes, exact-output checks, and unsupported commands raw.
- When an RTK summary is insufficient, inspect its tee/raw output before drawing conclusions.
- Use Graphify to locate architecture, ownership, and cross-document relationships once its graph is
  installed and current. Use `rg` and direct reads for exact text and line-level facts.
- During the code-only pilot, prefer `graphify explain` with unique symbols. Natural-language queries
  and duplicate labels can be ambiguous in 0.8.44. Do not install hooks, MCP, or optional backends.
- Treat Graphify as an index, never as source of truth. Verify cited source files before editing.
- Refresh after milestone merges or substantial refactors with
  `tools/token-reduction/Update-GraphifyCodeGraph.ps1`; fall back to source when stale.
- The repository pre-push hook also runs that code-only refresh automatically. Set
  `LOOM_SKIP_GRAPHIFY_PUSH_HOOK=1` only when a push must bypass local Graphify tooling.
