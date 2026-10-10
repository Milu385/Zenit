"""Claves derivadas con bcrypt (RNF-SEG-06). Nunca se guarda ni se registra
la clave en texto claro."""
import bcrypt

COSTO = 12
MINIMO = 12
# bcrypt solo mira los primeros 72 bytes; una clave mas larga se rechaza en
# vez de truncarse en silencio.
MAXIMO_BYTES = 72

# Hash de una clave que nadie conoce. Se verifica contra el cuando el usuario
# no existe, para que la respuesta tarde lo mismo y no revele que usuarios hay.
_SENUELO = bcrypt.hashpw(b"zenit-usuario-inexistente", bcrypt.gensalt(COSTO))


class ClaveInvalida(ValueError):
    pass


def validar(clave: str) -> None:
    if len(clave) < MINIMO:
        raise ClaveInvalida(f"la clave debe tener al menos {MINIMO} caracteres")
    if len(clave.encode()) > MAXIMO_BYTES:
        raise ClaveInvalida(f"la clave no puede pasar de {MAXIMO_BYTES} bytes")


def derivar(clave: str) -> str:
    validar(clave)
    return bcrypt.hashpw(clave.encode(), bcrypt.gensalt(COSTO)).decode()


def verificar(clave: str, clave_hash: str | None) -> bool:
    candidato = clave.encode()[:MAXIMO_BYTES]
    if not clave_hash:
        bcrypt.checkpw(candidato, _SENUELO)
        return False
    try:
        return bcrypt.checkpw(candidato, clave_hash.encode())
    except ValueError:  # hash corrupto en la tabla
        return False
