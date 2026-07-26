"""Excepciones de dominio de la capa de servicios.

Son deliberadamente pocas y simples: no envuelven errores de PostgreSQL ni cargan
detalles técnicos. La capa de API las traduce a códigos HTTP. Nunca llevan SQL, nombres
de constraints ni mensajes de psycopg: solo un texto pensado para mostrarle al usuario.
"""

from __future__ import annotations


class NotFoundError(Exception):
    """El recurso pedido no existe (o no pertenece al perfil demo).

    El mensaje ya viene listo para el cliente: "Movimiento no encontrado", etc.
    """
