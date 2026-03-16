# Deployment and Versioning

<div class="admonition info">
<p class="admonition-title">For Maintainers only</p>
<p>This is the day-to-day guide for shipping releases and docs for <code>django-mindoff</code>. If you are a contributor or user, feel free to skim and make use of it in your own projects.</p>
</div>

Django Mindoff uses linear versioning where only the latest release is supported for security and bug fixes. Some teams may remain on feature-complete or legacy releases, so documentation is preserved per release to keep those projects unblocked.

Releases and their changes are tracked in the [Release Notes](../release_notes.md). That page is the source of truth for what changed in each version and is the best starting point if you are upgrading or validating behavior changes.

## Deployment Workflow

### Deploy the Package

Releases are created in GitHub and finalized by the deployment pipeline.

1. Create a tag in the format: `vX.Y.Z`
2. Publish that tag as a GitHub Release.
3. The deployment pipeline then:

- Updates `pyproject.toml` with the release version
- Updates `changelog.md` so changes land under the correct version
- Reverts those changes if the release process fails

Once the pipeline finishes cleanly, the release is live.

### Deploy the Documentation

Source docs live in `docs/` directory. Rendered docs are built with MkDocs and Mike, then pushed to the `docs` branch.

<div class="admonition note">
<p class="admonition-title">Key points to remember</p>
<ul>
<li>The <code>docs</code> branch contains generated output only. Do not edit it by hand.</li>
<li>Both the <code>root</code> branch and the <code>docs</code> branch are protected. Do not attempt to merge each other.</li>
</ul>
</div>

The public documentation site is:

```
https://django.mindoff.work/
```

Before you deploy, make sure your working tree is clean, `docs/` contains the final content
and you are on the correct branch

Example for the `2.1` series:

```bash
mike deploy --push docs --update-aliases v2.1 latest-release
mike set-default --push latest-release
```

This:

- Builds the docs
- Publishes them to the `docs` branch
- Updates the `latest-release` alias

### Update an existing documentation version

If you need to fix docs for the same series:

```bash
mike deploy --push 2.1
```

This replaces the existing rendered docs for that version.

## Versioning Policy

Release tags always follow: `vX.Y.Z` format  
Where:

| Component | Meaning       |
| --------- | ------------- |
| X         | Major version |
| Y         | Minor version |
| Z         | Patch version |

Rules we follow:

1. A release is official only after the tag is published as a GitHub Release.
2. The GitHub release title must match the tag exactly.
3. The deployment pipeline updates `pyproject.toml` from the tag.
4. Only the latest release line receives fixes and security updates.

### Documentation Versioning

Docs versions are created per major release series, not per patch. Patch releases update the existing docs for that series rather than creating a new docs version. The version selector on the site is driven by `versions.json`.

Example:

| Release | Documentation Version |
| ------- | --------------------- |
| v2.1.0  | 2.1                   |
| v2.1.3  | 2.1                   |
| v2.1.4  | 2.1                   |
| v3.0.0  | 3.0                   |

**Documentation Aliases**

The alias `latest-release` always points to the newest published docs version. This ensures users land on the recommended docs by default.

Example:

| Alias          | Points to                          |
| -------------- | ---------------------------------- |
| latest-release | 3.0                                |
| 2.1            | documentation for the 2.1.x series |

## Rollbacks

### Release Rollback

If a release needs to be rolled back:

1. Unpublish or delete the GitHub Release for the bad tag.
2. If you need to reuse the same tag for another release, delete the tag itself.
3. Yank the release on PyPI for the `django-mindoff` org so new installs no longer resolve to it.
4. If a corrected release is needed, create a new patch tag and publish it as a new GitHub Release.
5. The deployment pipeline will update `pyproject.toml` from the corrected tag.

When you are ready to cut the corrected release, return to the [Deployment Workflow](#deploy-the-package).

### Documentation Rollback

If a docs deploy introduces an issue, redeploy the last known good version:

```bash
mike deploy --push <version>
```

This restores the previous docs for that series.
