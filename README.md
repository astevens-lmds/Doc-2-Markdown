# PDF-MD Converter

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A desktop application for converting PDF documents to Markdown format, optimized for legal professionals and document management.

## Features

- **AI-Enhanced Conversion**: Uses LLMs (OpenRouter, OpenAI, Anthropic, Google) to improve markdown quality
- **RAG Support**: Built-in vector database for semantic search across your converted documents
- **Multiple Input Formats**: PDF, DOCX, TXT, MSG, EML, EPUB, MOBI
- **Multiple Output Formats**: Markdown, HTML, DOCX
- **OCR Support**: Extract text from scanned PDFs using Tesseract
- **Table Detection**: Advanced table extraction with pdfplumber and tabula-py
- **Batch Processing**: Convert multiple files with parallel workers
- **Drag & Drop**: Simple file selection interface
- **Cost Tracking**: Monitor API usage and stay within budget

## Installation

### Prerequisites

- Python 3.10 or higher
- (Optional) Java JDK 11+ for Tika/Tabula enhanced extraction
- (Optional) Tesseract OCR for scanned document support

### Setup

1. Clone this repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/pdf-md-converter.git
   cd pdf-md-converter
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv

   # Windows
   .venv\Scripts\activate

   # macOS/Linux
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy the example config and add your API key:
   ```bash
   cp config.example.json config.json
   ```

   Edit `config.json` and add your OpenRouter API key (get one free at [openrouter.ai](https://openrouter.ai))

5. Run the application:
   ```bash
   python pdf_to_markdown.py
   ```

### Optional: Create Desktop Shortcut (Windows)

Run PowerShell as Administrator and execute:
```powershell
.\create_shortcut.ps1
```

## Configuration

Edit `config.json` to customize:

| Setting | Description |
|---------|-------------|
| `monthly_budget` | Maximum monthly spend on API calls |
| `default_model` | LLM model for AI enhancement |
| `api_keys.openrouter` | Your OpenRouter API key |
| `use_ocr` | Enable Tesseract OCR for scanned PDFs |
| `use_table_detection` | Enable advanced table extraction |
| `parallel_workers` | Number of parallel conversion threads |
| `java_path` | Path to java.exe (for Tika/Tabula) |

### RAG Settings

The `rag_settings` section configures the built-in vector database:

| Setting | Description |
|---------|-------------|
| `enabled` | Enable RAG functionality |
| `auto_vectorize` | Automatically index after conversion |
| `embedding_provider` | Embedding service (openai, local) |
| `embedding_model` | Model for generating embeddings |
| `vector_db_path` | Location for vector database storage |

## Legal Document Optimization

The default prompt is optimized for legal documents:

- Preserves page numbers as `[[PAGE_START: X]]` anchors for citation
- Maintains footnote references and content
- Removes running headers while keeping structural headers
- Proper hierarchy with nested markdown headers
- Blockquotes for case extracts and quoted material

## Supported LLM Models

### OpenRouter (Recommended)
- Google Gemini 2.5 Flash (fast, cost-effective)
- Anthropic Claude Sonnet 4.5 (balanced)
- OpenAI GPT-4o (versatile)
- DeepSeek R1 (reasoning)

### Direct API Access
- OpenAI models (requires OpenAI API key)
- Anthropic Claude (requires Anthropic API key)
- Google Gemini (requires Google API key)

## Dependencies

Core requirements:
- PyMuPDF - PDF parsing
- pdfplumber - Table extraction
- tkinter - GUI framework
- tiktoken - Token counting

Optional:
- pytesseract - OCR support
- python-docx - DOCX export
- chromadb - Vector database
- sentence-transformers - Local embeddings

See `requirements.txt` for full list.

## Usage Examples

### GUI Mode (Default)

```bash
python pdf_to_markdown.py
```

This launches the desktop GUI where you can drag & drop files or browse to select them.

### Batch Processing (CLI)

```bash
python batch_convert.py --input ./pdfs --output ./markdown --workers 4
```

Convert an entire directory of documents in parallel.

### Supported Input Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Native text + OCR for scanned |
| Word | `.docx` | Preserves headings and tables |
| Plain Text | `.txt` | Direct conversion |
| Email | `.msg`, `.eml` | Extracts body + attachments |
| eBook | `.epub`, `.mobi` | Chapter-aware conversion |

### Output Sample

Given a legal PDF, the converter produces clean Markdown:

```markdown
[[PAGE_START: 1]]

# Motion for Summary Judgment

## I. Statement of Facts

The plaintiff filed the original complaint on **January 15, 2024**...

> "The duty of care requires that the defendant exercise reasonable
> diligence in maintaining the property." *Smith v. Jones*, 123 F.3d 456 (2024)

## II. Legal Standard

Summary judgment is appropriate when there is no genuine dispute
as to any material fact.[^1]

[^1]: Fed. R. Civ. P. 56(a).

[[PAGE_START: 2]]
```

### Docker Usage

```bash
docker build -t pdf-md-converter .
docker run -v $(pwd)/input:/input -v $(pwd)/output:/output pdf-md-converter
```

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions welcome! Please open an issue or submit a pull request.

## Acknowledgments

Built with assistance from Claude (Anthropic) for code optimization and legal document processing patterns.
