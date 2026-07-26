"""RAG híbrido: indexación de movimientos y recuperación (SQL + full-text + pgvector).

Los embeddings nunca entran en un cálculo financiero: sirven para *encontrar* movimientos;
las sumas y agregaciones se hacen con SQL/Python determinístico sobre lo recuperado.
"""
