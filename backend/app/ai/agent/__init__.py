"""Copiloto financiero: agente LangGraph con tool calling, evidencia y aprobación humana.

El agente NO calcula el saldo ni inventa montos: orquesta herramientas acotadas (que reusan
los servicios determinísticos) y responde citando la evidencia recuperada. Las escrituras se
pausan para que una persona apruebe antes de tocar la base.
"""
