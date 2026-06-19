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
| `google-genai` | Gemini SDK (**optional**) | Only needed for real Gemini. Defaults to Vertex AI + Application Default Credentials when Gemini is selected; AI Studio API keys remain an explicit fallback. |

### Key principles

1. **Compiled dependencies are the risk.** `pymupdf` and `pydantic-core` are
   the only non-pure-Python packages. When bumping Python, confirm wheels exist
   for the target interpreter/OS before upgrading.
2. **Hosted LLM SDKs are optional and isolated.** `anthropic` is imported only
   inside `app/services/anthropic_client.py`; `google-genai` is imported only
   inside `app/services/gemini_client.py`. Missing SDKs or keys degrade
   gracefully to the offline backend during auto-detection (see
   `app/services/factory.py`).
3. **Offline-first.** Tests and the default UI never require network or
   credentials; the deterministic local backend and the `MockClaudeClient`
   cover all automated testing.

## Configuration (environment variables)

| Variable               | Purpose                                              | Default            |
|------------------------|------------------------------------------------------|--------------------|
| `ANTHROPIC_API_KEY`    | Enables the real Claude backend.                     | unset (offline)    |
| `GOOGLE_GENAI_USE_VERTEXAI` | Routes modern `google-genai` calls to Vertex AI. | set by `GeminiClient` when Gemini is selected |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI project for Gemini. | `skilled-loader-468413-j6` when Gemini is selected |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI location for Gemini. | `global` |
| `HEALTHAI_GEMINI_USE_VERTEXAI` | Set `false` only to force AI Studio API-key mode. | `true` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | AI Studio key fallback when Vertex mode is disabled. | unset |
| `HEALTHAI_LLM_BACKEND` | Force a backend: `anthropic`, `gemini`, or `local`.  | auto-detect        |
| `HEALTHAI_CLAUDE_MODEL`| Override the Claude model id.                        | `claude-opus-4-8`  |
| `HEALTHAI_GEMINI_MODEL`| Override the Gemini model id.                        | `gemini-3.5-flash` |
| `HEALTHAI_GEMINI_THINKING_BUDGET` | Thinking-token budget for Gemini models. Set `0` for deterministic structured JSON responses. | `0` |

### Gemini on Vertex AI with ADC

```powershell
gcloud auth application-default login
gcloud config set project skilled-loader-468413-j6

$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\jakes\Downloads\skilled-loader-468413-j6-50ec35997585.json"
$env:GOOGLE_CLOUD_PROJECT = "skilled-loader-468413-j6"
$env:GOOGLE_CLOUD_LOCATION = "global"
$env:HEALTHAI_LLM_BACKEND = "gemini"
$env:HEALTHAI_STRUCTURED_EXTRACTION_BACKEND = "gemini"
$env:HEALTHAI_CLINICAL_REASONING_BACKEND = "gemini"
$env:HEALTHAI_APPEAL_DRAFTING_BACKEND = "gemini"
$env:HEALTHAI_GEMINI_MODEL = "gemini-3.5-flash"

streamlit run app\main.py
```

No Gemini API key is required in Vertex mode. Authentication comes from local
Application Default Credentials. API keys, if used for other providers, are read
from the environment only; no key is ever written to disk or committed.

For service-account authentication, point `GOOGLE_APPLICATION_CREDENTIALS` at
the downloaded JSON key file stored outside the repo.
