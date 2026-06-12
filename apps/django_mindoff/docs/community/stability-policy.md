# Stability & SemVer Policy

This page defines what `django-mindoff` considers stable, what SemVer guarantees you can rely on, and which Django and Python versions are supported at any given release.

## Public API surface

The stable, versioned surface is the set of names exported from the top-level package:

```python
from django_mindoff import (
    mo_api_kit,
    mo_crud_kit,
    mo_helper_kit,
    mo_polars_kit,
    mo_response_kit,
    mo_validation_kit,
    MindoffAPIMixin,
    MindoffValidationError,
    MindoffTestCase,
    MindoffRouterTestCase,
)
```

These names are enumerated in `__all__` and enforced by the public API contract tests. Everything inside `django_mindoff.components.*` (including `_api_kit/`, `_crud_kit/`, and all `_`-prefixed sub-packages) is **private implementation detail** — it may be reorganised or removed without notice.

### Stability tiers

| Name | Tier | Guarantee |
|---|---|---|
| `mo_api_kit`, `mo_crud_kit`, `mo_response_kit`, `mo_validation_kit` | **Stable** | Full SemVer (see below) |
| `mo_polars_kit` | **Stable** | Full SemVer; requires `[internal]` extra |
| `MindoffAPIMixin`, `MindoffValidationError`, `MindoffTestCase`, `MindoffRouterTestCase` | **Stable** | Full SemVer |
| `mo_helper_kit` | **Experimental** | API may change in a minor release; changes are noted in the changelog |
| `django_mindoff.components.*` | **Internal** | No guarantee; do not import directly |

## SemVer rules

Releases follow `vMAJOR.MINOR.PATCH`.

**MAJOR** (`2.0.0`) — significant scope expansion or, in rare cases, forced breaking change:

- One or more big feature additions that significantly improve or change the application (new kit, major new capability, architectural shift in how APIs are built).
- A public name is removed from `__all__` or renamed (see [backwards compatibility](#backwards-compatibility) below).
- An existing method's call signature changes in a way that breaks call sites (parameter removed, positional argument reordered, return type changed incompatibly).
- The JSON response envelope shape changes (field renamed, field removed).
- A supported Python or Django version is **dropped** from the lower bound.

**MINOR** (`1.1.0`) — small features and improvements that stabilise or extend the existing feature set:

- New methods, parameters (with defaults), or options added to an existing public kit.
- A new name is added to the public surface.
- New optional configuration settings are introduced.
- Quality-of-life improvements that make existing features easier to use.
- A Django or Python version is **added** to the support window.

**PATCH** (`1.0.1`) — backwards-compatible fixes:

- Bug fixes that do not change the documented behaviour.
- Security fixes.
- Documentation-only changes.

<div class="admonition warning">
<p class="admonition-title">Experimental names</p>
<p><code>mo_helper_kit</code> is experimental and may have its API adjusted in a MINOR release. Changes will always be called out explicitly in the changelog but will not force a MAJOR bump until the tier is promoted to Stable.</p>
</div>

## Backwards compatibility

We will always try to keep everything backwards compatible so that users do not have to update their applications on the fly. The goal is for upgrades across MINOR and PATCH releases to be drop-in — no code changes required.

When a deprecation is unavoidable, we follow a **graceful deprecation** path:

1. The deprecated name or behaviour is kept working and emits a deprecation warning for at least one MINOR release cycle.
2. The changelog and, where necessary, the docs clearly describe what is deprecated, why, and what to use instead.
3. Hard removal happens in the next MAJOR release, giving users ample time to migrate.

In the rare situation where a hard breaking change cannot be deferred (security fix, upstream framework constraint), a **migration guide** will be published alongside the release. The guide covers every affected call site and the exact change needed, with before/after examples, so the migration is quick and unambiguous.

## Supported version window

Only the **latest release** receives bug fixes and security updates. There are no long-term support (LTS) branches.

### Python

| Version | Status |
|---|---|
| 3.13 | Supported |
| 3.12 | Supported |
| < 3.12 | Not supported |

### Django

| Version | Status |
|---|---|
| 6.0 | Supported |
| 5.2 (LTS) | Supported |
| 5.1 | Supported |
| 5.0 | Supported |
| < 5.0 | Not supported |

The `internal` extra pins its own dependency range (DRF, Polars, Redis, Dramatiq). Consult the `[internal]` section of [`pyproject.toml`](https://github.com/mindoffwork/django-mindoff/blob/root/pyproject.toml) for exact bounds on each release.

## What "no breaking change" means in practice

- `from django_mindoff import mo_response_kit` continues to work.
- `mo_response_kit.json_response(...)` accepts the same positional and keyword arguments it accepted in the previous MINOR.
- The JSON envelope keys (`status`, `message`, `data`) are stable.
- Existing `responses.csv` entries are not renamed or removed without a MAJOR bump.

## What is explicitly not covered

- The `django_mindoff.components.*` module tree.
- Internal response codes prefixed with `QUEUE_` may be extended without a version bump.
- CLI output formatting (`django-mindoff init`, `django-mindoff nuke`) is not versioned.
- The dev-only `mindoff.py` entrypoint (not shipped) is not versioned.
