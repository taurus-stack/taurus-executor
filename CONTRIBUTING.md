# Contributing to Taurus Executor

Thank you for your interest in contributing to Taurus Executor! This document provides guidelines and instructions for contributing.

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct. Please be respectful and constructive in all interactions.

## Getting Started

### Development Environment

1. **Fork and clone** the repository
2. **Install dependencies**:
   ```bash
   poetry install --with dev
   ```
3. **Generate gRPC code**:
   ```bash
   make build-all
   ```
4. **Run tests** to ensure everything works:
   ```bash
   make test
   ```

### Development Workflow

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes

3. **Format your code**:
   ```bash
   make format
   ```

4. **Run linters**:
   ```bash
   make lint
   ```

5. **Run tests**:
   ```bash
   make test
   ```

6. Commit your changes with clear, descriptive commit messages

7. Push to your fork and submit a Pull Request

## Pull Request Guidelines

### Before Submitting

- [ ] Code follows the project's style guidelines
- [ ] Self-review completed
- [ ] Code is commented where necessary
- [ ] Tests added for new functionality
- [ ] All tests pass (`make test`)
- [ ] Linters pass (`make lint`)
- [ ] Documentation updated if necessary

### PR Description

Please include:
- **What** changes you made
- **Why** you made these changes
- **How** to test the changes
- Any **breaking changes** or migration notes

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) format:

```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

Examples:
```
feat(executor): add support for interactive sessions
fix(sdk): resolve connection timeout issue
docs(readme): update installation instructions
test(handlers): add unit tests for command execution
```

## Code Style

We use:
- **Black** for code formatting
- **isort** for import sorting
- **flake8** for linting
- **mypy** for type checking

Run `make format` and `make lint` to ensure compliance.

## Testing

### Running Tests

```bash
# All tests
make test

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific test file
pytest tests/unit/test_executor.py

# With verbose output
pytest -v

# With coverage
pytest --cov=executor_core
```

### Writing Tests

- Write tests for all new functionality
- Unit tests should be fast and isolated
- Integration tests should test real gRPC communication
- Use fixtures from `conftest.py` for common setup

## Architecture Overview

Taurus Executor consists of several key components:

- **executor_core**: Main server implementation
  - `executors/`: Command execution engines
  - `services/`: gRPC services and handlers
  - `infra/`: Infrastructure (config, TLS, logging, state)
- **manage**: Client SDK and CLI tools
  - `sdk/`: Python client library
  - `cli.py`: Command-line interface
- **proto**: Protocol buffer definitions

## Reporting Issues

When reporting bugs, please include:

- **Taurus Executor version**
- **Python version**
- **Operating system**
- **Steps to reproduce**
- **Expected behavior**
- **Actual behavior**
- **Logs** (if applicable)

## Feature Requests

We welcome feature requests! Please:

1. Check existing issues to avoid duplicates
2. Describe the use case clearly
3. Explain why this feature would be valuable
4. Suggest implementation approach (optional)

## Documentation

Good documentation is crucial. When contributing:

- Update relevant docs in `docs/`
- Add docstrings to new functions/classes
- Update README if user-facing changes
- Include examples for new features

## Release Process

Releases are managed by maintainers. The process:

1. Version bump in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create release tag
4. Build and publish to PyPI
5. Create GitHub release with binaries

## Questions?

- **General questions**: Open a [Discussion](https://github.com/taurus-stack/taurus-executor/discussions)
- **Bug reports**: Open an [Issue](https://github.com/taurus-stack/taurus-executor/issues)
- **Code review**: Submit a [Pull Request](https://github.com/taurus-stack/taurus-executor/pulls)

Thank you for contributing! 🎉