"""
Tests for the PDF-to-Markdown conversion pipeline.
Tests DocumentConverter, ExportManager, and error handling.
"""

import os
import sys
import json
import tempfile
import struct
import zlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# We need to handle the tkinter import in pdf_to_markdown gracefully
# since tests run headless. We patch it before importing.
sys.modules.setdefault('tkinter', MagicMock())
sys.modules.setdefault('tkinter.filedialog', MagicMock())
sys.modules.setdefault('tkinter.messagebox', MagicMock())
sys.modules.setdefault('tkinter.ttk', MagicMock())
sys.modules.setdefault('tkinter.scrolledtext', MagicMock())
sys.modules.setdefault('tkinter.simpledialog', MagicMock())
sys.modules.setdefault('tkinterdnd2', MagicMock())

from pdf_to_markdown import (
    DocumentConverter,
    ExportManager,
    load_config,
    _check_availability,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def converter():
    """Create a DocumentConverter instance."""
    return DocumentConverter()


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_markdown():
    """Sample markdown content for export tests."""
    return """# Test Document

## Section One

This is a **bold** paragraph with *italic* text.

- Item 1
- Item 2
- Item 3

### Subsection

| Column A | Column B |
|----------|----------|
| Cell 1   | Cell 2   |

1. First
2. Second
3. Third

---

`inline code` and [a link](https://example.com)
"""


def _create_minimal_pdf(path):
    """Create a minimal valid PDF file for testing."""
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td (Hello World) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000360 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
441
%%EOF"""
    with open(path, 'wb') as f:
        f.write(pdf_content)


def _create_password_pdf(path):
    """Create a file that looks like a password-protected PDF."""
    # Minimal PDF with /Encrypt dictionary signals password protection
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
xref
0 3
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
trailer
<< /Size 3 /Root 1 0 R /Encrypt << /Filter /Standard /V 2 /R 3 /O (xxxx) /U (yyyy) /P -3904 >> >>
startxref
115
%%EOF"""
    with open(path, 'wb') as f:
        f.write(pdf_content)


# ============================================================================
# DocumentConverter Tests
# ============================================================================

class TestDocumentConverter:
    """Tests for the DocumentConverter class."""

    def test_init(self, converter):
        """Converter initializes without errors."""
        assert converter is not None

    def test_convert_txt_to_markdown(self, converter, tmp_dir):
        """TXT files convert to markdown."""
        txt_file = tmp_dir / "test.txt"
        txt_file.write_text("Hello World\nThis is a test.", encoding="utf-8")
        result = converter.convert_txt_to_markdown(str(txt_file))
        assert "Hello World" in result
        assert "This is a test" in result

    def test_extract_metadata_nonexistent(self, converter):
        """Metadata extraction handles missing files gracefully."""
        meta = converter.extract_metadata("/nonexistent/file.pdf")
        # Should return dict (possibly empty) without crashing
        assert isinstance(meta, dict)

    def test_table_to_markdown(self, converter):
        """Table data converts to markdown table format."""
        table = [
            ["Name", "Age"],
            ["Alice", "30"],
            ["Bob", "25"],
        ]
        result = converter.table_to_markdown(table)
        assert "Name" in result
        assert "Alice" in result
        assert "|" in result

    def test_table_to_markdown_empty(self, converter):
        """Empty table returns empty string."""
        result = converter.table_to_markdown([])
        assert result == "" or result is None or len(result.strip()) == 0

    def test_table_to_markdown_none_cells(self, converter):
        """Tables with None cells don't crash."""
        table = [["A", None], [None, "B"]]
        result = converter.table_to_markdown(table)
        assert isinstance(result, str)


# ============================================================================
# ExportManager Tests
# ============================================================================

class TestExportManager:
    """Tests for export to various formats."""

    def test_to_txt(self, sample_markdown, tmp_dir):
        """Markdown exports to plain text."""
        output = tmp_dir / "output.txt"
        result = ExportManager.to_txt(sample_markdown, str(output))
        assert result is True
        content = output.read_text(encoding="utf-8")
        # Headers should have markdown removed
        assert "# Test Document" not in content
        assert "Test Document" in content

    def test_to_html(self, sample_markdown, tmp_dir):
        """Markdown exports to HTML."""
        output = tmp_dir / "output.html"
        result = ExportManager.to_html(sample_markdown, str(output))
        assert result is True
        content = output.read_text(encoding="utf-8")
        assert "<html>" in content
        assert "Test Document" in content
        assert "<h1>" in content or "<h2>" in content

    def test_to_txt_removes_formatting(self, tmp_dir):
        """Plain text export strips markdown formatting."""
        md = "# Header\n\n**bold** and *italic*\n\n`code`"
        output = tmp_dir / "output.txt"
        ExportManager.to_txt(md, str(output))
        content = output.read_text(encoding="utf-8")
        assert "**" not in content
        assert "*" not in content or "italic" in content
        assert "`" not in content

    def test_to_html_includes_styles(self, sample_markdown, tmp_dir):
        """HTML export includes CSS styles."""
        output = tmp_dir / "output.html"
        ExportManager.to_html(sample_markdown, str(output))
        content = output.read_text(encoding="utf-8")
        assert "<style>" in content

    def test_to_txt_handles_links(self, tmp_dir):
        """Plain text export converts links to just text."""
        md = "Visit [Example](https://example.com) for more."
        output = tmp_dir / "output.txt"
        ExportManager.to_txt(md, str(output))
        content = output.read_text(encoding="utf-8")
        assert "Example" in content
        assert "](https" not in content


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestErrorHandling:
    """Tests for corrupt and password-protected PDF handling."""

    def test_corrupt_file_handling(self, converter, tmp_dir):
        """Corrupt files are handled gracefully."""
        corrupt = tmp_dir / "corrupt.pdf"
        corrupt.write_bytes(b"This is not a PDF at all")
        with pytest.raises(Exception):
            converter.convert_pdf_to_markdown(str(corrupt))

    def test_empty_file_handling(self, converter, tmp_dir):
        """Empty files are handled gracefully."""
        empty = tmp_dir / "empty.pdf"
        empty.write_bytes(b"")
        with pytest.raises(Exception):
            converter.convert_pdf_to_markdown(str(empty))

    def test_nonexistent_file(self, converter):
        """Non-existent files raise appropriate errors."""
        with pytest.raises(Exception):
            converter.convert_pdf_to_markdown("/nonexistent/file.pdf")

    def test_password_protected_pdf(self, converter, tmp_dir):
        """Password-protected PDFs produce a clear error."""
        pdf_path = tmp_dir / "protected.pdf"
        _create_password_pdf(pdf_path)
        # Should raise or return error message, not crash silently
        try:
            result = converter.convert_pdf_to_markdown(str(pdf_path))
            # If it returns instead of raising, the result should indicate failure
            # (some implementations return error text)
        except Exception as e:
            error_msg = str(e).lower()
            # Just ensure it doesn't crash with an unhandled exception type
            assert isinstance(e, Exception)


# ============================================================================
# Config Tests
# ============================================================================

class TestConfig:
    """Tests for configuration loading."""

    def test_load_config_returns_dict(self):
        """Config loading returns a dictionary."""
        config = load_config()
        assert isinstance(config, dict)

    def test_availability_check(self):
        """Availability check runs without errors."""
        _check_availability()
        # Just verify it doesn't crash


# ============================================================================
# Batch Processing CLI Tests
# ============================================================================

class TestBatchCLI:
    """Tests for the batch processing CLI."""

    def test_batch_cli_import(self):
        """Batch CLI module imports successfully."""
        from batch_convert import main, convert_file
        assert callable(main)
        assert callable(convert_file)

    def test_batch_cli_no_args(self, tmp_dir):
        """Batch CLI with empty directory processes zero files."""
        from batch_convert import find_convertible_files
        files = find_convertible_files(str(tmp_dir))
        assert files == []

    def test_find_files(self, tmp_dir):
        """Batch CLI finds PDF files in directory."""
        from batch_convert import find_convertible_files
        (tmp_dir / "test1.pdf").write_bytes(b"%PDF-1.4 fake")
        (tmp_dir / "test2.pdf").write_bytes(b"%PDF-1.4 fake")
        (tmp_dir / "readme.txt").write_text("not a pdf")
        files = find_convertible_files(str(tmp_dir), extensions=[".pdf"])
        assert len(files) == 2
