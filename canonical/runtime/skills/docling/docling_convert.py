#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

def build_converter(args):
    if args.pipeline == 'standard':
        opts = PdfPipelineOptions(do_ocr=args.ocr, do_table_structure=args.tables)
        return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return DocumentConverter()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--to', choices=['md','json','text','html'], default='md')
    p.add_argument('--pipeline', choices=['standard','auto'], default='standard')
    p.add_argument('--ocr', dest='ocr', action='store_true')
    p.add_argument('--no-ocr', dest='ocr', action='store_false')
    p.set_defaults(ocr=True)
    p.add_argument('--tables', dest='tables', action='store_true')
    p.add_argument('--no-tables', dest='tables', action='store_false')
    p.set_defaults(tables=True)
    p.add_argument('--output')
    args = p.parse_args()
    conv = build_converter(args)
    result = conv.convert(args.source)
    if args.to == 'json':
        text = json.dumps(result.document.export_to_dict(), ensure_ascii=False, indent=2)
    elif args.to == 'html':
        text = result.document.export_to_html()
    elif args.to == 'text':
        text = result.document.export_to_text()
    else:
        text = result.document.export_to_markdown()
    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)

if __name__ == '__main__':
    main()
