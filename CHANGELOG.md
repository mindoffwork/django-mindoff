<h1>Release Notes</h1>

## Recent Changes

### Enhancements
- 🔧 Update scaffold gitignore defaults ([#35](https://github.com/mindoffwork/django-mindoff/pull/35))
- ⚡ Optimize framework performance and simplify CRUD operations ([#34](https://github.com/mindoffwork/django-mindoff/pull/34))

## v0.6.0

### Enhancements
- ✨ Improve runtime safety and response resource guards ([#33](https://github.com/mindoffwork/django-mindoff/pull/33))
- ✨ Expand public API surface and harden compatibility coverage ([#32](https://github.com/mindoffwork/django-mindoff/pull/32))
- 💄 Refresh docs design and update README ([#31](https://github.com/mindoffwork/django-mindoff/pull/31))

## v0.5.0

### Enhancements
- 📝 Move documentation to package folder ([#27](https://github.com/mindoffwork/django-mindoff/pull/27))

### Features
- ✨ Add CORS defaults to init scaffold ([#28](https://github.com/mindoffwork/django-mindoff/pull/28))

### Fixes
- 🐛 Load parent models from all apps during foreign key selection ([#29](https://github.com/mindoffwork/django-mindoff/pull/29))

## v0.4.0

### Fixes

- 🐛 Fix exception code not matching response code ([#23](https://github.com/mindoffwork/django-mindoff/pull/23))

### Enhancements

- 🚚 Add top-level dramatiq worker entrypoint and remove legacy `_api_kit` worker module ([#26](https://github.com/mindoffwork/django-mindoff/pull/26))
- 🔨 Add AGENTS.md on `init` and corresponding tests ([#24](https://github.com/mindoffwork/django-mindoff/pull/24))

### Internal

- ♻️ Sanitize docstrings in the whole codebase ([#25](https://github.com/mindoffwork/django-mindoff/pull/25))

## v0.3.0

### Internal

- 👷 Update codecov status in root ci ([#19](https://github.com/mindoffwork/django-mindoff/pull/19))

### Documentation

- 📝 Update overall documentation and improve documentation design ([#20](https://github.com/mindoffwork/django-mindoff/pull/20))

### Fixes

- 🐛 Fix `init` to read the packages from metadata as fallback ([#21](https://github.com/mindoffwork/django-mindoff/pull/21))

### Enhancements

- ✨ Simplify default API class and Test Class template ([#22](https://github.com/mindoffwork/django-mindoff/pull/22))

## v0.2.0

### Features

- ✨ Add API queue mode implementation with full test coverage
- ✨ Add queue-mode support in TDD kit

### Fixes

- 🐛 Fix CRUD kit and API test regressions

### Enhancements

- ⚡ Optimize test cases across kits for broader coverage
- ⚡ Improve management kit stability and create_app inference

### Documentation

- 📝 Update README formatting and structure

### Internal

- 👷 Update GitHub workflows and add `Root CI` ([#14](https://github.com/mindoffwork/django-mindoff/pull/14))
- 🐛 Fix token issue to write protected branch by actions ([#12](https://github.com/mindoffwork/django-mindoff/pull/12))
- 🔧 Update CI/CD workflows, PR title lint, and changelog automation
- ♻️ Remove deprecated manager features (organize init/py, build)

## v0.1.3

### Fixes

- 🐛 Fix test suite for release readiness

### Internal

- 🔧 Update CI workflow

## v0.1.2

### Features

- ✨ Add API versioning support and routing updates

### Fixes

- 🔒 Add CSRF-exempt defaults for generated URLs

### Internal

- 🚀 Release initial PyPI package (alpha)
- 📦 Update build/manifest handling and packaging metadata behavior
- 🔧 Add component `init.py` files across components
- 🔧 Refresh CI workflows

## Notes

- All notable changes to **Django Mindoff** are documented in [Changelog](https://github.com/mindoffwork/django-mindoff/blob/root/CHANGELOG.md).
- For Released versions, see [Releases](https://github.com/mindoffwork/django-mindoff/releases)
- Packaging and project metadata are available in `pyproject.toml`.
