"""Shared model-provider adapter (owner: Anushree).

One configurable structured-output and vision boundary for the whole
application. The agent component and Teammate 3's review both call it; neither
talks to a provider SDK directly, and the browser never calls a provider at all.
"""

from certivity.llm.adapter import (
    ProviderConfig,
    StructuredLLMAdapter,
    adapter_from_env,
    describe_configuration,
)
from certivity.llm.cache import CallCache, NullCache

create_llm = adapter_from_env
"""Canonical factory name for the composition layer.

Same callable as ``adapter_from_env``; both names are supported so the
application layer does not have to change its call site."""

__all__ = [
    "ProviderConfig",
    "StructuredLLMAdapter",
    "adapter_from_env",
    "create_llm",
    "describe_configuration",
    "CallCache",
    "NullCache",
]
