"""SYNTHETIC shared contract fixtures (owner: Anushree).

Nothing in this package is real data or a real model result. Every record is
invented, and the records carry ``is_synthetic_fixture`` / ``is_fixture_data``
so no caller can display one as an extraction result.

What each part is for:

* ``InMemoryCorpusReader`` - a complete ``CorpusReader`` so the agent, quality
  and application components can run before PDF ingestion exists.
* ``ScriptedLLMAdapter`` - a complete ``LLMAdapter`` so tests are deterministic,
  offline and free. It validates its scripted answers against the caller's
  schema, so a drifted fixture fails loudly.
* ``records`` - one valid example of every shared record, for Teammate 3's
  review/export work and Teammate 4's API and UI.
"""

from tests.fixtures.contracts import corpus, records
from tests.fixtures.contracts.corpus import (
    DOCUMENTS,
    PASSAGES,
    SECTIONS,
    SNAPSHOT_ID,
    InMemoryCorpusReader,
)
from tests.fixtures.contracts.llm import (
    FIXTURE_MODEL,
    RecordedCall,
    ScriptedLLMAdapter,
    default_script,
)
from tests.fixtures.contracts.records import (
    PIPELINE_DRAFT,
    QUALITY_REPORT,
    RUN_INPUT,
    RUN_VIEW,
)

__all__ = [
    "corpus",
    "records",
    "DOCUMENTS",
    "SECTIONS",
    "PASSAGES",
    "SNAPSHOT_ID",
    "InMemoryCorpusReader",
    "ScriptedLLMAdapter",
    "RecordedCall",
    "default_script",
    "FIXTURE_MODEL",
    "RUN_INPUT",
    "PIPELINE_DRAFT",
    "QUALITY_REPORT",
    "RUN_VIEW",
]
