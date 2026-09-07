# 01-omega-developmentmd

# OMEGA Development Rules

## Environment
- Primary shell: Windows PowerShell 5.1.
- Never use `&&` or `||`.
- Run commands separately.

## Scope
- Modify only files explicitly authorized by the current task.
- No broad refactors.
- No opportunistic cleanup.
- Preserve existing formatting and EOL outside the requested scope.
- If another production file appears necessary, STOP and report before editing it.

## Verification
- Default semantic verification: exactly ONE targeted pytest process.
- FAIL ONCE → STOP.
- No automatic debug/fix/retry loop after a failed semantic test.
- No second pytest unless explicitly authorized in a new instruction.
- Static-only formatting/import/whitespace repairs do not require pytest rerun.
- Prefer targeted verification over full-suite execution.

## Git
- Never commit unless explicitly authorized.
- Never push unless explicitly authorized.
- Never use destructive Git commands unless explicitly authorized.
- Do not reset, clean, restore, rewrite history, or force push without explicit authorization.
- Always report final `git status --short`.

## Real Providers
- Do not call real external providers during implementation, audit, unit-test, or static tasks unless explicitly authorized.
- Prefer deterministic offline tests, fixtures, and mocks.

## Real Canary Rules
- A real canary is one-shot unless explicitly stated otherwise.
- Once a canary starts, ANY result consumes the attempt.
- Never rerun the same canary after success, failure, timeout, provider error, partial output, or unknown result.
- Do not run separate real-provider probes before a one-shot canary unless explicitly authorized.
- Classify failures as:
  - PRODUCT_BLOCKER
  - EXTERNAL_BLOCKER

## Task Discipline
- Read only what is necessary for the bounded task.
- Avoid rereading unchanged large files.
- Do not start unrelated subtasks.
- Do not change mode automatically.
- Do not spawn subtasks unless explicitly requested.
- Stop immediately when the task's HARD STOP condition is reached.

## OMEGA Architecture Discipline
- Preserve deterministic fail-closed behavior.
- Do not add silent fallbacks.
- Do not bypass Guardian, QA, provider safety gates, or asset/license gates.
- Do not replace repository truth with speculative architecture.
- Prefer existing services, contracts, models, and adapters over parallel implementations.
- Do not introduce cross-phase refactors unless explicitly authorized.

## Reporting
At task completion report:
- HEAD
- worktree state
- files changed
- production files changed
- tests run
- static checks
- provider calls
- render/canary executions
- blocker, if any

Do not claim PASS if required verification was not run.
Do not hide partial failures.
