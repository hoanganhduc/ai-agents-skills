#!/usr/bin/env python3
import argparse

from docling_runtime import (
    add_common_arguments,
    add_json_output_arguments,
    build_docling_converter,
    convert_with_options,
    emit_json_payload,
    non_negative_int,
    prepare_json_output,
    resolve_runtime_options,
    run_cli,
    validate_local_source,
)


def _chunk_record(index: int, chunk, max_chunk_chars: int) -> dict:
    text = getattr(chunk, 'text', '') or ''
    kept = text[:max_chunk_chars] if max_chunk_chars else text
    meta = getattr(chunk, 'meta', None)
    return {
        'index': index,
        'characters': len(text),
        'characters_emitted': len(kept),
        'truncated': len(kept) < len(text),
        'text': kept,
        'meta': meta.model_dump() if meta else None,
    }


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument(
        '--mode',
        choices=['hierarchical'],
        default='hierarchical',
        help=(
            'Chunking strategy. Only hierarchical is offered: the Docling hybrid chunker '
            'needs a downloaded tokenizer, which the local-only runtime forbids.'
        ),
    )
    p.add_argument('--offset', type=int, default=0, help='Skip this many leading chunks.')
    p.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Emit at most this many chunks; 0 emits every chunk from --offset onward.',
    )
    p.add_argument(
        '--max-chunk-chars',
        type=int,
        default=0,
        help='Cut each chunk to this many characters and mark it truncated; 0 keeps full text.',
    )
    add_common_arguments(p)
    add_json_output_arguments(p)
    args = p.parse_args()

    offset = non_negative_int(args.offset, 'offset')
    limit = non_negative_int(args.limit, 'limit')
    max_chunk_chars = non_negative_int(args.max_chunk_chars, 'max-chunk-chars')

    source = validate_local_source(args.source)
    prepare_json_output(args)
    options = resolve_runtime_options(args)
    result = convert_with_options(build_docling_converter(options), source, options)
    from docling.chunking import HierarchicalChunker

    chunks = list(HierarchicalChunker().chunk(result.document))
    window = chunks[offset:offset + limit] if limit else chunks[offset:]
    records = [_chunk_record(offset + i, c, max_chunk_chars) for i, c in enumerate(window)]

    characters_total = sum(len(getattr(c, 'text', '') or '') for c in chunks)
    characters_emitted = sum(r['characters_emitted'] for r in records)
    consumed = offset + len(records)

    emit_json_payload(args, {
        'schema_version': 'docling-chunk.v2',
        'source': source,
        'mode': args.mode,
        'complete': characters_emitted == characters_total,
        'chunks_total': len(chunks),
        'chunks_emitted': len(records),
        'offset': offset,
        'limit': limit or None,
        'next_offset': consumed if consumed < len(chunks) else None,
        'characters_total': characters_total,
        'characters_emitted': characters_emitted,
        'max_chunk_chars': max_chunk_chars or None,
        'truncated_chunks': sum(1 for r in records if r['truncated']),
        'chunks': records,
    })


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
