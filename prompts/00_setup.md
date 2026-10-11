# Prompt 00 — Set up the workspace

Paste everything below the line into Claude Code at the repo root.

---

Set up this repo's development environment and skeleton. Do not write any business logic yet.

First read `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md` and `docs/CONTRACTS.md`. These files, plus `pyproject.toml`, `.env.example` and `.gitignore`, already exist and are the source of truth. Do not rewrite them. If you see a real inconsistency between them, list it at the end instead of fixing it.

Do the following:

1. **Python.** Confirm Python 3.12+ is available (`python3.12 --version`, falling back to `python3 --version`). Create `.venv`, activate it and run `pip install -e ".[dev]"`. If any dependency fails to install, fix the version pin in `pyproject.toml` minimally and tell me what changed.

2. **Env file.** If `.env` does not exist, copy `.env.example` to `.env`. Leave the keys empty, because I'll add them later. Never print `.env` contents.

3. **Dev container.** Create `.devcontainer/devcontainer.json` for GitHub Codespaces:
   - Python 3.12 image (`mcr.microsoft.com/devcontainers/python:3.12`).
   - Node LTS feature, for the frontend later.
   - `postCreateCommand`: `python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && cp -n .env.example .env || true`.
   - Forward port 8000.
   - VS Code extensions: ms-python.python, charliermarsh.ruff.

4. **Package skeleton.** Create exactly this tree with empty modules. Each module's only content is a one-line docstring saying what it will contain, taken from ARCHITECTURE §2.
   ```
   foreman/__init__.py
   foreman/config.py
   foreman/contracts.py
   foreman/dev.py
   foreman/core/__init__.py  questions.py  jev.py  llm.py  decide.py  ledger.py  state.py  context.py  concurrency.py
   foreman/tools/__init__.py  base.py
   foreman/testing/__init__.py  fakes.py
   foreman/graph/__init__.py
   foreman/api/__init__.py
   foreman/extract_files/__init__.py
   foreman/format/__init__.py
   tests/__init__.py  tests/core/__init__.py  tests/tools/__init__.py
   tests/test_smoke.py      # imports foreman and asserts True
   data/samples/.gitkeep
   runs/.gitkeep            # runs/ is gitignored; keep the folder locally only
   ```
   Do not create files for the individual tools. Each tool is built by its own prompt later.

5. **Check it works.** Run `pytest -q` and `ruff check .`. Both must pass.

6. **Git.**
   - If this is not a git repo yet, run `git init` with `main` as the default branch.
   - Commit everything with the message `chore: project skeleton`.
   - Confirm `.env` is NOT tracked.

7. **Finish with a short report:**
   - The Python version.
   - Whether the install succeeded.
   - The test result.
   - Any inconsistencies you noticed in the docs.
   - Next step: add `AI_GATEWAY_API_KEY` and `FIREWORKS_API_KEY` to `.env`, then run prompt 01.
