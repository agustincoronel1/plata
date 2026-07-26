"""Constantes de dominio compartidas.

El perfil demo vive acá y en un solo lugar. Todavía no hay autenticación, así que
los endpoints operan siempre sobre este perfil fijo en lugar de resolver "el usuario
actual". Cuando exista autenticación, este identificador desaparece de los servicios
y lo reemplaza el usuario de la sesión.

No buscar "el primer usuario" de la tabla ni depender del nombre del perfil: el
contrato es este UUID.
"""

from uuid import UUID

# UUID fijo del perfil demo. Es también la clave de idempotencia del seed.
DEMO_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
