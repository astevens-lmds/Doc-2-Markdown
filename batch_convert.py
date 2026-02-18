#!/usr/bin/env python3
"""
Batch Processing CLI for PDF-to-Markdown Converter.

Convert an entire directory of documents to markdown (or other formats).
Supports parallel processing, recursive scanning, and multiple output formats.

Usage:
    python batch_convert.py /path/to/pdfs
    python batch_convert.py /path/to/pdfs --output /path/to/output --format html
    python batch_convert.py /path/to/pdfs --recursive --workers 4
"""

import argparse
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

# Suppress tkinter for CLI usage
from unittest.mock import MagicMock
for mod in ['tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
            'tkinter.ttk', 'tkinter.scrolledtext', 'tkinter.simpledialog',
            'tkinterdnd2']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from pdf_to_markdown import DocumentConverter, ExportManager, _check_availability

# Supported input extensions
SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.msg', '.eml', '.epub'}


def find_convertible_files(directory: str, extensions: list = None, recursive: bool = False) -> list:
    """
    Find all convertible files in a directory.

    Args:
        directory: Path to scan.
        extensions: List of extensions to include (e.g., ['.pdf', '.docx']).
                   Defaults to all supported extensions.
        recursive: Whether to scan subdirectories.

    Returns:
        Sorted list of Path objects.
    """
    exts = set(extensions) if extensions else SUPPORTED_EXTENSIONS
    root = Path(directory)

    if not root.is_dir():
        return []

    if recursive:
        files = [f for f in root.rglob('*') if f.suffix.lower() in exts and f.is_file()]
    else:
        files = [f for f in root.iterdir() if f.suffix.lower() in exts and f.is_file()]

    return sorted(files)


def convert_file(input_path: str, output_dir: str = None, output_format: str = 'markdown',
                 use_ai: bool = False) -> dict:
    """
    Convert a single file and return result info.

    Args:
        input_path: Path to the input file.
        output_dir: Directory for output files. Defaults to same directory as input.
        output_format: One of 'markdown', 'html', 'txt'.
        use_ai: Whether to use AI enhancement.

    Returns:
        Dict with 'input', 'output', 'status', 'error', 'elapsed'.
    """
    start = time.time()
    input_path = Path(input_path)
    result = {
        'input': str(input_path),
        'output': None,
        'status': 'error',
        'error': None,
        'elapsed': 0,
    }

    try:
        converter = DocumentConverter()

        # Determine output path
        out_dir = Path(output_dir) if output_dir else input_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        ext_map = {'markdown': '.md', 'html': '.html', 'txt': '.txt'}
        out_ext = ext_map.get(output_format, '.md')
        out_path = out_dir / (input_path.stem + out_ext)

        # Convert based on input type
        suffix = input_path.suffix.lower()
        markdown_text = None

        if suffix == '.pdf':
            markdown_text = converter.convert_pdf_to_markdown(str(input_path))
        elif suffix == '.docx':
            markdown_text = converter.convert_docx_to_markdown(str(input_path))
        elif suffix == '.txt':
            markdown_text = converter.convert_txt_to_markdown(str(input_path))
        elif suffix == '.msg':
            markdown_text = converter.convert_msg_to_markdown(str(input_path))
        elif suffix == '.eml':
            markdown_text = converter.convert_eml_to_markdown(str(input_path))
        elif suffix == '.epub':
            markdown_text = converter.convert_epub_to_markdown(str(input_path))
        else:
            result['error'] = f"Unsupported format: {suffix}"
            return result

        if not markdown_text:
            result['error'] = "Conversion returned empty result"
            return result

        # Export to desired format
        if output_format == 'html':
            ExportManager.to_html(markdown_text, str(out_path))
        elif output_format == 'txt':
            ExportManager.to_txt(markdown_text, str(out_path))
        else:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(markdown_text)

        result['output'] = str(out_path)
        result['status'] = 'success'

    except Exception as e:
        result['error'] = str(e)

    result['elapsed'] = round(time.time() - start, 2)
    return result


def main():
    """CLI entry point for batch conversion."""
    parser = argparse.ArgumentParser(
        description='Batch convert documents to Markdown (or HTML/TXT).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python batch_convert.py ./documents
  python batch_convert.py ./documents --output ./converted --format html
  python batch_convert.py ./documents --recursive --workers 4
  python batch_convert.py ./documents --extensions .pdf .docx
        """
    )

    parser.add_argument('input_dir', help='Directory containing documents to convert')
    parser.add_argument('--output', '-o', help='Output directory (default: same as input)')
    parser.add_argument('--format', '-f', choices=['markdown', 'html', 'txt'],
                        default='markdown', help='Output format (default: markdown)')
    parser.add_argument('--recursive', '-r', action='store_true',
                        help='Scan subdirectories recursively')
    parser.add_argument('--workers', '-w', type=int, default=1,
                        help='Number of parallel workers (default: 1)')
    parser.add_argument('--extensions', '-e', nargs='+',
                        help='File extensions to process (default: all supported)')
    parser.add_argument('--dry-run', action='store_true',
                        help='List files that would be converted without converting')

    args = parser.parse_args()

    _check_availability()

    # Find files
    extensions = args.extensions if args.extensions else None
    files = find_convertible_files(args.input_dir, extensions=extensions, recursive=args.recursive)

    if not files:
        print(f"No convertible files found in: {args.input_dir}")
        return 0

    print(f"Found {len(files)} file(s) to convert")

    if args.dry_run:
        for f in files:
            print(f"  {f}")
        return 0

    # Convert
    results = []
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(convert_file, str(f), args.output, args.format): f
                for f in files
            }
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                status = '✓' if r['status'] == 'success' else '✗'
                print(f"  {status} {Path(r['input']).name} ({r['elapsed']}s)"
                      + (f" - {r['error']}" if r['error'] else ""))
    else:
        for f in files:
            r = convert_file(str(f), args.output, args.format)
            results.append(r)
            status = '✓' if r['status'] == 'success' else '✗'
            print(f"  {status} {f.name} ({r['elapsed']}s)"
                  + (f" - {r['error']}" if r['error'] else ""))

    # Summary
    success = sum(1 for r in results if r['status'] == 'success')
    failed = len(results) - success
    total_time = sum(r['elapsed'] for r in results)

    print(f"\nDone: {success} succeeded, {failed} failed, {total_time:.1f}s total")
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
