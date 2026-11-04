# engram/erasure_coding.py
from functools import lru_cache
from dataclasses import dataclass
from math import ceil

from reedsolo import RSCodec


@dataclass(frozen=True)
class EncodedShards:
    """Một bộ shard được tạo từ cùng một payload."""

    shards: tuple[bytes, ...]
    original_size: int
    data_shards: int
    total_shards: int


def encode(
    data: bytes,
    data_shards: int = 4,
    total_shards: int = 6,
) -> EncodedShards:
    """
    Mã hóa dữ liệu thành total_shards shard bằng Reed-Solomon.

    Mỗi byte tại cùng một vị trí trong data_shards shard dữ liệu
    được xem là một thông điệp RS riêng. Các byte parity tương ứng
    được phân phối vào những shard còn lại.

    Điều kiện: 1 <= data_shards < total_shards <= 255.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    if not (1 <= data_shards < total_shards <= 255):
        raise ValueError(
            "Require 1 <= data_shards < total_shards <= 255"
        )

    parity_shards = total_shards - data_shards
    rs = RSCodec(parity_shards)

    # Chia dữ liệu thành data_shards phần có kích thước bằng nhau.
    shard_size = max(1, ceil(len(data) / data_shards))
    padded = data.ljust(shard_size * data_shards, b"\x00")

    data_parts = [
        padded[i * shard_size : (i + 1) * shard_size]
        for i in range(data_shards)
    ]

    # Khởi tạo các shard đầu ra.
    output = [bytearray(shard_size) for _ in range(total_shards)]

    # Mã hóa RS theo từng vị trí byte trên các shard dữ liệu.
    for offset in range(shard_size):
        message = bytes(
            data_parts[i][offset]
            for i in range(data_shards)
        )

        codeword = bytes(rs.encode(message))

        for shard_index, value in enumerate(codeword):
            output[shard_index][offset] = value

    return EncodedShards(
        shards=tuple(bytes(shard) for shard in output),
        original_size=len(data),
        data_shards=data_shards,
        total_shards=total_shards,
    )

# GF(2^8), với đa thức nguyên thủy 0x11D.
# Khớp cấu hình mặc định của reedsolo.RSCodec đang sử dụng.
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
    """Nhân hai phần tử GF(256)."""
    if a == 0 or b == 0:
        return 0

    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _gf_inv(a: int) -> int:
    """Nghịch đảo nhân của phần tử khác 0 trong GF(256)."""
    if a == 0:
        raise ZeroDivisionError("Zero has no multiplicative inverse")

    return _GF_EXP[255 - _GF_LOG[a]]


# Bảng tra cứu: MUL_TABLE[a][b] = a * b trong GF(256).
# bytes.translate() sẽ dùng bảng này để nhân cả một shard.
_GF_MUL_TABLE = tuple(
    bytes(_gf_mul(a, b) for b in range(256))
    for a in range(256)
)


@lru_cache(maxsize=32)
def _generator_matrix(
    data_shards: int,
    total_shards: int,
) -> tuple[tuple[int, ...], ...]:
    """
    Suy ra ma trận mã hóa từ chính RSCodec.encode().

    G[i][j] là hệ số đóng góp của byte dữ liệu j
    vào byte tại shard i, ở cùng một offset.
    """
    parity_shards = total_shards - data_shards
    rs = RSCodec(parity_shards)

    # Mỗi vector cơ sở có đúng một phần tử bằng 1.
    encoded_basis = []

    for j in range(data_shards):
        basis = bytearray(data_shards)
        basis[j] = 1

        codeword = bytes(rs.encode(bytes(basis)))
        if len(codeword) != total_shards:
            raise RuntimeError("Unexpected Reed-Solomon codeword size")

        encoded_basis.append(codeword)

    # Chuyển các codeword cơ sở thành ma trận:
    # hàng = chỉ số shard, cột = chỉ số byte dữ liệu.
    return tuple(
        tuple(encoded_basis[j][i] for j in range(data_shards))
        for i in range(total_shards)
    )


def _invert_matrix_gf(
    matrix: tuple[tuple[int, ...], ...],
) -> tuple[tuple[int, ...], ...]:
    """Nghịch đảo ma trận vuông trên GF(256) bằng khử Gauss."""
    n = len(matrix)

    if n == 0 or any(len(row) != n for row in matrix):
        raise ValueError("Matrix must be non-empty and square")

    # Ghép ma trận đầu vào với ma trận đơn vị.
    aug = [
        list(matrix[i]) +
        [1 if i == j else 0 for j in range(n)]
        for i in range(n)
    ]

    for col in range(n):
        # Tìm pivot khác 0.
        pivot = next(
            (r for r in range(col, n) if aug[r][col] != 0),
            None,
        )

        if pivot is None:
            raise ValueError("Selected shards do not form an invertible matrix")

        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]

        # Chuẩn hóa pivot thành 1.
        pivot_inv = _gf_inv(aug[col][col])
        aug[col] = [
            _gf_mul(value, pivot_inv)
            for value in aug[col]
        ]

        # Khử các phần tử còn lại trong cột.
        for row in range(n):
            if row == col:
                continue

            factor = aug[row][col]
            if factor == 0:
                continue

            aug[row] = [
                a ^ _gf_mul(factor, b)
                for a, b in zip(aug[row], aug[col])
            ]

    return tuple(
        tuple(aug[i][n:])
        for i in range(n)
    )


@lru_cache(maxsize=128)
def _recovery_matrix(
    data_shards: int,
    total_shards: int,
    selected_indices: tuple[int, ...],
) -> tuple[tuple[int, ...], ...]:
    """Tính ma trận khôi phục cho tập shard đã chọn."""
    generator = _generator_matrix(data_shards, total_shards)

    selected_matrix = tuple(
        generator[index]
        for index in selected_indices
    )

    return _invert_matrix_gf(selected_matrix)

def decode(
    available_shards: dict[int, bytes],
    original_size: int,
    data_shards: int = 4,
    total_shards: int = 6,
) -> bytes:
    """
    Khôi phục dữ liệu từ các shard bằng ma trận GF(256).

    API và quy tắc chia dữ liệu tương thích với encode() hiện tại.
    Cần ít nhất data_shards shard khác nhau.
    """
    if not (1 <= data_shards < total_shards <= 255):
        raise ValueError(
            "Require 1 <= data_shards < total_shards <= 255"
        )

    if original_size < 0:
        raise ValueError("original_size must be non-negative")

    if len(available_shards) < data_shards:
        raise ValueError("Not enough shards to reconstruct data")

    for index, shard in available_shards.items():
        if not isinstance(index, int) or not (
            0 <= index < total_shards
        ):
            raise ValueError(f"Invalid shard index: {index}")

        if not isinstance(shard, bytes):
            raise TypeError("Each shard must be bytes")

    # Giữ thứ tự đầu vào để ánh xạ đúng các hàng của ma trận.
    selected = list(available_shards.items())[:data_shards]
    selected_indices = tuple(index for index, _ in selected)

    shard_size = len(selected[0][1])

    if any(len(shard) != shard_size for _, shard in available_shards.items()):
        raise ValueError("All shards must have the same size")

    if original_size > shard_size * data_shards:
        raise ValueError("original_size exceeds shard capacity")

    # Tính hoặc lấy từ cache ma trận nghịch đảo.
    recovery = _recovery_matrix(
        data_shards,
        total_shards,
        selected_indices,
    )

    selected_bytes = [shard for _, shard in selected]

    # Mỗi hàng recovery khôi phục một data shard.
    recovered_data = []

    for output_index in range(data_shards):
        output = bytearray(shard_size)
        coefficients = recovery[output_index]

        for input_index, coefficient in enumerate(coefficients):
            if coefficient == 0:
                continue

            source = selected_bytes[input_index]

            if coefficient == 1:
                # Nhân với 1 trong GF(256) không đổi giá trị.
                translated = source
            else:
                # Nhân toàn bộ shard bằng bảng tra cứu.
                translated = source.translate(
                    _GF_MUL_TABLE[coefficient]
                )

            # Cộng trong GF(256) chính là XOR.
            for offset, value in enumerate(translated):
                output[offset] ^= value

        recovered_data.append(bytes(output))

    reconstructed = b"".join(recovered_data)
    return reconstructed[:original_size]