#!/usr/bin/env python3
import argparse
import json
import sys

from docling_runtime import (
    add_common_arguments,
    build_docling_converter,
    convert_with_options,
    resolve_runtime_options,
    run_cli,
    validate_local_source,
    write_text_output,
)


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--to', choices=['md','json','text','html'], default='md')
    add_common_arguments(p)
    p.add_argument('--ocr', dest='legacy_ocr', action='store_true', default=None)
    p.add_argument('--no-ocr', dest='legacy_ocr', action='store_false')
    p.add_argument('--output')
    p.add_argument('--overwrite', action='store_true')
    args = p.parse_args()
    options = resolve_runtime_options(args)
    source = validate_local_source(args.source)
    conv = build_docling_converter(options)
    result = convert_with_options(conv, source, options)
    if args.to == 'json':
        text = json.dumps(result.document.export_to_dict(), ensure_ascii=False, indent=2)
    elif args.to == 'html':
        text = result.document.export_to_html()
    elif args.to == 'text':
        text = result.document.export_to_text()
    else:
        text = result.document.export_to_markdown()
    if args.output:
        write_text_output(args.output, text, overwrite=args.overwrite)
    else:
        sys.stdout.write(text)


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
