# Contribution Guide

Thank you for your interest in contributing to Django Mindoff.

Contributions help improve the framework and make it more useful for developers building Django REST APIs. By joining hands, we can make `django-mindoff` more secure, better, faster, simpler, and easier for all.

## Ways to Contribute

- ✨ Build new features or extend existing capabilities.
- 🐛 Fix bugs and security concerns.
- ⚡ Improve or optimize the codebase, including performance.
- 📝 Improve documentation, fill gaps, or resolve inconsistencies.
- 🚨 Open Issues to request features, report bugs, or highlight documentation gaps.
- 💬 Use Discussions to answer questions or share what you built with django-mindoff.

<div class="admonition warning">
  <p class="admonition-title">Note on Security Issues</p>
  <p>If you discover a security vulnerability, do not open a public issue. Please follow the <a href="security.md">Security Policy</a>.</p>
</div>

## Before You Start

Before beginning work on a contribution:

1. Check existing Issues to see if the problem is already reported.
2. If you plan to add a feature and want it included in the project, consider opening a discussion first.
3. Keep pull requests focused, small and centered around one thing at a time.

## Development Setup

### 1. Fork or Clone the Repository

**Option A - Fork**

1. Click **Fork** in the GitHub UI to create your own copy of the repository.
2. Clone your fork locally:

```bash
git clone https://github.com/mindoffwork/django-mindoff.git
cd django-mindoff
```

3. Add the upstream remote (so you can sync changes from the original repo):

```bash
git remote add upstream https://github.com/mindoffwork/django-mindoff.git
git fetch upstream
git rebase upstream/root
```

**Option B - Clone directly (Recommended only for maintainers and collaborators)**

1. Clone the original repository:

```bash
git clone https://github.com/mindoffwork/django-mindoff.git
cd django-mindoff
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -e ".[internal]"
```

If you plan to edit documentation, install docs dependencies as well:

```bash
pip install -e ".[internal,docs]"
```

Django Mindoff uses `mkdocs` for documentation. Use the following command to run the docs locally and open the local URL shown in the terminal to preview live rendering:

```bash
mkdocs serve
```

### 4. Running Tests

Before submitting a pull request, ensure that the test suite passes and that **coverage is at 90% minimum**.

```bash
pytest
```

### 5. Branch Naming Strategy

_Good Practices, Recommended for Maintainers and Collaborators_

All branches must start with one of the following prefixes:

- `feature/*` - New user-facing functionality or capabilities.
- `bug/*` - Fixes for incorrect behavior, regressions, or defects.
- `documentation/*` - Docs-only updates (guides, README, examples).
- `enhancement/*` - Improvements to existing behavior without adding a new feature.
- `internal/*` - Refactors, tooling, CI changes, or non-user-facing maintenance.

Examples:

```text
feature/bulk-update-validation
bug/queue-timeout-fix
documentation/contribution-guide
enhancement/validation-performance
internal/ci-changelog-step
```

### 6. Commit Message Guidelines

Use [Gitmoji](https://gitmoji.dev) codes in commit messages that matches the change type. keep the text short and action-oriented. Please favor clarity over creativity.

Example:

```text
:sparkles: Add bulk update validation
```

### 7. Push Changes and Open a Pull Request

1. Push your branch: `git push origin <branch-name>`

2. Open a Pull Request targeting the `root` branch on `mindoffwork/django-mindoff`. If you are using fork, read about [creating a pull request from a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request-from-a-fork)

## Pull Request Guidelines

- Ensure tests pass and coverage is at least 90% as mentioned earlier
- Follow the coding conventions and project structure used in the existing codebase. Refer [architecture documentation](../architecture/index.md) for details.
- Update documentation based on the changes. Otherwise, mention clearly that it's not documented in the PR description.
- Run linting before submitting PRs using `black` formatting standards.

### 1. PR Title

Pattern:

```
{emoji} {Verb-starting phrase}
```

- Use the **actual emoji** in place of `emoji` (not the `:gitmoji:` code) so it renders correctly in release notes. Use `:sparkles:`-style codes in commit messages, and raw emoji in PR titles.
- `Verb-starting phrase` should not start with a participle verb and should not end with a period, as it's a phrase, not a sentence. (Use "Add", not "Added")

Good Example ✅:

```
✨ Add bulk update validation
```

Bad Example ❌:

```
:sparkles: Added bulk update validation.
```

### 2. PR Labels (Required)

Pick the label that best matches the main change in your branch. Do not choose more than one:

- `feature` - New functionality or capability.
- `bug` - Fix for incorrect behavior or regression.
- `documentation` - Docs-only changes.
- `enhancement` - Improvements to existing behavior without a new feature.
- `internal` - Refactors, tooling, CI, or changes that doesn't affect users.

### 3. PR Description

<div class="admonition info">
  <p class="admonition-title">When it's Optional</p>
  <p>PR Descriptions are optional if the title itself is self-explanatory or if you are the reviewer of the PR you're creating.</p>
</div>

Keep the title and description simple, accurate, and easy to understand. Clearly explain what changed and why, how to test (including specific commands or steps), and any potential risks, migrations, or breaking changes (if applicable). Always review them before submitting.

**General rule:** a good PR description should take reviewers less time to review than it took you to write it.

Suggested structure (Styling is encouraged, if you have the time):

- Summary
- Motivation / context
- Testing
- Risks / migrations (if applicable)

Example:

```text
Summary:
Add bulk update validation for CSV uploads to prevent invalid rows from being written.

Motivation / context:
Users reported silent failures when invalid rows were included. This adds explicit validation and error reporting.

Testing:
pytest apps/<app_name>/tests/test_apis/test_bulk_update.py -q

Risks / migrations:
None.
```

## One Last Thing

Please respect the maintainers' time and effort.

AI tools can churn out code, PRs, or comments in a flash, but reviewing them still takes real human work on our end. Flooding the repo with automated or half-baked submissions creates a kind of Human Effort Denial-of-Service attack on the project.

That said, modern tools like AI can be a huge help when used wisely. They speed up development, boost code quality, and help you learn faster. Just don't let them replace careful thinking and intentions.

If you're using AI or similar tools, always review, understand, and test your changes before submitting a PR. Make sure your submissions are spot-on and genuinely useful for the project and easy on the reviewer.

<div class="admonition warning">
  <p class="admonition-title">Important Note</p>
  <p>Pull requests that do not follow this guide properly may get rejected without a review.</p>
</div>
