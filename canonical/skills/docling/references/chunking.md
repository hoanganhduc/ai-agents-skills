# Docling chunking

Default Codex recommendation:

- use hierarchical chunking first for lightweight structure-aware chunking
- switch to hybrid/token-aware chunking only when downstream embedding/token constraints justify it

Chunking should preserve:
- heading context
- page provenance when possible
- table/figure boundaries when relevant

## Why hybrid chunking is not offered

`--mode` accepts `hierarchical` only. Docling's `HybridChunker` builds a
Hugging Face tokenizer on construction, and the runtime sets `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, and `HF_DATASETS_OFFLINE=1` before any conversion, so
the default constructor raises `OSError` on a machine with no cached tokenizer.
Supporting hybrid means supplying a local tokenizer explicitly; note that
`HybridChunker.max_tokens` is a read-only property, so a token budget has to be
set on the tokenizer rather than passed to the chunker.

## Completeness

Chunk output is complete by default and says so in the payload: `complete`,
`chunks_total` vs `chunks_emitted`, `characters_total` vs `characters_emitted`,
and `next_offset`. A narrowed result must remain recoverable — `--offset` and
`--limit` page through the document, and oversized stdout is an error rather
than a silent cut. See `context-discipline.md` on disclosing elision.
