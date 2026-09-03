# Release Checklist

Before publishing Taurus Executor as an open-source project, please complete the following checklist:

## Pre-Release

### 1. Update Repository URLs
- [ ] Replace all `taurus-stack` placeholders with actual GitHub organization name
- [ ] Update URLs in:
  - `README.md`
  - `pyproject.toml`
  - `manage/pyproject.toml`
  - `CONTRIBUTING.md`
  - `SECURITY.md`
  - `CODE_OF_CONDUCT.md`
  - `CHANGELOG.md`
  - `.github/workflows/ci.yml`

### 2. Update Contact Information
- [ ] Replace `security@taurus-stack.com` in `SECURITY.md` with `taurus-stack@outlook.com`
- [ ] Replace `conduct@taurus-stack.com` in `CODE_OF_CONDUCT.md` with `taurus-stack@outlook.com`

### 3. Review Sensitive Information
- [ ] Check `.env` file for any real credentials
- [ ] Verify no private keys or certificates are committed
- [ ] Check for hardcoded IP addresses or internal hostnames
- [ ] Review `hosts.json` and other config files

### 4. Clean Up Test Files
- [ ] Remove or move internal test files to `.gitignore`:
  - `test_fork_solution.py`
  - `test_grpc_fork.py`
  - `verify_fix.py`
  - `verify_grpc_env.py`

### 5. Documentation
- [ ] Ensure `README.md` is complete and accurate
- [ ] Verify all code examples work
- [ ] Check that all links in documentation are correct
- [ ] Update `CHANGELOG.md` with release notes

### 6. Code Quality
- [ ] Run `make format` to format code
- [ ] Run `make lint` to check for issues
- [ ] Run `make test` to ensure all tests pass
- [ ] Review and fix any mypy type errors

### 7. Dependencies
- [ ] Review `pyproject.toml` dependencies
- [ ] Ensure `poetry.lock` is up to date
- [ ] Check for any security vulnerabilities in dependencies

### 8. Build and Package
- [ ] Run `make build-all` to generate gRPC code
- [ ] Test `poetry build` to ensure package builds
- [ ] Test Docker build: `docker build -t taurus-executor:latest .`

### 9. Version Bump
- [ ] Update version in `pyproject.toml`
- [ ] Update version in `manage/pyproject.toml`
- [ ] Update `CHANGELOG.md` with release date

### 10. Final Review
- [ ] Review all files in the repository
- [ ] Check for any remaining internal references
- [ ] Verify LICENSE file is correct
- [ ] Test installation from clean state

## Publishing

### GitHub
- [ ] Create GitHub repository
- [ ] Push code to main branch
- [ ] Create initial release with tag (e.g., `v0.1.0`)
- [ ] Enable GitHub Actions
- [ ] Configure branch protection rules

### PyPI (Optional)
- [ ] Create PyPI account
- [ ] Create API token
- [ ] Configure Poetry for PyPI
- [ ] Publish package: `poetry publish --build`

### Docker Hub (Optional)
- [ ] Create Docker Hub repository
- [ ] Configure GitHub Actions for Docker builds
- [ ] Push initial image

## Post-Release

- [ ] Announce release on relevant channels
- [ ] Monitor GitHub Issues for bug reports
- [ ] Respond to community questions
- [ ] Plan next release