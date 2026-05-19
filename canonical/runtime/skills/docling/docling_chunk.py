#!/usr/bin/env python3
import argparse, json

from docling_runtime import (
    add_common_arguments,
    build_docling_converter,
    convert_with_options,
    resolve_runtime_options,
    run_cli,
    validate_local_source,
)


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--mode', choices=['hierarchical'], default='hierarchical')
    add_common_arguments(p)
    args = p.parse_args()
    options = resolve_runtime_options(args)
    source = validate_local_source(args.source)
    result = convert_with_options(build_docling_converter(options), source, options)
    from docling.chunking import HierarchicalChunker

    chunker = HierarchicalChunker()
    chunks = list(chunker.chunk(result.document))
    out = []
    for c in chunks[:200]:
        out.append({
            'text': getattr(c, 'text', '')[:2000],
            'meta': getattr(c, 'meta', None).model_dump() if getattr(c, 'meta', None) else None,
        })
    print(json.dumps({'count': len(chunks), 'chunks': out}, ensure_ascii=False, indent=2))


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
