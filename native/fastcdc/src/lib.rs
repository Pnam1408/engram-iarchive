use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Return FastCDC v2020 chunk boundaries as (offset, length) pairs.
///
/// The Python layer computes hashes and constructs its own stable metadata
/// records; this native layer is intentionally limited to boundary detection.
#[pyfunction]
fn chunk_boundaries(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    min_size: u32,
    avg_size: u32,
    max_size: u32,
) -> PyResult<Vec<(usize, usize)>> {
    if min_size < 64 {
        return Err(PyValueError::new_err("min_size must be at least 64 bytes"));
    }
    if min_size > avg_size || avg_size > max_size {
        return Err(PyValueError::new_err(
            "chunk sizes must satisfy min_size <= avg_size <= max_size",
        ));
    }
    if !avg_size.is_power_of_two() {
        return Err(PyValueError::new_err("avg_size must be a power of two"));
    }

    let owned_data = data.as_bytes().to_vec();
    if owned_data.is_empty() {
        return Ok(Vec::new());
    }
    if owned_data.len() > u32::MAX as usize {
        return Err(PyValueError::new_err(
            "fastcdc-rs v2020 currently accepts inputs up to 4 GiB per call",
        ));
    }

    let boundaries = py.detach(move || {
        fastcdc::v2020::FastCDC::new(
            &owned_data,
            min_size as usize,
            avg_size as usize,
            max_size as usize,
        )
        .map(|chunk| (chunk.offset as usize, chunk.length as usize))
        .collect::<Vec<_>>()
    });

    Ok(boundaries)
}

#[pymodule]
fn _fastcdc(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chunk_boundaries, m)?)?;
    Ok(())
}
