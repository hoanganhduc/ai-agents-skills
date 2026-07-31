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


# Docling labels the structural headings of a document `title` and
# `section_header`; no label contains the substring "heading" except the
# form-field label `field_heading`. `page_header` is running-head noise, not
# document structure, so it stays out.
HEADING_LABELS = {'title', 'section_header'}


def _heading_record(index: int, level, text: str, max_heading_chars: int) -> dict:
    kept = text[:max_heading_chars] if max_heading_chars else text
    return {
        'index': index,
        'level': level,
        'characters': len(text),
        'truncated': len(kept) < len(text),
        'text': kept,
    }


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument(
        '--headings-limit',
        type=int,
        default=0,
        help='Emit at most this many headings; 0 emits every heading.',
    )
    p.add_argument(
        '--max-heading-chars',
        type=int,
        default=0,
        help='Cut each heading to this many characters and mark it truncated; 0 keeps full text.',
    )
    add_common_arguments(p)
    add_json_output_arguments(p)
    args = p.parse_args()

    headings_limit = non_negative_int(args.headings_limit, 'headings-limit')
    max_heading_chars = non_negative_int(args.max_heading_chars, 'max-heading-chars')

    source = validate_local_source(args.source)
    prepare_json_output(args)
    options = resolve_runtime_options(args)
    result = convert_with_options(build_docling_converter(options), source, options)
    doc = result.document

    found = []
    for item, level in doc.iterate_items():
        label = getattr(item, 'label', None)
        text = getattr(item, 'text', None)
        if label and str(label).lower() in HEADING_LABELS and text:
            found.append((level, text))

    window = found[:headings_limit] if headings_limit else found
    headings = [
        _heading_record(i, level, text, max_heading_chars)
        for i, (level, text) in enumerate(window)
    ]

    emit_json_payload(args, {
        'schema_version': 'docling-extract.v2',
        'source': source,
        'complete': len(headings) == len(found) and not any(h['truncated'] for h in headings),
        'pages': doc.num_pages(),
        'texts': len(getattr(doc, 'texts', [])),
        'tables': len(getattr(doc, 'tables', [])),
        'pictures': len(getattr(doc, 'pictures', [])),
        'headings_total': len(found),
        'headings_emitted': len(headings),
        'headings_limit': headings_limit or None,
        'max_heading_chars': max_heading_chars or None,
        'truncated_headings': sum(1 for h in headings if h['truncated']),
        'headings': headings,
    })


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
