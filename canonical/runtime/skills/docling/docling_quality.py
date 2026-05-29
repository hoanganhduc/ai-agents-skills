#!/usr/bin/env python3
import argparse
import json

from docling_runtime import (
    add_common_arguments,
    add_quality_arguments,
    build_docling_converter,
    convert_with_options,
    document_quality_report,
    resolve_runtime_options,
    run_cli,
    validate_local_source,
)


def _run():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    add_common_arguments(p)
    add_quality_arguments(p)
    args = p.parse_args()
    source = validate_local_source(args.source)
    options = resolve_runtime_options(args)
    result = convert_with_options(build_docling_converter(options), source, options)
    report = document_quality_report(result.document, args)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    return run_cli(_run)


if __name__ == '__main__':
    raise SystemExit(main())
