# uv — Reference

Detailed examples and configurations from the official uv documentation.

---

## Tools — Details

**Execution vs installation:** Prefer `uvx` for one-off runs. Use `uv tool install` when another program (script, Docker) needs the tool on PATH.

**Tool versions:**
- `uvx ruff` — first run: latest; later: cached until `uv cache clean` or `@latest`
- `uvx ruff@0.6.0` — specific version
- `uvx ruff@latest` — refresh cache, use latest
- `uvx --isolated ruff` — ignore installed version, use cached/latest
- After `uv tool install ruff==0.5.0`, both `ruff` and `uvx ruff` use 0.5.0

**Upgrading:** `uv tool upgrade black` upgrades all; `--upgrade-package click` upgrades one. Respects install constraints (e.g. `black>=23,<24`). Use `--reinstall` to reinstall packages.

**Additional deps:** `uvx -w <extra> <tool>` or `uv tool install --with <extra> <tool>`. `--with-executables-from` adds packages and their executables (e.g. ansible + ansible-core + ansible-lint).

**Relationship to uv run:** `uv tool run <name>` ≈ `uv run --no-project --with <name> -- <name>`. Tools always run isolated from project. For pytest/mypy in project, use `uv run`.

---

## Docker — Images

**Distroless:**
- `ghcr.io/astral-sh/uv:latest`
- `ghcr.io/astral-sh/uv:0.10.8`
- `ghcr.io/astral-sh/uv:0.8` (latest patch)

**Derived (with OS):**
- Alpine: `ghcr.io/astral-sh/uv:alpine`, `ghcr.io/astral-sh/uv:alpine3.23`
- Debian: `ghcr.io/astral-sh/uv:debian-slim`, `ghcr.io/astral-sh/uv:trixie-slim`, `ghcr.io/astral-sh/uv:debian`
- Python: `ghcr.io/astral-sh/uv:python3.12-alpine`, `ghcr.io/astral-sh/uv:python3.12-trixie-slim`, etc.

Versioned: `ghcr.io/astral-sh/uv:0.10.8-alpine`

---

## Docker — Full Examples

**Install uv (copy binary):**
```dockerfile
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/
```

**Install uv (installer):**
```dockerfile
FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"
```

**Project install with intermediate layers:**
```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
```

**Workspace (intermediate layers):**
```dockerfile
RUN uv sync --frozen --no-install-workspace  # first (no workspace members yet)
COPY . /app
RUN uv sync --locked  # second
```

**Non-editable multi-stage (copy venv only):**
```dockerfile
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.12-slim
COPY --from=builder --chown=app:app /app/.venv /app/.venv
CMD ["/app/.venv/bin/hello"]
```

**Using installed tools:**
```dockerfile
ENV PATH=/root/.local/bin:$PATH
RUN uv tool install cowsay
# or: ENV UV_TOOL_BIN_DIR=/opt/uv-bin/
```

**Developing with bind mount:**
```bash
docker run --rm --volume .:/app --volume /app/.venv ...
```

**Docker Compose watch (exclude .venv):**
```yaml
develop:
  watch:
    - action: sync
      path: .
      target: /app
      ignore:
        - .venv/
    - action: rebuild
      path: ./pyproject.toml
```

---

## Docker — Caching & Bytecode

```dockerfile
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv uv sync
```

Python cache:
```dockerfile
ENV UV_PYTHON_CACHE_DIR=/root/.cache/uv/python
RUN --mount=type=cache,target=/root/.cache/uv uv python install
```

Bytecode:
```dockerfile
RUN uv python install --compile-bytecode
RUN uv sync --compile-bytecode
# or: ENV UV_COMPILE_BYTECODE=1
```

---

## GitHub Actions — PyPI Publish

```yaml
name: "Publish"
on:
  push:
    tags:
      - v*

jobs:
  run:
    runs-on: ubuntu-latest
    environment:
      name: pypi
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v7
      - run: uv python install 3.13
      - run: uv build
      - name: Smoke test (wheel)
        run: uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
      - name: Smoke test (sdist)
        run: uv run --isolated --no-project --with dist/*.tar.gz tests/smoke_test.py
      - run: uv publish
```

Configure trusted publisher in PyPI; create `pypi` environment in GitHub. Tag with `v*` (e.g. `v0.1.0`).

---

## GitHub Actions — Manual Cache

```yaml
jobs:
  install_job:
    env:
      UV_CACHE_DIR: /tmp/.uv-cache
    steps:
      - uses: actions/cache@v5
        with:
          path: /tmp/.uv-cache
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: |
            uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
            uv-${{ runner.os }}
      # ... install, run tests ...
      - run: uv cache prune --ci
```

For `uv pip`, use `requirements.txt` in cache key instead of `uv.lock`.

---

## Docker — Pip Install Project

```dockerfile
COPY pyproject.toml .
RUN uv pip install -r pyproject.toml
COPY . .
RUN uv pip install -e .
```

## pre-commit — Multiple pip-compile

```yaml
- repo: https://github.com/astral-sh/uv-pre-commit
  rev: 0.10.8
  hooks:
    - id: pip-compile
      name: pip-compile requirements.in
      args: [requirements.in, -o, requirements.txt]
    - id: pip-compile
      name: pip-compile requirements-dev.in
      args: [requirements-dev.in, -o, requirements-dev.txt]
      files: ^requirements-dev\.(in|txt)$
```

## Image Provenance

Verify official images with GitHub CLI:
```bash
gh attestation verify --owner astral-sh oci://ghcr.io/astral-sh/uv:latest
```

Or with cosign for attestation blob verification. Prefer version tag or digest over `latest`.