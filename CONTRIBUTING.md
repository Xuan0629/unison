# Contributing to Unison

Thanks for your interest in contributing to Unison (万物一心)!

Unison is licensed under [Apache License 2.0](LICENSE). By contributing, you agree that your contributions will be licensed under the same terms.

## Ways to Contribute

- **Bug reports** — Open an issue with steps to reproduce
- **Feature requests** — Open an issue describing the use case
- **Code contributions** — Fork → branch → PR (see below)
- **Documentation** — Fix typos, improve README, translate
- **Test coverage** — Write tests for untested code paths
- **Platform testing** — Test on macOS / Windows WSL and report results

## Development Setup

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .
pytest tests/ -v    # Should pass
```

## Pull Request Process

1. Fork the repo and create a feature branch
2. Write or update tests for your changes
3. Run `pytest tests/ -v` — all tests must pass
4. Keep diffs minimal — no reformatting of unrelated code
5. Open a PR with a clear description of what and why

## Code Style

- Match existing patterns in the codebase
- Use `pathlib.Path` for all path operations
- Use `dataclass` for configuration objects
- Follow Python naming conventions (snake_case)

## Running with Unison Itself

Unison is self-hosting — you can use Unison pipelines to develop Unison:

```bash
# Write a PRD to prd/PRD.md, then:
unison run --pipeline my-unison-fix.yaml
```

See `docs/MANUAL.md` for pipeline configuration details.

## Questions?

Open a [GitHub Discussion](https://github.com/Xuan0629/unison/discussions) or an issue.
