## Agent skills

### Issue tracker

Issues live in GitHub Issues; use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Use `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Use the single-context layout: root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.

### Pull requests

- When the user asks to merge changes, prepare a pull request targeting `dev` by default. Use another base only when the user names it, and merge directly only when explicitly requested.
- Use a `worktree/<name>` source branch for pull requests into `dev`; pull requests into `main` must come from `dev`. Follow `.github/workflows/branch-flow-and-tags.yml` for the enforced branch policy.
