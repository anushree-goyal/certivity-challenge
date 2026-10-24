# Contract proposal from the corpus component (for Anushree)

> **Settled.** Anushree answered every item and published contracts 1.3.0; the corpus now implements it (merged
> from `feature/agent`). Decisions: machine namespaces via `namespace_for` (`annex:6/part:6`, `annex:5/appendix`,
> `appendix:2`, `introduction`, `part:i`); label-keyed passage IDs `passage_id_for(doc, namespace, label, ordinal)`;
> `paragraph_label` in citation form, built by the corpus; printed pages as strings, read from the footer;
> `get_passages` omits unknown IDs; `read_scope` returns the whole Definitions clause; `table_context` = caption +
> headers + footnotes (interim rule); `visual_pdf_page_indices` kept; `get_document` and
> `list_reference_candidates` joined `CorpusReader`, `search` is the optional `CorpusSearch`. The text below is the
> original proposal, kept for the record.

`src/certivity/contracts.py` did not exist when the corpus was built. To avoid blocking, the corpus imports
`certivity.contracts` when present and otherwise uses
`src/certivity/corpus/_provisional_contracts.py`, whose field names follow the master-plan table. When you publish
the canonical module, the corpus switches to it automatically, and the provisional file should then be deleted. Below
is what the corpus produces, so the canonical definitions can match it (or tell me what to rename).

## Records as implemented

```python
Document(document_id, regulation, title, role, version_label, filename, checksum, snapshot_id, page_count)
SourcePassage(passage_id, document_id, section_id, text, paragraph_label: str | None, heading_ancestry: list[str],
              printed_start_page: int | None, page_span: list[int], pdf_page_indices: list[int], parsing_flags: list[str])
Section(section_id, document_id, parent_section_id: str | None, heading, namespace, paragraph_label: str | None,
        primary_passage_ids: list[str], parsing_flags: list[str])
SectionBundle(section, primary_passages, context_passages, visual_pdf_page_indices: list[int])
ReferenceRequest(originating_passage_id, raw_reference, target_regulation=None, target_annex=None, target_paragraph=None)
ReferenceResolution(status: "resolved"|"ambiguous"|"missing"|"outside_corpus", candidate_passage_ids, explanation)
PageAsset(document_id, pdf_page_index, printed_page: str | None, media_type, asset_ref)
```

## Decisions to confirm

1. **`Section.namespace` and `Section.paragraph_label`** (the master plan says "paragraph/annex namespace"). Namespace
   values are `main`, `Annex 4`, `Annex 6 Part 6`, `Annex 4 Appendix 1`, `Appendix 2`, `Introduction` and `Part I`.
2. **`SourcePassage.paragraph_label`** is the citation form (`6.10.4.2`, `Annex 6 Part 6 3`), so export can copy
   it straight to `source.paragraph`.
3. **`SourcePassage.printed_start_page`** is the page to cite. It is verified against the PDF footer on every page.
   `page_span` is printed pages; `pdf_page_indices` are zero-based physical pages.
4. **The passage kind is a flag (`kind:table`) rather than a field**, so the planned record keeps its shape.
   Context role is the same: `context:parent` / `context:definition` on bundle context passages. If you prefer
   explicit fields (`kind`, `context_role`), I will switch.
5. **`SectionBundle.visual_pdf_page_indices`** is the name used for "relevant table/image page references".
6. **`ReferenceRequest.target_annex`** carries a namespace string (`Annex 6 Part 2`), or the literal `this annex`.
   `target_regulation` takes `UN R10` style.
7. **`CorpusReader` Protocol additions** you may want to include:
   - `list_reference_candidates(passage_ids) -> list[ReferenceRequest]`
   - `search(query, document_id=None, limit=20) -> list[SourcePassage]`
   - `get_document(document_id) -> Document`
8. `list_documents(role="base")` also accepts `role=None` (all roles), which is useful later for the sub-goal.
