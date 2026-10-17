"""Qt-free local documents. No UI, database, network or execution side effects."""
from .schema import DocumentError, decode, encode, new_document, validate

__all__ = ['DocumentError', 'decode', 'encode', 'new_document', 'validate']
