"""
RAG (Retrieval-Augmented Generation) Module for PDF-MD
======================================================

This module provides legal document-optimized RAG capabilities including:
- Hierarchical chunking with citation preservation
- OpenAI embeddings integration
- ChromaDB vector storage with metadata filtering
- Hybrid search (dense + BM25 sparse)

Author: PDF-MD Project
Version: 1.1.0
"""

import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Union
from dataclasses import dataclass, field, asdict

# Try to import numpy (needed for SimpleVectorStore)
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

# Optional dependency flags
HAS_CHROMADB = False
HAS_OPENAI = False
HAS_BM25 = False
HAS_TIKTOKEN = False

try:
    import chromadb
    # Detect API version: 0.4+ has PersistentClient, 0.3.x uses Client with Settings
    try:
        from chromadb import PersistentClient
        from chromadb.config import Settings
        CHROMADB_NEW_API = True
    except ImportError:
        # Old API (0.3.x) - PersistentClient doesn't exist
        CHROMADB_NEW_API = False
    HAS_CHROMADB = True
except ImportError:
    CHROMADB_NEW_API = False
    pass

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    pass

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    pass

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    pass

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class ChunkMetadata:
    """Metadata for a single chunk."""
    doc_id: str
    chunk_index: int
    source_file: str
    doc_type: str = "default"
    section_hierarchy: List[str] = field(default_factory=list)
    page_range: str = ""
    jurisdiction: str = ""
    date: str = ""
    citation_ref: str = ""
    token_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        """Convert to dictionary for ChromaDB metadata."""
        d = asdict(self)
        # Convert list to JSON string for ChromaDB compatibility
        d['section_hierarchy'] = json.dumps(d['section_hierarchy'])
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> 'ChunkMetadata':
        """Create from dictionary."""
        if isinstance(d.get('section_hierarchy'), str):
            d['section_hierarchy'] = json.loads(d['section_hierarchy'])
        return cls(**d)


@dataclass
class Chunk:
    """A document chunk with text and metadata."""
    text: str
    metadata: ChunkMetadata
    embedding: Optional[List[float]] = None


@dataclass
class SearchResult:
    """A search result with score and chunk."""
    chunk_text: str
    metadata: Dict
    score: float
    source: str = "dense"  # "dense" or "sparse"


# ============================================================================
# RAGChunker - Legal Document Hierarchical Chunking
# ============================================================================

class RAGChunker:
    """
    Hierarchical chunker optimized for legal documents.

    Features:
    - Splits by document structure (headers)
    - Preserves legal citations (never splits mid-citation)
    - Configurable chunk sizes by document type
    - Overlap between chunks for context continuity
    """

    # Chunk size configurations by document type (in tokens)
    DOC_TYPE_CONFIGS = {
        'statute': {'min_tokens': 200, 'max_tokens': 400, 'overlap_pct': 0.15},
        'case_law': {'min_tokens': 400, 'max_tokens': 800, 'overlap_pct': 0.12},
        'treatise': {'min_tokens': 300, 'max_tokens': 600, 'overlap_pct': 0.10},
        'rule': {'min_tokens': 200, 'max_tokens': 400, 'overlap_pct': 0.15},
        'contract': {'min_tokens': 300, 'max_tokens': 500, 'overlap_pct': 0.12},
        'pleading': {'min_tokens': 300, 'max_tokens': 500, 'overlap_pct': 0.12},
        'default': {'min_tokens': 300, 'max_tokens': 600, 'overlap_pct': 0.12}
    }

    # Legal citation patterns to preserve (never split)
    CITATION_PATTERNS = [
        # Federal citations
        r'\d+\s+U\.S\.C?\.\s*§?\s*\d+[a-z]*(?:\([a-z0-9]+\))*',  # USC
        r'\d+\s+F\.(?:\s*2d|\s*3d|\s*4th)?\s*\d+',  # Federal Reporter
        r'\d+\s+S\.\s*Ct\.\s*\d+',  # Supreme Court Reporter
        r'\d+\s+L\.\s*Ed\.\s*(?:2d\s*)?\d+',  # Lawyers Edition
        # State citations (Michigan examples)
        r'\d+\s+Mich\.?\s*(?:App\.?)?\s*\d+',  # Michigan Reports
        r'\d+\s+N\.W\.(?:\s*2d)?\s*\d+',  # North Western Reporter
        r'MCL\s*\d+\.\d+[a-z]*',  # Michigan Compiled Laws
        r'MCR\s*\d+\.\d+',  # Michigan Court Rules
        # Case name patterns
        r'[A-Z][a-z]+\s+v\.?\s+[A-Z][a-z]+,?\s+\d+',  # Case names
        # Pinpoint citations
        r'at\s+\d+[-–]\d+',  # Page ranges
        r'¶+\s*\d+',  # Paragraph citations
    ]

    # Header patterns for structure detection
    HEADER_PATTERNS = [
        (r'^#{1}\s+(.+)$', 1),   # # Header
        (r'^#{2}\s+(.+)$', 2),   # ## Header
        (r'^#{3}\s+(.+)$', 3),   # ### Header
        (r'^#{4}\s+(.+)$', 4),   # #### Header
        (r'^([IVXLCDM]+)\.\s+(.+)$', 2),  # Roman numeral sections
        (r'^([A-Z])\.\s+(.+)$', 3),  # Letter sections
        (r'^(\d+)\.\s+(.+)$', 3),  # Numbered sections
    ]

    def __init__(self):
        """Initialize the chunker."""
        self.tokenizer = None
        if HAS_TIKTOKEN:
            try:
                self.tokenizer = tiktoken.encoding_for_model("gpt-4")
            except Exception:
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        # Fallback: estimate ~4 chars per token
        return len(text) // 4

    def chunk_by_structure(
        self,
        markdown_text: str,
        doc_type: str = 'default',
        source_metadata: Optional[Dict] = None
    ) -> List[Chunk]:
        """
        Split markdown text into chunks based on document structure.

        Args:
            markdown_text: The markdown text to chunk
            doc_type: Document type for chunk size configuration
            source_metadata: Optional metadata about the source document

        Returns:
            List of Chunk objects with text and metadata
        """
        config = self.DOC_TYPE_CONFIGS.get(doc_type, self.DOC_TYPE_CONFIGS['default'])
        max_tokens = config['max_tokens']
        overlap_pct = config['overlap_pct']

        # Parse structure
        sections = self._parse_sections(markdown_text)

        # Generate doc_id from content hash
        doc_id = source_metadata.get('doc_id') if source_metadata else None
        if not doc_id:
            doc_id = hashlib.sha256(markdown_text[:1000].encode()).hexdigest()[:16]

        source_file = source_metadata.get('source_file', 'unknown') if source_metadata else 'unknown'

        # Build chunks from sections
        chunks = []
        current_text = ""
        current_hierarchy = []
        chunk_index = 0

        for section in sections:
            section_text = section['text']
            section_level = section['level']
            section_title = section['title']

            # Update hierarchy
            if section_level > 0:
                # Trim hierarchy to current level
                current_hierarchy = current_hierarchy[:section_level-1]
                current_hierarchy.append(section_title)

            # Check if adding this section exceeds max tokens
            combined_tokens = self.count_tokens(current_text + section_text)

            if combined_tokens > max_tokens and current_text:
                # Save current chunk
                chunk = self._create_chunk(
                    text=current_text.strip(),
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    source_file=source_file,
                    doc_type=doc_type,
                    section_hierarchy=list(current_hierarchy),
                    source_metadata=source_metadata
                )
                chunks.append(chunk)
                chunk_index += 1

                # Start new chunk with overlap
                overlap_text = self._get_overlap_text(current_text, overlap_pct)
                current_text = overlap_text + section_text
            else:
                current_text += section_text

            # If single section exceeds max, split it further
            while self.count_tokens(current_text) > max_tokens:
                # Find safe split point
                split_point = self._find_safe_split(current_text, max_tokens)

                chunk = self._create_chunk(
                    text=current_text[:split_point].strip(),
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    source_file=source_file,
                    doc_type=doc_type,
                    section_hierarchy=list(current_hierarchy),
                    source_metadata=source_metadata
                )
                chunks.append(chunk)
                chunk_index += 1

                # Continue with remainder plus overlap
                overlap_text = self._get_overlap_text(current_text[:split_point], overlap_pct)
                current_text = overlap_text + current_text[split_point:]

        # Don't forget the last chunk
        if current_text.strip():
            chunk = self._create_chunk(
                text=current_text.strip(),
                doc_id=doc_id,
                chunk_index=chunk_index,
                source_file=source_file,
                doc_type=doc_type,
                section_hierarchy=list(current_hierarchy),
                source_metadata=source_metadata
            )
            chunks.append(chunk)

        return chunks

    def _parse_sections(self, text: str) -> List[Dict]:
        """Parse markdown into sections based on headers."""
        sections = []
        lines = text.split('\n')
        current_section = {'text': '', 'level': 0, 'title': ''}

        for line in lines:
            # Check for header
            header_match = None
            header_level = 0
            header_title = ''

            for pattern, level in self.HEADER_PATTERNS:
                match = re.match(pattern, line, re.MULTILINE)
                if match:
                    header_match = match
                    header_level = level
                    header_title = match.group(1) if level <= 1 else match.group(0)
                    break

            if header_match and current_section['text']:
                # Save previous section
                sections.append(current_section)
                current_section = {
                    'text': line + '\n',
                    'level': header_level,
                    'title': header_title.strip()
                }
            else:
                current_section['text'] += line + '\n'

        # Add final section
        if current_section['text']:
            sections.append(current_section)

        return sections

    def _find_safe_split(self, text: str, max_tokens: int) -> int:
        """Find a safe split point that doesn't break citations."""
        # Estimate character position for max_tokens
        target_chars = max_tokens * 4  # Approximate

        if len(text) <= target_chars:
            return len(text)

        # Look for safe split points (paragraph breaks, sentence ends)
        search_start = max(0, target_chars - 500)
        search_end = min(len(text), target_chars + 200)
        search_region = text[search_start:search_end]

        # Priority: paragraph break > sentence end > word break
        # Look for paragraph break
        para_match = re.search(r'\n\n', search_region)
        if para_match:
            return search_start + para_match.end()

        # Look for sentence end (but not in citations)
        sentence_match = re.search(r'(?<![A-Z])\.\s+(?=[A-Z])', search_region)
        if sentence_match:
            candidate = search_start + sentence_match.end()
            # Verify we're not splitting a citation
            if not self._is_in_citation(text, candidate):
                return candidate

        # Fall back to word break
        word_match = re.search(r'\s+', search_region[::-1])
        if word_match:
            return search_end - word_match.start()

        return target_chars

    def _is_in_citation(self, text: str, position: int) -> bool:
        """Check if position is within a legal citation."""
        # Check surrounding context
        context_start = max(0, position - 100)
        context_end = min(len(text), position + 100)
        context = text[context_start:context_end]

        for pattern in self.CITATION_PATTERNS:
            matches = list(re.finditer(pattern, context))
            for match in matches:
                abs_start = context_start + match.start()
                abs_end = context_start + match.end()
                if abs_start <= position <= abs_end:
                    return True

        return False

    def _get_overlap_text(self, text: str, overlap_pct: float) -> str:
        """Get overlap text from end of chunk."""
        token_count = self.count_tokens(text)
        overlap_tokens = int(token_count * overlap_pct)

        # Work backwards to find overlap point
        words = text.split()
        if not words:
            return ""

        # Estimate words for overlap (roughly 1.3 tokens per word)
        overlap_words = max(1, int(overlap_tokens / 1.3))
        overlap_words = min(overlap_words, len(words) - 1)

        return ' '.join(words[-overlap_words:]) + '\n'

    def _create_chunk(
        self,
        text: str,
        doc_id: str,
        chunk_index: int,
        source_file: str,
        doc_type: str,
        section_hierarchy: List[str],
        source_metadata: Optional[Dict]
    ) -> Chunk:
        """Create a Chunk object with metadata."""
        # Extract page range from [[PAGE_START: N]] markers
        page_range = self._extract_page_range(text)

        # Get additional metadata from source
        jurisdiction = ""
        date = ""
        citation_ref = ""

        if source_metadata:
            jurisdiction = source_metadata.get('jurisdiction', '')
            date = source_metadata.get('date', '')
            citation_ref = source_metadata.get('citation_ref', '')

        metadata = ChunkMetadata(
            doc_id=doc_id,
            chunk_index=chunk_index,
            source_file=source_file,
            doc_type=doc_type,
            section_hierarchy=section_hierarchy,
            page_range=page_range,
            jurisdiction=jurisdiction,
            date=date,
            citation_ref=citation_ref,
            token_count=self.count_tokens(text)
        )

        return Chunk(text=text, metadata=metadata)

    def _extract_page_range(self, text: str) -> str:
        """Extract page range from [[PAGE_START: N]] markers."""
        matches = re.findall(r'\[\[PAGE_START:\s*(\d+)\]\]', text)
        if not matches:
            return ""

        pages = [int(p) for p in matches]
        if len(pages) == 1:
            return str(pages[0])
        return f"{min(pages)}-{max(pages)}"

    # ========================================================================
    # ADVANCED CHUNKING STRATEGIES (2025 Best Practices)
    # ========================================================================

    def chunk_semantic(
        self,
        markdown_text: str,
        embedding_client: 'EmbeddingClient',
        doc_type: str = 'default',
        source_metadata: Optional[Dict] = None,
        similarity_threshold: float = 0.75
    ) -> List[Chunk]:
        """
        Semantic chunking - splits text based on embedding similarity.

        This method analyzes semantic similarity between consecutive sentences
        and creates chunk boundaries where topics shift (low similarity).

        Based on 2025 research showing 70% accuracy improvement with semantic chunking.
        Optimal settings: 256-512 tokens, 10-20% overlap.

        Args:
            markdown_text: The markdown text to chunk
            embedding_client: Embedding client for similarity calculation
            doc_type: Document type for configuration
            source_metadata: Optional source metadata
            similarity_threshold: Similarity below this creates a new chunk (0.0-1.0)

        Returns:
            List of semantically coherent chunks
        """
        config = self.DOC_TYPE_CONFIGS.get(doc_type, self.DOC_TYPE_CONFIGS['default'])
        max_tokens = config['max_tokens']
        overlap_pct = config['overlap_pct']

        # Split into sentences
        sentences = self._split_into_sentences(markdown_text)
        if len(sentences) < 2:
            return self.chunk_by_structure(markdown_text, doc_type, source_metadata)

        # Get embeddings for all sentences (batch for efficiency)
        try:
            sentence_embeddings = embedding_client.embed_batch([s['text'] for s in sentences])
        except Exception as e:
            logger.warning(f"Semantic chunking failed ({e}), falling back to structure-based")
            return self.chunk_by_structure(markdown_text, doc_type, source_metadata)

        # Calculate cosine similarities between consecutive sentences
        similarities = []
        for i in range(len(sentence_embeddings) - 1):
            sim = self._cosine_similarity(sentence_embeddings[i], sentence_embeddings[i + 1])
            similarities.append(sim)

        # Find chunk boundaries (where similarity drops below threshold)
        boundaries = [0]
        for i, sim in enumerate(similarities):
            if sim < similarity_threshold:
                boundaries.append(i + 1)
        boundaries.append(len(sentences))

        # Build chunks from boundaries
        doc_id = source_metadata.get('doc_id') if source_metadata else None
        if not doc_id:
            doc_id = hashlib.sha256(markdown_text[:1000].encode()).hexdigest()[:16]
        source_file = source_metadata.get('source_file', 'unknown') if source_metadata else 'unknown'

        chunks = []
        chunk_index = 0

        for i in range(len(boundaries) - 1):
            start_idx = boundaries[i]
            end_idx = boundaries[i + 1]

            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(s['text'] for s in chunk_sentences)

            # Split further if chunk is too large
            if self.count_tokens(chunk_text) > max_tokens:
                sub_chunks = self._split_large_semantic_chunk(
                    chunk_sentences, max_tokens, overlap_pct,
                    doc_id, chunk_index, source_file, doc_type, source_metadata
                )
                chunks.extend(sub_chunks)
                chunk_index += len(sub_chunks)
            else:
                chunk = self._create_chunk(
                    text=chunk_text.strip(),
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    source_file=source_file,
                    doc_type=doc_type,
                    section_hierarchy=[],
                    source_metadata=source_metadata
                )
                chunks.append(chunk)
                chunk_index += 1

        return chunks

    def chunk_contextual(
        self,
        markdown_text: str,
        doc_type: str = 'default',
        source_metadata: Optional[Dict] = None,
        context_summary: Optional[str] = None
    ) -> List[Chunk]:
        """
        Contextual chunking - adds document context prefix to each chunk.

        This technique prepends a brief context summary to each chunk, helping
        the retriever understand chunk meaning even without surrounding context.

        Based on 2025 research showing 2-18% improvement in retrieval quality.

        Args:
            markdown_text: The markdown text to chunk
            doc_type: Document type for configuration
            source_metadata: Optional source metadata
            context_summary: Optional custom context (auto-generated if None)

        Returns:
            List of chunks with context prefixes
        """
        # First, do structure-based chunking
        base_chunks = self.chunk_by_structure(markdown_text, doc_type, source_metadata)

        # Generate context summary if not provided
        if not context_summary:
            context_summary = self._generate_context_summary(markdown_text, source_metadata)

        # Add context prefix to each chunk
        contextual_chunks = []
        for chunk in base_chunks:
            # Build context prefix
            context_parts = [f"[DOCUMENT CONTEXT: {context_summary}]"]

            if chunk.metadata.section_hierarchy:
                section_path = " > ".join(chunk.metadata.section_hierarchy)
                context_parts.append(f"[SECTION: {section_path}]")

            if chunk.metadata.page_range:
                context_parts.append(f"[PAGES: {chunk.metadata.page_range}]")

            context_prefix = " ".join(context_parts) + "\n\n"

            # Create new chunk with context
            contextual_chunk = Chunk(
                text=context_prefix + chunk.text,
                metadata=chunk.metadata
            )
            contextual_chunks.append(contextual_chunk)

        return contextual_chunks

    def chunk_advanced(
        self,
        markdown_text: str,
        strategy: str = 'hybrid',
        embedding_client: Optional['EmbeddingClient'] = None,
        doc_type: str = 'default',
        source_metadata: Optional[Dict] = None
    ) -> Tuple[List[Chunk], Dict]:
        """
        Advanced chunking with strategy selection and content retention verification.

        Strategies:
        - 'structure': Structure-based (fast, good for well-formatted docs)
        - 'semantic': Embedding-based similarity (best for unstructured content)
        - 'contextual': Structure + context prefixes (best for cross-references)
        - 'hybrid': Semantic + contextual (highest quality, slower)

        Args:
            markdown_text: The markdown text to chunk
            strategy: Chunking strategy to use
            embedding_client: Required for semantic/hybrid strategies
            doc_type: Document type for configuration
            source_metadata: Optional source metadata

        Returns:
            Tuple of (chunks, stats_dict with retention info)
        """
        original_char_count = len(markdown_text)
        original_word_count = len(markdown_text.split())

        # Select strategy
        if strategy == 'semantic' and embedding_client:
            chunks = self.chunk_semantic(
                markdown_text, embedding_client, doc_type, source_metadata
            )
        elif strategy == 'contextual':
            chunks = self.chunk_contextual(
                markdown_text, doc_type, source_metadata
            )
        elif strategy == 'hybrid' and embedding_client:
            # Semantic chunking with contextual enhancement
            semantic_chunks = self.chunk_semantic(
                markdown_text, embedding_client, doc_type, source_metadata
            )
            # Generate context summary
            context_summary = self._generate_context_summary(markdown_text, source_metadata)
            # Add context to semantic chunks
            chunks = []
            for chunk in semantic_chunks:
                context_prefix = f"[CONTEXT: {context_summary}]\n\n"
                contextual_chunk = Chunk(
                    text=context_prefix + chunk.text,
                    metadata=chunk.metadata
                )
                chunks.append(contextual_chunk)
        else:
            chunks = self.chunk_by_structure(
                markdown_text, doc_type, source_metadata
            )

        # Verify content retention
        retention_stats = self._verify_content_retention(
            markdown_text, chunks, original_char_count, original_word_count
        )

        return chunks, retention_stats

    def _split_into_sentences(self, text: str) -> List[Dict]:
        """Split text into sentences, preserving citations."""
        # Simple sentence splitting that respects citations
        sentences = []

        # Split on sentence boundaries but not citation periods
        pattern = r'(?<=[.!?])\s+(?=[A-Z])'
        parts = re.split(pattern, text)

        for part in parts:
            part = part.strip()
            if part:
                sentences.append({'text': part, 'start': text.find(part)})

        return sentences

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if not HAS_NUMPY:
            # Fallback without numpy
            dot = sum(a * b for a, b in zip(vec1, vec2))
            norm1 = sum(a * a for a in vec1) ** 0.5
            norm2 = sum(b * b for b in vec2) ** 0.5
            return dot / (norm1 * norm2 + 1e-10)

        import numpy as np
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        return float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2) + 1e-10))

    def _split_large_semantic_chunk(
        self, sentences: List[Dict], max_tokens: int, overlap_pct: float,
        doc_id: str, start_index: int, source_file: str, doc_type: str, source_metadata: Optional[Dict]
    ) -> List[Chunk]:
        """Split a large semantic chunk into smaller pieces."""
        chunks = []
        current_text = ""
        chunk_index = start_index

        for sentence in sentences:
            test_text = current_text + " " + sentence['text'] if current_text else sentence['text']

            if self.count_tokens(test_text) > max_tokens and current_text:
                chunk = self._create_chunk(
                    text=current_text.strip(),
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    source_file=source_file,
                    doc_type=doc_type,
                    section_hierarchy=[],
                    source_metadata=source_metadata
                )
                chunks.append(chunk)
                chunk_index += 1

                # Add overlap
                overlap_text = self._get_overlap_text(current_text, overlap_pct)
                current_text = overlap_text + sentence['text']
            else:
                current_text = test_text

        if current_text.strip():
            chunk = self._create_chunk(
                text=current_text.strip(),
                doc_id=doc_id,
                chunk_index=chunk_index,
                source_file=source_file,
                doc_type=doc_type,
                section_hierarchy=[],
                source_metadata=source_metadata
            )
            chunks.append(chunk)

        return chunks

    def _generate_context_summary(self, text: str, source_metadata: Optional[Dict]) -> str:
        """Generate a brief context summary for the document."""
        parts = []

        # Add metadata-based context
        if source_metadata:
            if source_metadata.get('title'):
                parts.append(source_metadata['title'])
            if source_metadata.get('doc_type'):
                parts.append(f"({source_metadata['doc_type']})")
            if source_metadata.get('jurisdiction'):
                parts.append(f"from {source_metadata['jurisdiction']}")

        # Extract first meaningful header or paragraph
        header_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        if header_match:
            parts.append(f"- {header_match.group(1)}")
        elif not parts:
            # Use first 100 chars as summary
            first_para = text[:200].split('\n')[0]
            parts.append(first_para[:100] + "..." if len(first_para) > 100 else first_para)

        return " ".join(parts) if parts else "Document content"

    def _verify_content_retention(
        self, original_text: str, chunks: List[Chunk],
        original_chars: int, original_words: int
    ) -> Dict:
        """
        Verify that chunking preserved all source content.

        Returns stats about content retention for quality assurance.
        """
        # Reconstruct text from chunks (excluding context prefixes)
        chunk_texts = []
        for chunk in chunks:
            text = chunk.text
            # Remove context prefixes for comparison
            text = re.sub(r'\[DOCUMENT CONTEXT:.*?\]', '', text)
            text = re.sub(r'\[SECTION:.*?\]', '', text)
            text = re.sub(r'\[PAGES:.*?\]', '', text)
            text = re.sub(r'\[CONTEXT:.*?\]', '', text)
            chunk_texts.append(text.strip())

        combined_text = " ".join(chunk_texts)

        # Calculate retention metrics
        chunk_char_count = len(combined_text)
        chunk_word_count = len(combined_text.split())

        # Check for key content preservation
        original_paragraphs = set(p.strip() for p in original_text.split('\n\n') if len(p.strip()) > 50)
        retained_paragraphs = sum(1 for p in original_paragraphs if p[:100] in combined_text)

        return {
            'original_chars': original_chars,
            'chunk_chars': chunk_char_count,
            'char_retention_pct': round((chunk_char_count / max(original_chars, 1)) * 100, 2),
            'original_words': original_words,
            'chunk_words': chunk_word_count,
            'word_retention_pct': round((chunk_word_count / max(original_words, 1)) * 100, 2),
            'total_chunks': len(chunks),
            'avg_chunk_tokens': sum(c.metadata.token_count for c in chunks) // max(len(chunks), 1),
            'paragraph_retention_pct': round((retained_paragraphs / max(len(original_paragraphs), 1)) * 100, 2),
            'retention_verified': chunk_char_count >= original_chars * 0.95  # 95% threshold
        }


# ============================================================================
# EmbeddingClient - Multi-Provider Embeddings
# ============================================================================

class EmbeddingClient:
    """
    Multi-provider embedding client with OpenAI as primary.

    Supports:
    - OpenAI text-embedding-3-large (3072 dimensions)
    - Cost tracking and estimation
    """

    MODELS = {
        'text-embedding-3-large': {
            'dimensions': 3072,
            'max_tokens': 8191,
            'cost_per_million': 0.13
        },
        'text-embedding-3-small': {
            'dimensions': 1536,
            'max_tokens': 8191,
            'cost_per_million': 0.02
        },
        'text-embedding-ada-002': {
            'dimensions': 1536,
            'max_tokens': 8191,
            'cost_per_million': 0.10
        }
    }

    def __init__(self, api_key: str, model: str = 'text-embedding-3-large'):
        """
        Initialize embedding client.

        Args:
            api_key: OpenAI API key
            model: Model name (default: text-embedding-3-large)
        """
        if not HAS_OPENAI:
            raise ImportError("openai package not installed. Run: pip install openai")

        self.api_key = api_key
        self.model = model
        self.client = OpenAI(api_key=api_key)
        self.model_info = self.MODELS.get(model, self.MODELS['text-embedding-3-large'])

        # Track usage
        self.total_tokens = 0
        self.total_cost = 0.0

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions for current model."""
        return self.model_info['dimensions']

    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        # Truncate if too long
        max_chars = self.model_info['max_tokens'] * 4
        if len(text) > max_chars:
            text = text[:max_chars]

        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )

        # Track usage
        tokens = response.usage.total_tokens
        self.total_tokens += tokens
        self.total_cost += (tokens / 1_000_000) * self.model_info['cost_per_million']

        return response.data[0].embedding

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        progress_callback: Optional[callable] = None
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            batch_size: Number of texts per API call
            progress_callback: Optional callback(current, total) for progress

        Returns:
            List of embedding vectors
        """
        embeddings = []
        total = len(texts)

        for i in range(0, total, batch_size):
            batch = texts[i:i+batch_size]

            # Truncate each text
            max_chars = self.model_info['max_tokens'] * 4
            batch = [t[:max_chars] if len(t) > max_chars else t for t in batch]

            response = self.client.embeddings.create(
                model=self.model,
                input=batch
            )

            # Track usage
            tokens = response.usage.total_tokens
            self.total_tokens += tokens
            self.total_cost += (tokens / 1_000_000) * self.model_info['cost_per_million']

            # Extract embeddings in order
            batch_embeddings = [item.embedding for item in response.data]
            embeddings.extend(batch_embeddings)

            if progress_callback:
                progress_callback(min(i + batch_size, total), total)

        return embeddings

    def estimate_cost(self, texts: List[str]) -> Tuple[int, float]:
        """
        Estimate cost for embedding texts.

        Args:
            texts: List of texts

        Returns:
            Tuple of (estimated_tokens, estimated_cost)
        """
        total_chars = sum(len(t) for t in texts)
        estimated_tokens = total_chars // 4  # Rough estimate
        estimated_cost = (estimated_tokens / 1_000_000) * self.model_info['cost_per_million']
        return estimated_tokens, estimated_cost

    def get_usage(self) -> Dict:
        """Get current usage statistics."""
        return {
            'total_tokens': self.total_tokens,
            'total_cost': self.total_cost,
            'model': self.model
        }


class LocalEmbeddingClient:
    """
    Local embedding client using sentence-transformers.

    Free, offline, no API key required.
    Good fallback when OpenAI is unavailable.
    """

    MODELS = {
        'all-MiniLM-L6-v2': {
            'dimensions': 384,
            'max_tokens': 256,
            'description': 'Fast, good quality (default)'
        },
        'all-mpnet-base-v2': {
            'dimensions': 768,
            'max_tokens': 384,
            'description': 'Better quality, slower'
        },
        'legal-bert-base-uncased': {
            'dimensions': 768,
            'max_tokens': 512,
            'description': 'Legal domain optimized'
        }
    }

    def __init__(self, model: str = 'all-MiniLM-L6-v2'):
        """
        Initialize local embedding client.

        Args:
            model: Model name (default: all-MiniLM-L6-v2)
        """
        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError("sentence-transformers not installed. Run: pip install sentence-transformers")

        self.model_name = model
        self.model = SentenceTransformer(model)
        self.model_info = self.MODELS.get(model, self.MODELS['all-MiniLM-L6-v2'])

        # Track usage (free, so just counts)
        self.total_tokens = 0
        self.total_cost = 0.0

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions for current model."""
        return self.model_info['dimensions']

    def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        # Truncate if too long
        max_chars = self.model_info['max_tokens'] * 4
        if len(text) > max_chars:
            text = text[:max_chars]

        embedding = self.model.encode(text, convert_to_numpy=True)
        self.total_tokens += len(text) // 4  # Rough estimate

        return embedding.tolist()

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 32,
        progress_callback: Optional[callable] = None
    ) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        max_chars = self.model_info['max_tokens'] * 4
        texts = [t[:max_chars] if len(t) > max_chars else t for t in texts]

        embeddings = []
        total = len(texts)

        for i in range(0, total, batch_size):
            batch = texts[i:i+batch_size]
            batch_embeddings = self.model.encode(batch, convert_to_numpy=True)
            embeddings.extend([e.tolist() for e in batch_embeddings])

            self.total_tokens += sum(len(t) // 4 for t in batch)

            if progress_callback:
                progress_callback(min(i + batch_size, total), total)

        return embeddings

    def estimate_cost(self, texts: List[str]) -> Tuple[int, float]:
        """Estimate cost (always 0 for local)."""
        total_chars = sum(len(t) for t in texts)
        estimated_tokens = total_chars // 4
        return estimated_tokens, 0.0  # Free!

    def get_usage(self) -> Dict:
        """Get current usage statistics."""
        return {
            'total_tokens': self.total_tokens,
            'total_cost': 0.0,
            'model': self.model_name,
            'provider': 'local'
        }


def get_embedding_client(api_key: str = None, provider: str = 'auto', model: str = None):
    """
    Factory function to get the best available embedding client.

    Args:
        api_key: API key for OpenAI (optional)
        provider: 'openai', 'local', or 'auto' (tries openai first, falls back to local)
        model: Specific model name (optional)

    Returns:
        EmbeddingClient or LocalEmbeddingClient instance
    """
    if provider == 'openai':
        if not api_key:
            raise ValueError("OpenAI API key required for OpenAI embeddings")
        return EmbeddingClient(api_key, model or 'text-embedding-3-large')

    elif provider == 'local':
        return LocalEmbeddingClient(model or 'all-MiniLM-L6-v2')

    elif provider == 'auto':
        # Try OpenAI first if key provided
        if api_key and HAS_OPENAI:
            try:
                return EmbeddingClient(api_key, model or 'text-embedding-3-large')
            except Exception as e:
                logger.warning(f"OpenAI embeddings failed: {e}, falling back to local")

        # Fall back to local
        if HAS_SENTENCE_TRANSFORMERS:
            logger.info("Using local sentence-transformers for embeddings (free)")
            return LocalEmbeddingClient(model or 'all-MiniLM-L6-v2')

        raise ImportError("No embedding provider available. Install openai or sentence-transformers.")

    else:
        raise ValueError(f"Unknown provider: {provider}")


# ============================================================================
# SimpleVectorStore - Numpy-based fallback (no external dependencies)
# ============================================================================

class SimpleVectorStore:
    """
    Simple numpy-based vector store - fallback when chromadb is unavailable.

    Features:
    - Persistent JSON storage
    - Cosine similarity search
    - Metadata filtering
    - No external dependencies beyond numpy
    """

    def __init__(self, persist_dir: str = "./vector_db"):
        """Initialize simple vector store."""
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.index_file = self.persist_dir / "vector_index.json"
        self.embeddings_file = self.persist_dir / "embeddings.npy"

        # In-memory storage
        self.documents: Dict[str, dict] = {}  # doc_id -> metadata
        self.chunks: List[dict] = []  # chunk metadata
        self.embeddings: Optional[np.ndarray] = None

        self._load()

    def _load(self):
        """Load index from disk."""
        try:
            if self.index_file.exists():
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.documents = data.get('documents', {})
                    self.chunks = data.get('chunks', [])

            if self.embeddings_file.exists():
                import numpy as np
                self.embeddings = np.load(str(self.embeddings_file))
        except Exception as e:
            logger.warning(f"Failed to load vector index: {e}")
            self.documents = {}
            self.chunks = []
            self.embeddings = None

    def _save(self):
        """Save index to disk."""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'documents': self.documents,
                    'chunks': self.chunks
                }, f, indent=2, default=str)

            if self.embeddings is not None:
                import numpy as np
                np.save(str(self.embeddings_file), self.embeddings)
        except Exception as e:
            logger.error(f"Failed to save vector index: {e}")

    def add_document(
        self,
        chunks: List['Chunk'],
        embeddings: List[List[float]],
        doc_id: Optional[str] = None
    ) -> str:
        """Add document chunks to the store."""
        import numpy as np

        if not chunks or not embeddings:
            return ""

        doc_id = doc_id or chunks[0].metadata.doc_id

        # Store document metadata
        self.documents[doc_id] = {
            'source_file': chunks[0].metadata.source_file,
            'doc_type': chunks[0].metadata.doc_type,
            'chunk_count': len(chunks),
            'added_at': datetime.now().isoformat()
        }

        # Store chunks
        new_chunks = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            new_chunks.append({
                'id': f"{doc_id}_chunk_{i}",
                'doc_id': doc_id,
                'text': chunk.text,
                'metadata': asdict(chunk.metadata)
            })

        self.chunks.extend(new_chunks)

        # Update embeddings array
        new_embeddings = np.array(embeddings, dtype=np.float32)
        if self.embeddings is None:
            self.embeddings = new_embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])

        self._save()
        return doc_id

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document and its chunks."""
        import numpy as np

        if doc_id not in self.documents:
            return False

        # Find indices to remove
        indices_to_remove = [
            i for i, chunk in enumerate(self.chunks)
            if chunk.get('doc_id') == doc_id
        ]

        if not indices_to_remove:
            del self.documents[doc_id]
            self._save()
            return True

        # Remove chunks
        self.chunks = [
            chunk for i, chunk in enumerate(self.chunks)
            if i not in indices_to_remove
        ]

        # Remove embeddings
        if self.embeddings is not None:
            mask = np.ones(len(self.embeddings), dtype=bool)
            mask[indices_to_remove] = False
            self.embeddings = self.embeddings[mask] if mask.any() else None

        del self.documents[doc_id]
        self._save()
        return True

    def search(
        self,
        query_embedding: List[float],
        k: int = 10,
        filters: Optional[Dict] = None
    ) -> List['SearchResult']:
        """Search for similar chunks using cosine similarity."""
        import numpy as np

        if self.embeddings is None or len(self.chunks) == 0:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)

        # Normalize for cosine similarity
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        embeddings_norm = self.embeddings / (
            np.linalg.norm(self.embeddings, axis=1, keepdims=True) + 1e-10
        )

        # Calculate cosine similarities
        similarities = np.dot(embeddings_norm, query_norm)

        # Apply metadata filter if provided
        valid_indices = list(range(len(self.chunks)))
        if filters:
            valid_indices = []
            for i, chunk in enumerate(self.chunks):
                meta = chunk.get('metadata', {})
                match = True
                for key, value in filters.items():
                    if value and meta.get(key) != value:
                        match = False
                        break
                if match:
                    valid_indices.append(i)

        if not valid_indices:
            return []

        # Get top-k from valid indices
        valid_similarities = [(i, similarities[i]) for i in valid_indices]
        valid_similarities.sort(key=lambda x: x[1], reverse=True)
        top_k = valid_similarities[:k]

        results = []
        for idx, score in top_k:
            chunk = self.chunks[idx]
            results.append(SearchResult(
                chunk_text=chunk['text'],
                metadata=chunk['metadata'],
                score=float(score),
                source="dense"
            ))

        return results

    def get_stats(self) -> Dict:
        """Get store statistics."""
        # Calculate storage size
        total_size = 0
        if self.index_file.exists():
            total_size += self.index_file.stat().st_size
        if self.embeddings_file.exists():
            total_size += self.embeddings_file.stat().st_size

        return {
            'document_count': len(self.documents),
            'chunk_count': len(self.chunks),
            'storage_size_mb': total_size / (1024 * 1024)
        }

    def list_documents(self) -> List[Dict]:
        """List all documents in the store."""
        return [
            {
                'doc_id': doc_id,
                **meta
            }
            for doc_id, meta in self.documents.items()
        ]


# ============================================================================
# VectorStore - ChromaDB Integration
# ============================================================================

class VectorStore:
    """
    ChromaDB-based vector store with metadata filtering.

    Features:
    - Persistent storage
    - Metadata filtering (doc_type, jurisdiction, etc.)
    - Document management (add, delete, update)
    """

    COLLECTION_NAME = "legal_documents"

    def __init__(self, persist_dir: str = "./vector_db"):
        """
        Initialize vector store.

        Args:
            persist_dir: Directory for persistent storage
        """
        if not HAS_CHROMADB:
            raise ImportError("chromadb package not installed. Run: pip install chromadb")

        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB - handle both old (0.3.x) and new (0.4+) APIs
        if CHROMADB_NEW_API:
            # New API (0.4+)
            self.client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False)
            )
        else:
            # Old API (0.3.x)
            from chromadb.config import Settings as OldSettings
            self.client = chromadb.Client(OldSettings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=str(self.persist_dir),
                anonymized_telemetry=False
            ))

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}  # Cosine similarity
        )

    def add_document(
        self,
        chunks: List[Chunk],
        embeddings: List[List[float]],
        doc_id: Optional[str] = None
    ) -> str:
        """
        Add document chunks to the vector store.

        Args:
            chunks: List of Chunk objects
            embeddings: List of embedding vectors
            doc_id: Optional document ID (generated if not provided)

        Returns:
            Document ID
        """
        if not chunks:
            return ""

        if len(chunks) != len(embeddings):
            raise ValueError("Number of chunks must match number of embeddings")

        # Use doc_id from first chunk or generate one
        if not doc_id:
            doc_id = chunks[0].metadata.doc_id

        # Prepare data for ChromaDB
        ids = [f"{doc_id}_{c.metadata.chunk_index}" for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [c.metadata.to_dict() for c in chunks]

        # Add to collection
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )

        logger.info(f"Added {len(chunks)} chunks for document {doc_id}")

        # Persist for old API
        if not CHROMADB_NEW_API:
            self.client.persist()

        return doc_id

    def search(
        self,
        query_embedding: List[float],
        k: int = 10,
        filters: Optional[Dict] = None
    ) -> List[SearchResult]:
        """
        Search for similar chunks.

        Args:
            query_embedding: Query embedding vector
            k: Number of results to return
            filters: Optional metadata filters

        Returns:
            List of SearchResult objects
        """
        # Build where clause for filtering
        where = None
        if filters:
            where = self._build_where_clause(filters)

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"]
        )

        # Convert to SearchResult objects
        search_results = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                # Convert distance to similarity score (cosine distance -> similarity)
                distance = results['distances'][0][i] if results['distances'] else 0
                score = 1 - distance  # Cosine similarity = 1 - cosine distance

                search_results.append(SearchResult(
                    chunk_text=doc,
                    metadata=results['metadatas'][0][i] if results['metadatas'] else {},
                    score=score,
                    source="dense"
                ))

        return search_results

    def _build_where_clause(self, filters: Dict) -> Dict:
        """Build ChromaDB where clause from filters."""
        conditions = []

        for key, value in filters.items():
            if value and value != "All":
                if isinstance(value, list):
                    conditions.append({key: {"$in": value}})
                else:
                    conditions.append({key: value})

        if not conditions:
            return None
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"$and": conditions}

    def delete_document(self, doc_id: str) -> int:
        """
        Delete all chunks for a document.

        Args:
            doc_id: Document ID

        Returns:
            Number of chunks deleted
        """
        # Find all chunks with this doc_id
        results = self.collection.get(
            where={"doc_id": doc_id},
            include=["metadatas"]
        )

        if not results['ids']:
            return 0

        count = len(results['ids'])
        self.collection.delete(ids=results['ids'])

        # Persist for old API
        if not CHROMADB_NEW_API:
            self.client.persist()

        logger.info(f"Deleted {count} chunks for document {doc_id}")
        return count

    def get_document_ids(self) -> List[str]:
        """Get list of all document IDs in the store."""
        results = self.collection.get(include=["metadatas"])

        doc_ids = set()
        for metadata in results.get('metadatas', []):
            if metadata and 'doc_id' in metadata:
                doc_ids.add(metadata['doc_id'])

        return sorted(list(doc_ids))

    def get_document_info(self, doc_id: str) -> Dict:
        """Get information about a document."""
        results = self.collection.get(
            where={"doc_id": doc_id},
            include=["metadatas"]
        )

        if not results['metadatas']:
            return {}

        # Get info from first chunk
        first_meta = results['metadatas'][0]
        return {
            'doc_id': doc_id,
            'source_file': first_meta.get('source_file', 'unknown'),
            'doc_type': first_meta.get('doc_type', 'default'),
            'chunk_count': len(results['ids']),
            'jurisdiction': first_meta.get('jurisdiction', ''),
            'date': first_meta.get('date', '')
        }

    def get_stats(self) -> Dict:
        """Get vector store statistics."""
        count = self.collection.count()
        doc_ids = self.get_document_ids()

        # Estimate storage size - check multiple possible db files
        size_bytes = 0
        for db_file in ['chroma.sqlite3', 'chroma-collections.parquet', 'chroma-embeddings.parquet']:
            db_path = self.persist_dir / db_file
            if db_path.exists():
                size_bytes += db_path.stat().st_size

        # Also check index directory for old API
        index_dir = self.persist_dir / "index"
        if index_dir.exists() and index_dir.is_dir():
            for f in index_dir.rglob('*'):
                if f.is_file():
                    size_bytes += f.stat().st_size

        size_mb = size_bytes / (1024 * 1024)

        return {
            'total_chunks': count,
            'total_documents': len(doc_ids),
            'size_mb': round(size_mb, 2),
            'persist_dir': str(self.persist_dir)
        }

    def list_documents(self) -> List[Dict]:
        """List all documents with their info."""
        doc_ids = self.get_document_ids()
        return [self.get_document_info(doc_id) for doc_id in doc_ids]


def get_vector_store(persist_dir: str = "./vector_db") -> Union[VectorStore, SimpleVectorStore]:
    """
    Factory function to get the best available vector store.

    Returns ChromaDB-based VectorStore if available, otherwise falls back
    to SimpleVectorStore (numpy-based).

    Args:
        persist_dir: Directory for persistent storage

    Returns:
        VectorStore or SimpleVectorStore instance
    """
    if HAS_CHROMADB:
        try:
            return VectorStore(persist_dir)
        except Exception as e:
            logger.warning(f"ChromaDB initialization failed ({e}), using SimpleVectorStore")

    if HAS_NUMPY:
        logger.info("Using SimpleVectorStore (numpy-based fallback)")
        return SimpleVectorStore(persist_dir)

    raise ImportError(
        "No vector store available. Install either chromadb or numpy:\n"
        "  pip install chromadb  # Full-featured vector database\n"
        "  pip install numpy     # Lightweight fallback"
    )


# ============================================================================
# HybridRetriever - Dense + Sparse Search
# ============================================================================

class HybridRetriever:
    """
    Hybrid retriever combining dense (vector) and sparse (BM25) search.

    Default weighting: 70% dense / 30% sparse
    """

    def __init__(
        self,
        vector_store: Union[VectorStore, SimpleVectorStore],
        embedding_client: 'EmbeddingClient',
        dense_weight: float = 0.70,
        sparse_weight: float = 0.30
    ):
        """
        Initialize hybrid retriever.

        Args:
            vector_store: VectorStore or SimpleVectorStore instance
            embedding_client: EmbeddingClient instance
            dense_weight: Weight for dense (vector) search
            sparse_weight: Weight for sparse (BM25) search
        """
        self.vector_store = vector_store
        self.embedding_client = embedding_client
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight

        # BM25 index (built lazily)
        self.bm25_index = None
        self.bm25_docs = []
        self.bm25_metadatas = []

    def _build_bm25_index(self):
        """Build BM25 index from all documents in vector store."""
        if not HAS_BM25:
            logger.warning("rank_bm25 not installed, sparse search disabled")
            return

        # Get all documents - handle both ChromaDB and SimpleVectorStore
        if hasattr(self.vector_store, 'collection'):
            # ChromaDB-based VectorStore
            results = self.vector_store.collection.get(
                include=["documents", "metadatas"]
            )
            if not results['documents']:
                return
            self.bm25_docs = results['documents']
            self.bm25_metadatas = results['metadatas']
        elif hasattr(self.vector_store, 'chunks'):
            # SimpleVectorStore (numpy-based)
            if not self.vector_store.chunks:
                return
            self.bm25_docs = [chunk['text'] for chunk in self.vector_store.chunks]
            self.bm25_metadatas = [chunk['metadata'] for chunk in self.vector_store.chunks]
        else:
            logger.warning("Unknown vector store type, BM25 index not built")
            return

        if not self.bm25_docs:
            return

        # Tokenize documents for BM25
        tokenized = [doc.lower().split() for doc in self.bm25_docs]
        self.bm25_index = BM25Okapi(tokenized)

        logger.info(f"Built BM25 index with {len(self.bm25_docs)} documents")

    def retrieve(
        self,
        query: str,
        k: int = 10,
        filters: Optional[Dict] = None,
        use_hybrid: bool = True
    ) -> List[SearchResult]:
        """
        Retrieve relevant chunks for a query.

        Args:
            query: Search query
            k: Number of results to return
            filters: Optional metadata filters
            use_hybrid: Whether to use hybrid search (default True)

        Returns:
            List of SearchResult objects sorted by score
        """
        results = []

        # Dense search
        query_embedding = self.embedding_client.embed_text(query)
        dense_results = self.vector_store.search(
            query_embedding=query_embedding,
            k=k * 2 if use_hybrid else k,
            filters=filters
        )

        if not use_hybrid or not HAS_BM25:
            return dense_results[:k]

        # Ensure BM25 index is built
        if self.bm25_index is None:
            self._build_bm25_index()

        if self.bm25_index is None:
            return dense_results[:k]

        # Sparse search
        tokenized_query = query.lower().split()
        bm25_scores = self.bm25_index.get_scores(tokenized_query)

        # Get top k*2 sparse results
        sparse_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True
        )[:k * 2]

        sparse_results = []
        max_bm25_score = max(bm25_scores) if bm25_scores.any() else 1

        for idx in sparse_indices:
            if bm25_scores[idx] > 0:
                # Normalize BM25 score to 0-1 range
                normalized_score = bm25_scores[idx] / max_bm25_score
                sparse_results.append(SearchResult(
                    chunk_text=self.bm25_docs[idx],
                    metadata=self.bm25_metadatas[idx] if self.bm25_metadatas else {},
                    score=normalized_score,
                    source="sparse"
                ))

        # Reciprocal Rank Fusion
        combined = self._reciprocal_rank_fusion(dense_results, sparse_results, k)

        return combined

    def _reciprocal_rank_fusion(
        self,
        dense_results: List[SearchResult],
        sparse_results: List[SearchResult],
        k: int,
        rrf_k: int = 60
    ) -> List[SearchResult]:
        """
        Combine results using Reciprocal Rank Fusion.

        Args:
            dense_results: Results from dense search
            sparse_results: Results from sparse search
            k: Number of final results
            rrf_k: RRF parameter (default 60)

        Returns:
            Combined and reranked results
        """
        scores = {}
        result_map = {}

        # Score dense results
        for rank, result in enumerate(dense_results):
            key = result.chunk_text[:100]  # Use text prefix as key
            rrf_score = self.dense_weight * (1 / (rrf_k + rank + 1))
            scores[key] = scores.get(key, 0) + rrf_score
            result_map[key] = result

        # Score sparse results
        for rank, result in enumerate(sparse_results):
            key = result.chunk_text[:100]
            rrf_score = self.sparse_weight * (1 / (rrf_k + rank + 1))
            scores[key] = scores.get(key, 0) + rrf_score
            if key not in result_map:
                result_map[key] = result

        # Sort by combined score
        sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:k]

        # Build final results
        final_results = []
        for key in sorted_keys:
            result = result_map[key]
            result.score = scores[key]  # Update with combined score
            result.source = "hybrid"
            final_results.append(result)

        return final_results

    def refresh_index(self):
        """Refresh the BM25 index (call after adding/removing documents)."""
        self.bm25_index = None
        self.bm25_docs = []
        self.bm25_metadatas = []
        self._build_bm25_index()


# ============================================================================
# Convenience Functions
# ============================================================================

def vectorize_markdown_file(
    md_path: str,
    vector_store: VectorStore,
    embedding_client: EmbeddingClient,
    doc_type: str = 'default',
    source_metadata: Optional[Dict] = None,
    progress_callback: Optional[callable] = None
) -> Tuple[str, int]:
    """
    Convenience function to vectorize a markdown file.

    Args:
        md_path: Path to markdown file
        vector_store: VectorStore instance
        embedding_client: EmbeddingClient instance
        doc_type: Document type for chunking
        source_metadata: Optional source metadata
        progress_callback: Optional progress callback(current, total)

    Returns:
        Tuple of (doc_id, chunk_count)
    """
    # Read file
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Prepare metadata
    if source_metadata is None:
        source_metadata = {}

    source_metadata['source_file'] = Path(md_path).name

    # Extract metadata from YAML frontmatter if present
    yaml_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    if yaml_match:
        try:
            import yaml
            frontmatter = yaml.safe_load(yaml_match.group(1))
            if frontmatter:
                source_metadata.update({
                    'date': frontmatter.get('date', ''),
                    'jurisdiction': frontmatter.get('jurisdiction', ''),
                    'citation_ref': frontmatter.get('citation_ref', '')
                })
        except Exception:
            pass

    # Chunk the document
    chunker = RAGChunker()
    chunks = chunker.chunk_by_structure(content, doc_type, source_metadata)

    if not chunks:
        return "", 0

    # Generate embeddings
    texts = [c.text for c in chunks]
    embeddings = embedding_client.embed_batch(texts, progress_callback=progress_callback)

    # Store in vector database
    doc_id = vector_store.add_document(chunks, embeddings)

    return doc_id, len(chunks)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    'RAGChunker',
    'EmbeddingClient',
    'VectorStore',
    'HybridRetriever',
    'Chunk',
    'ChunkMetadata',
    'SearchResult',
    'vectorize_markdown_file',
    'HAS_CHROMADB',
    'HAS_OPENAI',
    'HAS_BM25'
]
