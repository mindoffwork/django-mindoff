# Contribution Guide

Thank you for your interest in contributing to Django Mindoff.

Contributions help improve the framework and make it more useful for developers building Django REST APIs. By joining hands, we can make `django-mindoff` more secure, better, faster, and simpler for all.

## Ways to Contribute

- ✨ Build new features or extend existing capabilities.
- 🐛 Fix bugs and security concerns.
- ⚡ Improve or optimize the codebase, including performance.
- 📝 Improve documentation, fill gaps, or resolve inconsistencies.
- 🚨 Open Issues to report bugs, or highlight documentation gaps.
- 💬 Use Discussions to request features, ask/answer questions or share what you built with django-mindoff.

Django Mindoff follows linear versioning system where only the latest version is actively supported. Before opening any issue, discussion or pull requests:

- Search the GitHub repository for related items to avoid duplicates.
- Make sure you're using the latest version of `django-mindoff` package.

## Code of Conduct

> **Be respectful, open-minded and constructive**.

Read the guidelines in repo: [Code of Conduct Document](https://github.com/mindoffwork/django-mindoff/blob/root/.github/CODE_OF_CONDUCT.md)

## Issues & Discussions

### Issues

Open an Issue only when something is broken or needs correction by following the issue template carefully.

Examples:

- Bugs or incorrect behavior
- Security concerns
- Incorrect documentation
- Compatibility problems with upcoming Django or Python versions

You can also contribute by confirming bugs, sharing reproducible examples, or submitting a pull request that fixes the issue.

### Discussions

Use Discussions for everything that is not a bug.

Examples:

- Feature ideas
- Questions about usage
- Architectural ideas
- Sharing projects built with django-mindoff

Django Mindoff's features are curated by the Maintainers. Reasonable feature ideas that align with the project's principles are welcome. Open feature ideas in **Discussions (not Issues)** and tag them with the `feature` label.

Avoid requesting changes to the behavior of existing features unless they are required for security reasons or to ensure compatibility with future versions of core dependencies.

## Development

### 1. Fork or Clone the Repository

**Option A - Fork**

Click **Fork** in the GitHub UI to create your own copy of the repository and Clone your fork locally:

```bash
git clone https://github.com/mindoffwork/django-mindoff.git
cd django-mindoff
```

Add the upstream remote (so you can sync changes from the original repo):

```bash
git remote add upstream https://github.com/mindoffwork/django-mindoff.git
git fetch upstream
git rebase upstream/root
```

**Option B - Clone directly (Recommended only for maintainers and collaborators)**

Clone the original repository:

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

Use [Gitmoji](https://gitmoji.dev) codes in commit messages that match the change type. Keep the text short and action-oriented. Favor clarity over creativity.

Example:

```text
:sparkles: Add bulk update validation
```

### 7. Open a Pull Request

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

<div class="admonition question">
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

<div class="admonition failure">
  <p class="admonition-title">Rejection Note</p>
  <p>Pull requests, Issues or Discussion threads that do not follow this guide properly may get rejected, closed or removed from the repo without any review or notice.</p>
</div>
