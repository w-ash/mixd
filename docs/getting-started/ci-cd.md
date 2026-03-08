# CI/CD with GitHub Actions

Trigger Claude Code from issue and PR comments with `@claude`, plus recommendations for standard quality gates.

---

## Claude Code @mention Workflow

Triggers when someone writes `@claude` in an issue, PR comment, or review.

```yaml
# .github/workflows/claude.yml
name: Claude Code
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]
  pull_request_review:
    types: [submitted]

jobs:
  claude:
    if: |
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review' && contains(github.event.review.body, '@claude')) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@claude'))
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
      issues: read
      id-token: write
      actions: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1
      - uses: anthropics/claude-code-action@beta
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

**Setup**: Add `CLAUDE_CODE_OAUTH_TOKEN` as a repository secret. See the [Claude Code Action docs](https://github.com/anthropics/claude-code-action) for OAuth setup.

---

## Standard Quality Gate Workflow

For automated checks on every push/PR, add a second workflow:

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - run: pip install poetry && poetry install
      - run: poetry run ruff check .
      - run: poetry run ruff format --check .
      - run: poetry run basedpyright src/
      - run: poetry run pytest

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
          cache-dependency-path: web/pnpm-lock.yaml
      - run: pnpm --prefix web install
      - run: pnpm --prefix web check
      - run: pnpm --prefix web test
      - run: pnpm --prefix web build
```

This mirrors the local quality gates from the [commands cheat sheet](README.md#essential-commands-cheat-sheet) — what passes locally should pass in CI.
