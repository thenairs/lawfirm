"""Local stand-in for the vector store in the design spec (§8: pgvector /
Pinecone / Weaviate in production). Uses TF-IDF + cosine similarity over a
small local JSON corpus so the RAG stage works with no external DB or
embeddings API. Swap `retrieve()` for a real vector store client to go to
production -- the calling code in legal_research.py doesn't need to change.
"""
import json

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import LEGAL_CORPUS_DIR

_vectorizer = None
_matrix = None
_corpus = None


def _load_corpus():
    entries = []
    for fname in ("sample_provisions.json", "precedents.json"):
        path = LEGAL_CORPUS_DIR / fname
        if path.exists():
            entries.extend(json.loads(path.read_text()))
    return entries


def _ensure_index():
    global _vectorizer, _matrix, _corpus
    if _corpus is not None:
        return
    _corpus = _load_corpus()
    if not _corpus:
        _vectorizer, _matrix = None, None
        return
    texts = [f"{e['citation']} {e['title']} {e['summary']}" for e in _corpus]
    _vectorizer = TfidfVectorizer(stop_words="english")
    _matrix = _vectorizer.fit_transform(texts)


def retrieve(query: str, top_k: int = 3, min_score: float = 0.08):
    _ensure_index()
    if not _corpus or _vectorizer is None:
        return []
    query_vec = _vectorizer.transform([query])
    scores = cosine_similarity(query_vec, _matrix)[0]
    ranked = sorted(zip(_corpus, scores), key=lambda x: x[1], reverse=True)
    return [{**entry, "score": float(score)} for entry, score in ranked[:top_k] if score >= min_score]
