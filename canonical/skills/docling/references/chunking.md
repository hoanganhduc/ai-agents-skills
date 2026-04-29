# Docling chunking

Default Codex recommendation:

- use hierarchical chunking first for lightweight structure-aware chunking
- switch to hybrid/token-aware chunking only when downstream embedding/token constraints justify it

Chunking should preserve:
- heading context
- page provenance when possible
- table/figure boundaries when relevant
