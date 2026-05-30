# Environment & Dependency Compatibility

## Supported Python version

- **Python 3.12 — Supported (recommended).** This is the target runtime for
  HealthAI. Use it for development and any deployment.

## Experimental Python version

- **Python 3.13 — Experimental.** The project has been developed and validated
  locally on 3.13.5 (it is what is installed on the current machine) and the
  full test suite passes. It is considered experimental because 3.12 is the
  declared support target; some third-party wheels historically lag a new
  CPython release. If you hit a dependency that lacks a 3.13 wheel, fall back to
  3.12.

> Earlier Python versions (≤ 3.11) are not supported. The code uses 3.10+
> syntax (PEP 604 `X | Y` unions, `list[...]` generics) and is only tested on
> 3.12 / 3.13.

## Creating the environment

```powershell
# Preferred: Python 3.12
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If only 3.13 is available, the same steps work with `py -3.13` (experimental).

## Dependency compatibility considerations

| Package    | Role                          | Compatibility notes |
|------------|-------------------------------|---------------------|
| `streamlit`| Dashboard UI                  | Pure-Python; works on 3.12/3.13. Pin a minor range before deploying to avoid surprise breaking changes. |
| `pymupdf`  | PDF text extraction           | Ships as a compiled wheel (`cp310-abi3`), so it installs on 3.12/3.13 from the same ABI3 wheel. A C extension — verify a wheel exists for any new Python before upgrading. |
| `pydantic` | Schema validation             | v2.x. Uses the compiled `pydantic-core` (Rust). Pinned to `>=2.7`; do not mix with v1 APIs. |
| `pytest`   | Test runner (dev)             | Pure-Python; broad version support. |
| `anthropic`| Claude SDK (**optional**)     | Only needed for real Claude. The app runs fully offline without it via the local/mock backends. Network + valid `ANTHROPIC_API_KEY` required at runtime to actually call Claude. |

### Key principles

1. **Compiled dependencies are the risk.** `pymupdf` and `pydantic-core` are
   the only non-pure-Python packages. When bumping Python, confirm wheels exist
   for the target interpreter/OS before upgrading.
2. **The `anthropic` SDK is optional and isolated.** It is imported only inside
   `app/services/anthropic_client.py`. Missing SDK or key degrades gracefully
   to the offline backend (see `app/services/factory.py`).
3. **Offline-first.** Tests and the default UI never require network or
   credentials; the deterministic local backend and the `MockClaudeClient`
   cover all automated testing.

## Configuration (environment variables)

| Variable               | Purpose                                              | Default            |
|------------------------|------------------------------------------------------|--------------------|
| `ANTHROPIC_API_KEY`    | Enables the real Claude backend.                     | unset (offline)    |
| `HEALTHAI_LLM_BACKEND` | Force a backend: `anthropic` or `local`.             | auto-detect        |
| `HEALTHAI_CLAUDE_MODEL`| Override the Claude model id.                        | `claude-opus-4-8`  |

Secrets are read from the environment only; no key is ever written to disk or
committed. Do not place real keys in `requirements.txt`, code, or docs.
