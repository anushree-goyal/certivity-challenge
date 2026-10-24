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

## Cost and throughput controls (added for the held-out run)

| Variable | Effect |
| --- | --- |
| `CERTIVITY_MODEL_EXTRACT` | Model for `extract_obligations` only — ~95% of a full run's calls, so this is the main cost lever |
| `CERTIVITY_MODEL_APPLICABILITY` | Model for candidate screening and scope decisions |
| `CERTIVITY_MODEL_PROFILE` | Model for the product profile |
| `CERTIVITY_MODEL_REVIEW` | Model for the quality component's review and dedup tasks |
| `CERTIVITY_MODEL_REVISE` | Model for targeted revision |

An explicit `model_overrides` argument always beats the environment.

`RunConfig.max_parallel_sections` (default 4) bounds concurrent section
extraction. Serially the real corpus needs ~6.7 hours of model calls against a
5-hour submission window; four workers brings that to roughly 1.7 hours. The
ceiling is the provider's rate limit, not local CPU.

**Recommended setting for a full run**, which keeps the judgement-heavy stages
on the stronger model and moves only the high-volume one:

```bash
CERTIVITY_MODEL=claude-opus-5          # profile, applicability, review
CERTIVITY_MODEL_EXTRACT=claude-sonnet-5
```

Measured per-call usage on the real corpus is ~9.2k input / ~0.9k output
tokens, so a ~1,200-call run is roughly 11M input / 1.1M output tokens: about
$80 entirely on Opus 5, or about $25 with extraction on Sonnet 5.
