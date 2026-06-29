---
name: controlled-multi-agent-workflow
description: Controlled Codex workflow for non-trivial coding, documentation, debugging, refactoring, QA, repo-maintenance, or verification tasks. Use when a task may touch multiple files, require repo discovery, need tests or review, involve risky edits, or benefit from repo_explorer, tester, reviewer, or worktree coordination.
---

# Controlled Multi-Agent Workflow

Use this skill to keep repo work deliberate, reviewable, and safe.

## Core Rules

- Follow the repository's `AGENTS.md` and any nested instruction files that apply
  to the current working directory.
- Inspect before editing. Read relevant source, tests, docs, config, and current
  git status before making non-trivial changes.
- Implement in the main thread only. Subagents may explore, test, or review, but
  they must not be the writer of record unless the user explicitly asks for a
  separate worktree implementation.
- Avoid staging or committing unless the user explicitly asks.
- Avoid deploy, live, staging, release, production, networked, destructive, or
  externally mutating commands unless the user explicitly approves them.

## Agent Use

- For non-trivial edits, use `repo_explorer` first when available to map files,
  dependencies, existing conventions, and likely tests.
- Use `tester` before non-trivial edits when available to identify focused
  checks, fixtures, likely failure modes, and safe verification scope.
- After implementation, use `reviewer` when available for meaningful behavior,
  security, API, data, or cross-file changes.
- Keep agent prompts scoped and read-only unless the user explicitly approves
  otherwise. Ask agents for evidence and file references, not broad rewrites.
- Use only one writer at a time. Do not allow multiple agents to edit the same
  files concurrently.
- Prefer separate worktrees for competing implementation attempts or risky
  alternatives.

## Planning

- Propose a brief plan before broad, risky, multi-file, user-facing, data,
  security, dependency, or architecture changes.
- Include intended files, verification approach, and known risks.
- For small localized changes, use the fast path: inspect the nearby code,
  make the minimal edit, run the smallest safe relevant check, and report.

## Implementation

- Keep changes minimal and aligned with existing patterns.
- Preserve public API behavior, security constraints, data-quality caveats, and
  user-facing workflows unless the user asked to change them.
- Update tests or docs when behavior, commands, or developer workflow changes.
- Do not invent commands, environment variables, services, deployment steps, or
  architecture details.

## Verification

- Run the smallest safe checks that cover the change first.
- Broaden to typecheck, build, compile, or full tests only when scope and risk
  justify it and the repo instructions permit it.
- Do not run app lint/test/build commands when the user forbids them.
- If checks are skipped or unverified, state that clearly with the reason.

## Handoff

- Summarize changed files, why they changed, checks run, results, and remaining
  risks.
- Mention any uncommitted unrelated work that was intentionally left alone.
