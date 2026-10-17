"""Stable file identities across native Undo, without retargeting editor models."""
from .local_documents import identity


def project_copy(document, mapping):
    result = identity.duplicate(document, id_maps=mapping,
                                document_id=document['document_id']).document
    # Only owned payload IDs are projected; the saved file owns its revision
    # and provenance, independently of every historical runtime snapshot.
    result['revision'] = document['revision']
    result['extensions'] = document['extensions']
    return result
