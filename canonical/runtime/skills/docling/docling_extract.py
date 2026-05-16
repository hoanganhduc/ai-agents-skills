#!/usr/bin/env python3
import argparse, json
from docling.document_converter import DocumentConverter

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    args = p.parse_args()
    result = DocumentConverter().convert(args.source)
    doc = result.document
    data = {
        'pages': doc.num_pages(),
        'texts': len(getattr(doc, 'texts', [])),
        'tables': len(getattr(doc, 'tables', [])),
        'pictures': len(getattr(doc, 'pictures', [])),
    }
    headings = []
    for item, level in doc.iterate_items():
        label = getattr(item, 'label', None)
        text = getattr(item, 'text', None)
        if label and 'heading' in str(label).lower() and text:
            headings.append({'level': level, 'text': text[:300]})
    data['headings'] = headings[:50]
    print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
