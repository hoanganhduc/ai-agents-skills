#!/usr/bin/env python3
import argparse, json
from docling.document_converter import DocumentConverter
from docling.chunking import HierarchicalChunker

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--mode', choices=['hierarchical'], default='hierarchical')
    args = p.parse_args()
    result = DocumentConverter().convert(args.source)
    chunker = HierarchicalChunker()
    chunks = list(chunker.chunk(result.document))
    out = []
    for c in chunks[:200]:
        out.append({
            'text': getattr(c, 'text', '')[:2000],
            'meta': getattr(c, 'meta', None).model_dump() if getattr(c, 'meta', None) else None,
        })
    print(json.dumps({'count': len(chunks), 'chunks': out}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
