# Contributing to Doc-2-Markdown

Thank you for your interest in contributing to Doc-2-Markdown — a PDF-to-Markdown converter with AI enhancement, OCR, and batch processing.

## Getting Started

1. **Fork** the repository and clone your fork
2. **Install dependencies:** `pip install -r requirements.txt`
3. **Run tests:** `pytest tests/ -v`

## Project Structure

```
├── pdf_to_markdown.py     # Main converter (GUI + core classes)
├── batch_convert.py       # CLI batch processing tool
├── rag_module.py          # RAG (Retrieval-Augmented Generation) module
├── config.example.json    # Configuration template
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container build
└── tests/
    └── test_conversion.py # Test suite
```

## How to Contribute

### Reporting Bugs

- Use GitHub Issues with the **bug** label
- Include: Python version, OS, steps to reproduce, error output
- Attach sample files if possible (no confidential documents)

### Suggesting Features

- Open an issue with the **enhancement** label
- Describe your use case and proposed solution

### Submitting Pull Requests

1. Create a feature branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes with clear, descriptive commits
3. Add or update tests in `tests/`
4. Ensure all tests pass: `pytest tests/ -v`
5. Open a PR against `main` with a clear description

## Code Style

- Follow PEP 8 for Python code
- Use type hints where practical
- Keep functions focused and well-documented with docstrings
- Use `pathlib.Path` over `os.path` where possible

## Testing

- Write tests using `pytest`
- Mock external dependencies (tkinter, API clients, file I/O)
- Cover edge cases: empty files, corrupt files, large files, unsupported formats
- Run: `pytest tests/ -v`

## Adding Format Support

To add a new input format:

1. Add a lazy import function in `pdf_to_markdown.py`
2. Implement conversion logic in `DocumentConverter`
3. Add the extension to `SUPPORTED_EXTENSIONS` in `batch_convert.py`
4. Write tests for the new format
5. Update `requirements.txt` if new dependencies are needed

## Security

- Never commit API keys or credentials
- Use `config.example.json` as a template — actual configs are gitignored
- Be cautious with file path handling to prevent traversal attacks

## Questions?

Open an issue or reach out to the maintainers. We're happy to help!
