"""Paquete compartido de Zenit.

Lo usan dos procesos: el punto de entrada (backend/ingesta), que escribe, y la
API (zenit.api), que consulta. Lo que ambos necesitan acordar vive aqui: el
esquema canonico, el protocolo de linea y el contrato del repositorio.
"""
