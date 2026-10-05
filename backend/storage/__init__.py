"""Unity Catalog write layer. Implemented in Sprint 6.

Persists the running session transcript (meeting text, assistant turns,
pause gap markers), the question slice, and the answer under board-tier
ACL. Must never include raw audio in a write payload.
"""
