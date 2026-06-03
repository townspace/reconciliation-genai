"""
ai/embeddings.py
----------------
Text embeddings for semantic (fuzzy) matching of narrations / descriptions.

LocalEmbedder is a dependency-free, deterministic embedder built from word
tokens + character n-grams hashed into a fixed-width vector. It runs offline
and is robust to the real-world noise the feedback called out:

  - typos / spelling drift      ("Acme Corp"  ~ "Acme Corpration")
  - word reordering             ("payment Acme" ~ "Acme payment")
  - punctuation / spacing       ("INV-001"     ~ "INV 001")
  - abbreviations / extra words ("NEFT Acme Ltd ref 88" ~ "Acme Limited")

Swapping in a hosted embedder (Voyage AI — Anthropic's recommended partner —
or OpenAI text-embedding-3-small) is a drop-in: implement Embedder.embed()
to return L2-normalised vectors and pass it wherever `embedder=` is accepted.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import zlib
from typing import List, Optional, Sequence

import numpy as np


class Embedder:
    """Interface: embed() returns an (n, dim) array of L2-normalised vectors."""
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError


class LocalEmbedder(Embedder):
    """Hashed word + char-n-gram bag-of-features. Deterministic, offline."""

    def __init__(self, dim: int = 1024, char_ngram: int = 3):
        self.dim = dim
        self.char_ngram = char_ngram

    # Word features are weighted above char n-grams: a shared whole token
    # ("globex", "88123") is stronger evidence than an incidental char overlap.
    WORD_WEIGHT = 2.0
    CHAR_WEIGHT = 1.0

    def _features(self, text: str) -> List[tuple]:
        """Return (feature, base_weight) pairs for one text."""
        text = (text or "").lower().strip()
        if not text:
            return [("__empty__", 1.0)]
        feats: List[tuple] = []
        for w in re.findall(r"[a-z0-9]+", text):
            feats.append((f"w:{w}", self.WORD_WEIGHT))   # whole-word feature
        clean = re.sub(r"\s+", " ", text)
        n = self.char_ngram
        for i in range(max(0, len(clean) - n + 1)):       # char n-grams catch typos
            feats.append(("c:" + clean[i:i + n], self.CHAR_WEIGHT))
        return feats or [("__empty__", 1.0)]

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """TF-IDF weighted vectors. IDF is computed over the supplied batch, so
        distinctive tokens (entity names, invoice numbers) dominate and common
        filler words ("payment", "the") are down-weighted. Embed both sides in
        one call to share document frequencies across them.
        """
        feats_per_doc = [self._features(t) for t in texts]
        n_docs = len(texts)

        # document frequency per feature across the batch
        df: dict = {}
        for feats in feats_per_doc:
            for f in set(name for name, _ in feats):
                df[f] = df.get(f, 0) + 1
        # smoothed idf
        idf = {f: np.log((n_docs + 1) / (c + 1)) + 1.0 for f, c in df.items()}

        vecs = np.zeros((n_docs, self.dim), dtype=np.float64)
        for i, feats in enumerate(feats_per_doc):
            for name, w in feats:
                idx = zlib.crc32(name.encode("utf-8")) % self.dim  # stable hash
                vecs[i, idx] += w * idf.get(name, 1.0)
            norm = np.linalg.norm(vecs[i])
            if norm > 0:
                vecs[i] /= norm
        return vecs


class OpenAIEmbedder(Embedder):
    """Hosted embeddings via the OpenAI Embeddings API (urllib, no SDK dep).

    Opt-in: pass an instance explicitly wherever `embedder=` is accepted, e.g.

        from ai.embeddings import OpenAIEmbedder
        wrapper.run_rule("R2", sources, embedder=OpenAIEmbedder())

    Note R2's `sim_threshold` is embedder-dependent. The local embedder uses
    ~0.40; a strong hosted embedder like this one typically warrants ~0.8, so
    raise the rule's threshold accordingly when you switch.
    """

    URL = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: Optional[str] = None,
                 model: str = "text-embedding-3-small", timeout: int = 30):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.timeout = timeout

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set; cannot use OpenAIEmbedder.")
        # OpenAI rejects empty strings; substitute a placeholder token.
        inputs = [t if (t and t.strip()) else " " for t in texts]
        body = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        req = urllib.request.Request(self.URL, data=body, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = sorted(data["data"], key=lambda d: d["index"])
        vecs = np.asarray([r["embedding"] for r in rows], dtype=np.float64)
        # L2-normalise so cosine_sim_matrix() can stay a plain dot product.
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def cosine_sim_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity of every row of `a` against every row of `b`.

    Inputs are assumed L2-normalised (LocalEmbedder guarantees this), so the
    cosine reduces to a dot product.
    """
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]))
    return a @ b.T


def default_embedder() -> Embedder:
    return LocalEmbedder()
