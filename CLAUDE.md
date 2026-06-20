@AGENTS.md

# Claude Code notes

- Invoke the project Graphify skill with `/graphify` after it is installed.
- A native-Windows query-first hook gives one advisory Graphify reminder per Claude session. The
  graph refresh itself is code-only and runs from the repository pre-push hook.
- Native Windows RTK use is explicit/instruction-based; the upstream transparent hook requires a
  Unix shell.
