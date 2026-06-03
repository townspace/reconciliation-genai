"""
ai
--
GenAI layer for the reconciliation framework. Every feature degrades gracefully:
with OPENAI_API_KEY set it calls OpenAI (or ANTHROPIC_API_KEY for Claude);
otherwise it uses deterministic local heuristics so the framework always runs.

Public surface
--------------
  default_client()            -> LLMClient (OpenAI/Anthropic if key set, else Offline)
  default_embedder()          -> Embedder for semantic matching
  classify_breaks(result)     -> root cause + commentary + confidence (feedback #2)
  generate_journals(result)   -> draft adjustment journals          (feedback #5)
  detect_sources(frames)      -> auto-detect feeds + recommend rule  (feedback #3)
  detect_anomalies(result)    -> flag outlier / recurring breaks     (feedback #6)
"""

from ai.client import (
    OpenAIClient,
    AnthropicClient,
    LLMClient,
    OfflineClient,
    default_client,
)
from ai.embeddings import (
    Embedder,
    LocalEmbedder,
    OpenAIEmbedder,
    cosine_sim_matrix,
    default_embedder,
)
from ai.classify import classify_breaks
from ai.journal import generate_journals, DEFAULT_GL_MAPPING
from ai.detect import detect_sources, profile_frame
from ai.anomaly import detect_anomalies

__all__ = [
    "OpenAIClient", "AnthropicClient", "OfflineClient", "LLMClient", "default_client",
    "Embedder", "LocalEmbedder", "OpenAIEmbedder", "cosine_sim_matrix", "default_embedder",
    "classify_breaks", "generate_journals", "DEFAULT_GL_MAPPING",
    "detect_sources", "profile_frame", "detect_anomalies",
]
