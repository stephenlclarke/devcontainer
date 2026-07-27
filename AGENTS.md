# Agent Rules

If `~/.agents/AGENTS.md` exists, read it and follow it.

## Project rules

- Target unmodified, tagged releases of `apple/container` and `apple/containerization` first.
- Treat `container-compose` as a separately installed provider; do not import `ComposeCore` into the runtime-neutral core.
- Prove supported behavior against the pinned Docker and `@devcontainers/cli` reference lane, the stock Apple lane, and the `container-compose` lane.
- Do not normalize or waive functional parity differences. A stable release requires zero semantic differences in its claimed compatibility scope.
- Keep the Docker Engine compatibility API local to a user-owned Unix socket. Do not expose a TCP listener by default.
- Keep all source, scripts, documentation, and generated notices compatible with Apache License 2.0.
- Use `main` for release-facing work, one validated Conventional Commit per completed slice, and never push to Apple-owned remotes.
- Keep `README.md`, `DESIGN.md`, `COMPATIBILITY.md`, DocC articles, and parity fixtures aligned with behavior.
