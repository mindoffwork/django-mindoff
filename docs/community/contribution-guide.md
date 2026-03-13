# Contribution Guide

Thank you for your interest in contributing to django-mindoff.

Contributions help improve the framework and make it more useful for developers building Django REST APIs.

## Ways to Contribute

You can contribute in several ways:

- Fix bugs
- Add new features
- Improve documentation
- Improve test coverage
- Suggest improvements

## Before You Start

Before beginning work on a contribution:

1. Check existing Issues to see if the problem is already reported.
2. If you plan to add a feature, consider opening a discussion first.
3. Keep pull requests focused and small.

## Development Setup

### 1. Fork the Repository

Fork the repository on GitHub and clone your fork.

```bash
git clone https://github.com/<your-username>/django-mindoff.git
cd django-mindoff
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -e .
```

## Running Tests

Before submitting a pull request, ensure the test suite passes.

```bash
pytest
```

## Pull Request Guidelines

When submitting a pull request:

- Ensure tests pass
- Follow the existing project structure
- Add tests for new features
- Update documentation when needed
- Keep pull requests focused on a single change

### 1. Code Style

Follow the conventions used in the existing codebase. Consistency with the existing style is preferred.

### 2. Submitting a Pull Request

1. Create a feature branch
2. Commit your changes
3. Push the branch to your fork
4. Open a Pull Request on GitHub
5. Clearly explain the motivation and implementation details in the pull request description.

Clearly explain the motivation and implementation details in the pull request description.
