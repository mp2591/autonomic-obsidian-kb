"""Autonomic, token-economical knowledge infrastructure for AI agents."""

from .config import KBConfig
from .models import MemoryRecord, RetrievalManifest

__all__ = ["KBConfig", "MemoryRecord", "RetrievalManifest"]
__version__ = "0.1.0"
