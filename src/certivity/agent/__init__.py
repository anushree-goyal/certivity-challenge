"""Extraction and reasoning component (owner: Anushree).

Public boundary, called by Teammate 4's composition layer:

    run_extraction(run_input, corpus, llm, emit_event) -> PipelineDraft
    revise_extraction(run_input, draft, findings, corpus, llm, emit_event) -> PipelineDraft

This component decides product facts, scope, which passages to read, and which
references to follow. It does not build the submission, implement SQL, serve
HTTP or render UI, and it never reads the golden answer.
"""

from certivity.agent.pipeline import revise_extraction, run_extraction

__all__ = ["run_extraction", "revise_extraction"]
