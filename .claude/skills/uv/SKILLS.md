---
name: uv
description: Guides use of uv (Astral's Python package manager) for tools, Docker, GitHub Actions, and pre-commit. Use when setting up Python projects, running CLI tools, configuring CI/CD, Docker builds, or pre-commit hooks with uv.
---

# uv — Python Package Manager

uv is a fast Python package and project manager by Astral. Use this skill when working with uv for tools, Docker, GitHub Actions, or pre-commit.

## Tools (`uvx` / `uv tool`)

**Run tools without installing** (preferred in most cases):
```bash
uvx <tool> [args]          # or: uv tool run <tool>
uvx ruff@0.6.0 --version   # specific version
uvx ruff@latest --version # refresh cache, use latest
uvx --isolated ruff       # ignore installed version
uvx -w <extra-package> <tool>   # add extra deps
```

**Install tools** (when another program needs them on PATH, e.g. Docker):
```bash
uv tool install ruff
uv tool install ruff@0.6.0
uv tool install --with <extra> <tool>
uv tool install --with-executables-from ansible-core,ansible-lint ansible
uv tool upgrade black
uv tool upgrade black --upgrade-package click
```

**Notes:**
- `uvx` = `uv tool run`; uses cached temp env; `uv cache clean` removes it.
- Installed tools live in uv tools dir; `uv tool dir --bin` shows executable path.
- Do not mutate tool envs manually (no `pip` in them).
- `uvx` uses latest on first run, then cached; use `@latest` to refresh.
- If tool installed, `uvx` uses installed version unless `@latest` or `--isolated`.
- For project tools (pytest, mypy), use `uv run` not `uv tool run`.

## Docker

**Install uv** (pin version or SHA256 for reproducibility):
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/
# or SHA256: COPY --from=ghcr.io/astral-sh/uv@sha256:<hash> /uv /uvx /bin/
```

**Install project:**
```dockerfile
COPY . /app
ENV UV_NO_DEV=1
WORKDIR /app
RUN uv sync --locked
CMD ["uv", "run", "my_app"]
```
Add `.venv` to `.dockerignore` — do not copy local venv.

**Use environment:**
```dockerfile
ENV PATH="/app/.venv/bin:$PATH"
# or: RUN uv run some_script.py
```

**Optimizations:**
- Cache: `RUN --mount=type=cache,target=/root/.cache/uv uv sync` with `ENV UV_LINK_MODE=copy`
- Intermediate layers: `uv sync --locked --no-install-project` first (deps only), then `COPY .` and `uv sync --locked`
- Bytecode: `uv sync --compile-bytecode` or `ENV UV_COMPILE_BYTECODE=1`
- Workspaces: use `--frozen --no-install-workspace` for first sync, then `--locked` after `COPY .`
- Non-editable: `uv sync --no-editable` for multi-stage builds (copy `.venv` only, not source)

**Pip interface** (requirements.txt, system Python):
```dockerfile
RUN uv pip install --system ruff
# or: ENV UV_SYSTEM_PYTHON=1
```

## GitHub Actions

**Install uv:**
```yaml
- uses: astral-sh/setup-uv@v7
  with:
    version: "0.10.8"   # pin version
```

**Python:**
```yaml
- run: uv python install   # respects .python-version
# or setup-python with python-version-file: ".python-version"
```

**Matrix (multiple Python versions):**
```yaml
strategy:
  matrix:
    python-version: ["3.10", "3.11", "3.12"]
steps:
  - uses: astral-sh/setup-uv@v7
    with:
      python-version: ${{ matrix.python-version }}
```

**Sync and run:**
```yaml
- run: uv sync --locked --all-extras --dev
- run: uv run pytest tests
```

**Caching:**
```yaml
- uses: astral-sh/setup-uv@v7
  with:
    enable-cache: true
```
Or manual: `actions/cache` with `path: /tmp/.uv-cache`, `key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}`, and `uv cache prune --ci` before save.

**Private repos:** Use PAT with `gh auth login --with-token` and `gh auth setup-git`.

**PyPI publish:** Use trusted publishing; workflow with `uv build`, smoke tests, `uv publish`. See [reference.md](reference.md).

## pre-commit

Repo: `https://github.com/astral-sh/uv-pre-commit`. Pin `rev` to uv version.

**Keep uv.lock up to date:**
```yaml
- repo: https://github.com/astral-sh/uv-pre-commit
  rev: 0.10.8
  hooks:
    - id: uv-lock
```

**Sync requirements.txt with uv.lock:**
```yaml
- id: uv-export
```

**Compile requirements:**
```yaml
- id: pip-compile
  args: [requirements.in, -o, requirements.txt]
```

## Additional Resources

- Full Docker images, Dockerfile examples, and PyPI workflow: [reference.md](reference.md)
- Official docs: https://docs.astral.sh/uv/