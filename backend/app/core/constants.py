"""Constantes de dominio compartidas.

El perfil demo vive acá y en un solo lugar. Es el que carga `app/scripts/seed_demo.py`.

Ningún código de producción lo usa: tanto el flujo financiero como el de IA resuelven el
usuario desde el JWT verificado. Lo que queda son tres consumidores deliberados y
acotados, que lo pasan de forma explícita:

- El seed de demostración, que crea ese perfil.
- Los evaluadores offline, que corren sobre el perfil sembrado.
- `app/scripts/real_ai_smoke.py`, la validación manual con el proveedor real.

Ninguna función lo toma como valor por defecto: si un llamador no dice de quién son los
datos, el error salta al escribir la llamada y no en producción.

No buscar "el primer usuario" de la tabla ni depender del nombre del perfil: el
contrato es este UUID.
"""

from uuid import UUID

# UUID fijo del perfil demo. Es también la clave de idempotencia del seed.
DEMO_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
