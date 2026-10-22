
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


def decode(
    available_shards: dict[int, bytes],
    original_size: int,
    data_shards: int = 4,
    total_shards: int = 6,
) -> bytes:
    """
    Khôi phục dữ liệu từ các shard còn lại.

    available_shards ánh xạ chỉ số shard -> bytes.
    Chỉ số hợp lệ nằm trong khoảng 0..total_shards-1.

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

    if len(set(available_shards)) != len(available_shards):
        raise ValueError("Duplicate shard indices")

    for index, shard in available_shards.items():
        if not isinstance(index, int) or not (
            0 <= index < total_shards
        ):
            raise ValueError(f"Invalid shard index: {index}")

        if not isinstance(shard, bytes):
            raise TypeError("Each shard must be bytes")

    shards = list(available_shards.items())
    shard_size = len(shards[0][1])

    if any(len(shard) != shard_size for _, shard in shards):
        raise ValueError("All shards must have the same size")

    if original_size > shard_size * data_shards:
        raise ValueError("original_size exceeds shard capacity")

    parity_shards = total_shards - data_shards
    rs = RSCodec(parity_shards)

    # Chọn k shard; các vị trí còn thiếu được khai báo là erasures.
    selected = shards[:data_shards]
    selected_indices = {index for index, _ in selected}
    missing_indices = [
        i for i in range(total_shards)
        if i not in selected_indices
    ]

    recovered_data = [
        bytearray(shard_size) for _ in range(data_shards)
    ]

    for offset in range(shard_size):
        codeword = bytearray(total_shards)

        for index, shard in selected:
            codeword[index] = shard[offset]

        # Reed-Solomon cần biết chính xác vị trí symbol bị thiếu.
        decoded_message = rs.decode(
            bytes(codeword),
            erase_pos=missing_indices,
        )[0]

        for i in range(data_shards):
            recovered_data[i][offset] = decoded_message[i]

    reconstructed = b"".join(bytes(part) for part in recovered_data)

    return reconstructed[:original_size]
