
use ark_bn254::Fr;
use ark_ff::PrimeField;
use light_poseidon::{Poseidon, PoseidonHasher};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::cell::RefCell;
use std::collections::HashMap;
use std::str::FromStr;

// Cache cấu hình Poseidon theo số lượng input, riêng cho từng thread.
thread_local! {
    static POSEIDON_CACHE: RefCell<HashMap<usize, Poseidon<Fr>>> =
        RefCell::new(HashMap::new());
}

#[pyfunction]
fn hash_elements(inputs: Vec<String>) -> PyResult<String> {
    if inputs.is_empty() {
        return Err(PyValueError::new_err(
            "inputs must not be empty",
        ));
    }

    // Chuyển input sang BN254 scalar field.
    let mut field_inputs = Vec::with_capacity(inputs.len());

    for value in &inputs {
        let parsed = Fr::from_str(value).map_err(|_| {
            PyValueError::new_err(
                format!("Invalid BN254 field element: {value}"),
            )
        })?;

        field_inputs.push(parsed);
    }

    let arity = field_inputs.len();

    // Khởi tạo cấu hình một lần cho mỗi arity trong thread hiện tại.
    let result = POSEIDON_CACHE.with(|cache| -> PyResult<Fr> {
        let mut cache = cache.borrow_mut();

        if !cache.contains_key(&arity) {
            let poseidon = Poseidon::<Fr>::new_circom(arity)
                .map_err(|e| {
                    PyValueError::new_err(
                        format!("Poseidon initialization failed: {e}"),
                    )
                })?;

            cache.insert(arity, poseidon);
        }

        let poseidon = cache
            .get_mut(&arity)
            .expect("Poseidon configuration should exist in cache");

        poseidon.hash(&field_inputs).map_err(|e| {
            PyValueError::new_err(
                format!("Poseidon hash failed: {e}"),
            )
        })
    })?;

    Ok(result.into_bigint().to_string())
}

#[pymodule]
fn fast_poseidon(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(hash_elements, m)?)?;
    Ok(())
}
