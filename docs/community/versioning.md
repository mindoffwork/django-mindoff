# Versioning

Django Mindoff follows a linear versioning system where only the latest release is supported for security and bug fixes. Some projects may remain on a feature-complete or legacy release, so documentation is preserved per release to keep those teams unblocked.

Releases and their changes are tracked in the [Release Notes](../release_notes.md). That page is the source of truth for what changed in each version and is the best starting point if you're upgrading or validating behavior changes.

**IMPORTANT NOTE:**

VERSIONING IS EXPERIMENTAL FOR NOW AND CAN BE CONSIDERED UNSTABLE AND THE WORKFLOW BELOW MAY CHANGE IN FUTURE VERSIONS.

THE FOLLOWING WORKFLOW IS FOR MAINTAINERS' USE ONLY. IF YOU'RE A CONTRIBUTOR OR JUST PASSING BY, THIS VERSIONING GUIDE WON'T SERVE ANY PURPOSE EXCEPT KNOWLEDGE ON HOW DJANGO MINDOFF AND ITS DOCUMENTATION IS VERSIONED.

## How Project Versioning Works

1. The project version is set in `pyproject.toml` and represents the current release line.
2. A release is cut by creating a Git tag in the form `vX.Y.Z` that matches the project version.
3. Only release tags that are published under releases are considered official and eligible for documentation versioning.

## How Documentation Versioning Works

1. Each documentation version extends from the project's version and corresponds to a release tag that was explicitly deployed.
2. Documentation versions are stored in the `gh-pages` branch, and `versions.json` powers the version selector.
3. The `latest` alias is updated to point to the newest deployed release.
