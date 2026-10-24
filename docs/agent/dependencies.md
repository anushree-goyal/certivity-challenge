# Dependency and configuration request → Teammate 4

Teammate 4 owns root dependency files, `.env.example` and startup
configuration. This is the agent component's request; nothing here has been
added to root config by this owner.

## Runtime dependencies

| Package | Needed by | Why |
| --- | --- | --- |
| `anthropic` | `src/certivity/llm/adapter.py` | The official SDK for the structured-output and vision calls. Imported lazily, so the rest of the component imports without it. |

That is the whole list. `contracts.py`, `agent/` and the fixtures are standard
library only, so three of the four components can import the contracts with no
dependency at all.

Verified against `anthropic` 2.3.0.

## Development dependencies

| Package | Needed by |
| --- | --- |
| `pytest` | `tests/agent/` |
| `anthropic` | `tests/agent/test_llm_adapter.py` only (injects a fake client; makes no paid call) |

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Provider credential. `ANTHROPIC_AUTH_TOKEN` or an `ant auth login` profile also work; the adapter reports which source it found without reading its value. |
| `CERTIVITY_MODEL` | `claude-opus-5` | Model id. |
| `CERTIVITY_MODEL_EFFORT` | `high` | `low` \| `medium` \| `high` \| `xhigh` \| `max`. |
| `CERTIVITY_MAX_OUTPUT_TOKENS` | `16000` | Per-call output cap. |
| `CERTIVITY_DISABLE_REFUSAL_FALLBACKS` | unset | Set to any value to disable server-side refusal fallbacks. |
| `ANTHROPIC_BASE_URL` | unset | Alternative endpoint, if the team is issued one. |

**No credential is configured in this environment as checked.** Until one is,
`run_extraction` raises `LLMConfigurationError` on its first call — by design,
so the application cannot present output produced without a model.

## Composition

```python
from certivity.llm import create_llm          # alias of adapter_from_env

llm = create_llm(
    cache_dir=run_dir / "agent" / "llm-cache",  # omit for no caching
    max_calls=run_input.config.max_model_calls,
    model_overrides=run_input.config.model_overrides,
)
draft = run_extraction(run_input, corpus, llm, emit_event,
                       artifacts_dir=run_dir / "agent")
```

`artifacts_dir` is optional and, per the plan, the agent writes only inside the
`agent/` subfolder it is given: `product_profile.json`, `applicability.json`,
`pipeline_draft.json` (and `pipeline_draft_revised.json` after a revision). The
saved draft carries the input hash, snapshot id, prompt versions and model
configuration, so a run can be reproduced and a cached result attributed.

`adapter.describe()` returns a secret-free dict for `GET /api/health`:

```json
{"provider": "anthropic", "model": "claude-opus-5", "effort": "high",
 "credentials_configured": false, "credential_source": null,
 "refusal_fallbacks": true, "cache": "NullCache", "calls_used": 0}
```
