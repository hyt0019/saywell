"""Ed25519 signatures (RFC 8032), used to check that an update was published by the project.

Follows the reference code in section 6 of the RFC. Plain Python is fast enough here: one
signature is checked per update, in a few milliseconds, and nothing secret is handled on
users' machines (signing happens only when a release is built)."""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_NEUTRAL = (0, 1, 1, 0)


def _hash(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _add(a: tuple, b: tuple) -> tuple:
    """Add two points in extended coordinates (X, Y, Z, T)."""
    first = (a[1] - a[0]) * (b[1] - b[0]) % _P
    second = (a[1] + a[0]) * (b[1] + b[0]) % _P
    cross = 2 * a[3] * b[3] * _D % _P
    depth = 2 * a[2] * b[2] % _P
    e, f, g, h = second - first, depth - cross, depth + cross, second + first
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _multiply(scalar: int, point: tuple) -> tuple:
    result = _NEUTRAL
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _same(a: tuple, b: tuple) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    square = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if square == 0:
        return None if sign else 0
    x = pow(square, (_P + 3) // 8, _P)
    if (x * x - square) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - square) % _P:
        return None
    return _P - x if x & 1 != sign else x


def _compress(point: tuple) -> bytes:
    inverse = pow(point[2], _P - 2, _P)
    x, y = point[0] * inverse % _P, point[1] * inverse % _P
    return (y | (x & 1) << 255).to_bytes(32, "little")


def _decompress(data: bytes) -> tuple | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    x = _recover_x(y & (1 << 255) - 1, y >> 255)
    if x is None:
        return None
    y &= (1 << 255) - 1
    return x, y, 1, x * y % _P


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_BASE = (_GX, _GY, 1, _GX * _GY % _P)


def _expand(secret: bytes) -> tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("An Ed25519 secret key is 32 bytes.")
    digest = hashlib.sha512(secret).digest()
    scalar = int.from_bytes(digest[:32], "little") & (1 << 254) - 8 | 1 << 254
    return scalar, digest[32:]


def public_key(secret: bytes) -> bytes:
    return _compress(_multiply(_expand(secret)[0], _BASE))


def sign(secret: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(secret)
    public = _compress(_multiply(scalar, _BASE))
    nonce = _hash(prefix + message) % _L
    commitment = _compress(_multiply(nonce, _BASE))
    challenge = _hash(commitment + public + message) % _L
    return commitment + ((nonce + challenge * scalar) % _L).to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    key = _decompress(public)
    commitment = _decompress(signature[:32])
    proof = int.from_bytes(signature[32:], "little")
    if key is None or commitment is None or proof >= _L:
        return False
    challenge = _hash(signature[:32] + public + message) % _L
    return _same(_multiply(proof, _BASE), _add(commitment, _multiply(challenge, key)))
