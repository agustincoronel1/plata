"""Base de AI Engineering de Vector.

Estructura deliberadamente pequeña:

- ``providers``: comunicación con el modelo (mock y real), detrás de una interfaz.
- ``gateway``: orquesta la llamada, valida el structured output y arma la trazabilidad.
- ``prompts``: registro de prompts versionados fuera del código, con checksum.
- ``evaluators``: evaluación offline reproducible contra el proveedor mock.

Regla de oro: el modelo **propone**, nunca **dispone**. No calcula el saldo, no persiste,
no ejecuta acciones. Solo produce un borrador estructurado que un humano confirma.
"""
