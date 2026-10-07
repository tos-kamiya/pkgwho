# Repository Guidelines

## Project Structure
- `src/pkgwho/`: Python package and CLI implementation.
- `tests/`: pytest tests (`test_*.py`).
- `handoff.md`: broader product goals and detector design.
- Root metadata and user documentation: `pyproject.toml`, `README.md`.

## Development
- Create the development environment with `uv venv --python 3.12`.
- Install the package and development tools with
  `uv pip install --python .venv/bin/python -e ".[dev]"`.
- Run the CLI with `pkgwho COMMAND`; use `pkgwho --help` for options.
- Check type hints with
  `uv run --python .venv/bin/python mypy src/pkgwho`.
- Run pytest with elevated permissions:
  `uv run --python .venv/bin/python pytest`.
- Python 3.10 or newer is supported. Use type hints and 4-space indentation.
- Use elevated permissions when a required development or system operation
  cannot be completed with normal permissions.

## Implementation Principles
- Query package managers with `subprocess` argument lists; do not use
  `shell=True`.
- Treat executable paths as supporting evidence and prefer manager databases.
- Show uninstall commands, but never uninstall packages automatically.
- Use semantic commit messages with a Conventional Commit type, such as
  `feat: add snap detection`, `fix: handle missing package tools`, or
  `docs: clarify package search`.
- Create commits with elevated permissions.
