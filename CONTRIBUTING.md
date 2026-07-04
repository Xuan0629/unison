# Contributing to Unison

Thank you for your interest in contributing to Unison (万物一心).

## License

Unison is licensed under the [Business Source License 1.1](LICENSE). By contributing to this project, you agree that your contributions will be licensed under the same terms.

## Contributor License Agreement (CLA)

Before we can accept your contribution, you must sign a Contributor License Agreement (CLA). The CLA clarifies that:

1. You own the copyright to your contribution
2. You grant the project maintainer a perpetual, worldwide, non-exclusive license to use your contribution under the project's license terms
3. You are not violating any third-party rights or obligations

To sign the CLA, open a pull request and reply to the CLA bot with:
```
I have read the CLA and agree to its terms.
```

Alternatively, for larger contributions, a formal CLA document can be provided upon request.

## How to Contribute

### Reporting Bugs
- Open an issue with: steps to reproduce, expected vs actual behavior, Unison version, Python version, OS
- Include relevant `state.json` or log snippets (redact API keys)

### Suggesting Features
- Open an issue with the `enhancement` label
- Describe the use case and expected behavior

### Pull Requests
1. Fork the repository
2. Create a feature branch
3. Make your changes (follow existing code style)
4. Add tests for new functionality
5. Run `pytest tests/` and ensure all pass
6. Open a PR with a clear description

### Code Style
- Follow PEP 8
- Use type hints
- Match existing module patterns (see `src/unison/` for examples)
- Docstrings for public functions

## Development Setup

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .
PYTHONPATH=$PWD:$PWD/src pytest tests/
```

## Communication
Open a GitHub issue for questions or reach out via Discord.
