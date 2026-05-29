#!/usr/bin/env python3
import argparse
import json
import sys

from docling_runtime import (
    add_common_arguments,
    add_remote_ocr_arguments,
    build_docling_converter,
    convert_with_options,
    document_quality_report,
    render_ocr_fallback_output,
    resolve_runtime_options,
    run_ocrspace_fallback,
    run_cli,
    validate_remote_ocr_args,
    validate_local_source,
    validate_output_path,
    maybe_write_audit,
    write_text_output,
)


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--to', choices=['md','json','text','html'], default='md')
    add_common_arguments(p)
    add_remote_ocr_arguments(p)
    p.add_argument('--ocr', dest='legacy_ocr', action='store_true', default=None)
    p.add_argument('--no-ocr', dest='legacy_ocr', action='store_false')
    p.add_argument('--output')
    p.add_argument('--overwrite', action='store_true')
    args = p.parse_args()
    source = validate_local_source(args.source)
    if args.output:
        validate_output_path(args.output, overwrite=args.overwrite)
    if args.ocr_audit_output:
        validate_output_path(args.ocr_audit_output, overwrite=True)
    options = resolve_runtime_options(args)
    validate_remote_ocr_args(args)
    local_error = None
    local_quality = None
    result = None
    try:
        conv = build_docling_converter(options)
        result = convert_with_options(conv, source, options)
        local_quality = document_quality_report(result.document, args)
    except Exception as exc:
        if args.ocr_fallback == 'none':
            raise
        local_error = {'type': type(exc).__name__, 'message': str(exc)[:500]}

    used_fallback = False
    if args.ocr_fallback == 'ocrspace' and (local_error is not None or (local_quality and not local_quality['passes'])):
        fallback = run_ocrspace_fallback(source, options, args, local_quality=local_quality, local_error=local_error)
        text = render_ocr_fallback_output(fallback, args.to)
        used_fallback = True
        maybe_write_audit(args, {
            'schema_version': 'docling-convert-audit.v1',
            'source': source,
            'output_format': args.to,
            'fallback_used': True,
            'fallback': {
                key: value for key, value in fallback.items()
                if key != 'pages'
            },
            'page_text_lengths': [
                {'page': item['page'], 'characters': len(item.get('text', ''))}
                for item in fallback['pages']
            ],
        })
    else:
        if result is None:
            raise RuntimeError(local_error['message'] if local_error else 'Docling conversion failed')
        if args.to == 'json':
            text = json.dumps(result.document.export_to_dict(), ensure_ascii=False, indent=2)
        elif args.to == 'html':
            text = result.document.export_to_html()
        elif args.to == 'text':
            text = result.document.export_to_text()
        else:
            text = result.document.export_to_markdown()
        maybe_write_audit(args, {
            'schema_version': 'docling-convert-audit.v1',
            'source': source,
            'output_format': args.to,
            'fallback_used': used_fallback,
            'local_quality': local_quality,
        })
    if args.output:
        write_text_output(args.output, text, overwrite=args.overwrite)
    else:
        sys.stdout.write(text)


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
