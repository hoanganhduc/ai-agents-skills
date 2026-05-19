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
    add_common_arguments(p)
    args = p.parse_args()
    options = resolve_runtime_options(args)
    source = validate_local_source(args.source)
    result = convert_with_options(build_docling_converter(options), source, options)
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


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
