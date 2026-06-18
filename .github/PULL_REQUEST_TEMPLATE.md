<!--
Title format (checked by CI): {emoji} {Capitalised verb phrase}
  Good:  ✨ Add bulk update validation to mo_crud_kit
  Bad:   :sparkles: Added bulk update validation.
Use the raw emoji in the title; use :gitmoji: codes in commit messages.

Add exactly ONE label: feature | bug | enhancement | documentation | internal
-->

## Summary

<!-- What changed, in a sentence or two. -->

## Motivation / context

<!-- Why this change? Link any related issue or discussion. -->

## Testing

<!-- How you verified it. Include the exact command. -->

```bash
pytest --ds=apps.django_mindoff.tests.settings --cov=apps.django_mindoff --cov-fail-under=90
```

## Public API / breaking changes

<!-- List any additions, removals, or signature changes to the public kits
(mo_api_kit, mo_crud_kit, mo_helper_kit, mo_polars_kit, mo_response_kit,
mo_validation_kit, MindoffAPIMixin, MindoffTestCase, MindoffValidationError).
Write "none" if this is purely internal. -->

## Risks / migrations

<!-- Breaking changes, dependency model changes (pyproject.toml + checks.py),
CLI surface changes, or "none". -->

## Checklist

- [ ] One label applied (`feature` / `bug` / `enhancement` / `documentation` / `internal`)
- [ ] Tests pass locally with 90 % coverage (`pytest --ds=apps.django_mindoff.tests.settings --cov=apps.django_mindoff --cov-fail-under=90`)
- [ ] Public API docstrings updated (if behaviour changed)
- [ ] Docs page updated at `apps/django_mindoff/docs/` (or noted here why not)
- [ ] If a runtime integration dep was added/removed: both `pyproject.toml [internal]` and `checks.py` are updated
