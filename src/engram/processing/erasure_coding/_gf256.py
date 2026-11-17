"""GF(256) helpers matching the legacy reedsolo encoder's generator matrix."""

from functools import lru_cache

from reedsolo import RSCodec

_GF_PRIMITIVE_POLY = 0x11D
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256

_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= _GF_PRIMITIVE_POLY

for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("zero has no multiplicative inverse")
    return _GF_EXP[255 - _GF_LOG[a]]


_GF_MUL_TABLE = tuple(bytes(_gf_mul(a, b) for b in range(256)) for a in range(256))


@lru_cache(maxsize=32)
def _generator_matrix(
    data_shards: int, total_shards: int
) -> tuple[tuple[int, ...], ...]:
    """Derive the systematic matrix from reedsolo's own encoding behavior."""
    rs = RSCodec(total_shards - data_shards)
    encoded_basis = []
    for column in range(data_shards):
        basis = bytearray(data_shards)
        basis[column] = 1
        codeword = bytes(rs.encode(bytes(basis)))
        if len(codeword) != total_shards:
            raise ValueError("Unexpected Reed-Solomon codeword size")
        encoded_basis.append(codeword)
    return tuple(
        tuple(encoded_basis[column][row] for column in range(data_shards))
        for row in range(total_shards)
    )


def _invert_matrix_gf(
    matrix: tuple[tuple[int, ...], ...],
) -> tuple[tuple[int, ...], ...]:
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise ValueError("matrix must be non-empty and square")

    augmented = [
        list(matrix[row]) + [1 if row == column else 0 for column in range(n)]
        for row in range(n)
    ]
    for column in range(n):
        pivot = next(
            (row for row in range(column, n) if augmented[row][column] != 0),
            None,
        )
        if pivot is None:
            raise ValueError("selected shards do not form an invertible matrix")
        if pivot != column:
            augmented[column], augmented[pivot] = (augmented[pivot], augmented[column])

        inverse = _gf_inv(augmented[column][column])
        augmented[column] = [_gf_mul(value, inverse) for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [
                    left ^ _gf_mul(factor, right)
                    for left, right in zip(
                        augmented[row], augmented[column], strict=True
                    )
                ]
    return tuple(tuple(row[n:]) for row in augmented)


@lru_cache(maxsize=128)
def _recovery_matrix(
    data_shards: int,
    total_shards: int,
    selected_indices: tuple[int, ...],
) -> tuple[tuple[int, ...], ...]:
    generator = _generator_matrix(data_shards, total_shards)
    selected = tuple(generator[index] for index in selected_indices)
    return _invert_matrix_gf(selected)
