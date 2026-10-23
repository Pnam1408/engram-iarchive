import fast_poseidon


def main() -> None:
    result = fast_poseidon.hash_elements(["1", "2"])

    expected = (
        "7853200120776062878684798364095072458815029376092732009249414926327459813530"
    )

    assert result == expected, f"Unexpected Poseidon hash: {result}"
    print("Poseidon Rust backend: PASS")
    print(f"Hash([1, 2]) = {result}")


if __name__ == "__main__":
    main()