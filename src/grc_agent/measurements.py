"""Numerical measurements and plots for finite one-dimensional sample arrays.

Spectra use the unnormalized discrete Fourier transform with a rectangular
window (the samples are used as supplied). DC is included. The reported
dominant frequency is the frequency of the largest-magnitude FFT bin, not an
interpolated estimate and not an assertion about signal correctness.
"""

from __future__ import annotations

from io import BytesIO
from numbers import Real

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def _prepare_samples(samples, sample_rate: float | None) -> tuple[np.ndarray, float | None]:
    """Validate the shared input contract and return samples and optional rate."""
    values = np.asarray(samples)
    if values.ndim != 1:
        raise ValueError("samples must be a one-dimensional real or complex numeric array")
    if values.dtype.kind not in "iufc":
        raise ValueError("samples must have a real or complex numeric dtype")
    if values.size == 0:
        raise ValueError("samples must not be empty")

    finite = np.isfinite(values)
    finite_count = int(np.count_nonzero(finite))
    nonfinite_count = int(values.size - finite_count)
    if nonfinite_count:
        raise ValueError(
            f"samples contain {nonfinite_count} nonfinite value(s); "
            f"{finite_count} finite value(s)"
        )

    promoted = np.asarray(
        values, dtype=np.complex128 if np.iscomplexobj(values) else np.float64
    )
    if not np.all(np.isfinite(promoted)):
        raise ValueError("sample values cannot be represented as finite float64/complex128 values")

    if sample_rate is not None:
        if isinstance(sample_rate, (bool, np.bool_)) or not isinstance(sample_rate, Real):
            raise ValueError("sample_rate must be a finite positive number")
        rate = float(sample_rate)
        if not np.isfinite(rate) or rate <= 0:
            raise ValueError("sample_rate must be a finite positive number")
    else:
        rate = None
    return promoted, rate


def _spectrum(values: np.ndarray, sample_rate: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Return raw FFT magnitudes and their matching frequency coordinates."""
    count = values.size
    with np.errstate(over="ignore", invalid="ignore"):
        if np.iscomplexobj(values):
            transform = np.fft.fft(values)
            frequencies = np.fft.fftfreq(count)
        else:
            transform = np.fft.rfft(values)
            frequencies = np.fft.rfftfreq(count)
        magnitudes = np.abs(transform)
    if sample_rate is not None:
        frequencies = frequencies * sample_rate
    return magnitudes, frequencies


def analyze_samples(samples, sample_rate: float | None = None) -> dict:
    """Return descriptive metrics for finite one-dimensional numeric samples.

    The dominant bin is selected from the unnormalized FFT using a rectangular
    window and including DC. Its frequency is a bin estimate only. Frequencies
    and resolution are in cycles/sample when ``sample_rate`` is omitted, and
    in Hz when it is supplied. Empty and nonfinite input raises ``ValueError``.
    """
    values, rate = _prepare_samples(samples, sample_rate)
    count = int(values.size)
    value_magnitudes = np.abs(values)
    scale = float(np.max(value_magnitudes))
    if scale == 0:
        mean = 0j if np.iscomplexobj(values) else 0.0
        rms_magnitude = 0.0
    else:
        mean = np.mean(values / scale) * scale
        rms_magnitude = scale * float(np.sqrt(np.mean((value_magnitudes / scale) ** 2)))

    magnitudes, frequencies = _spectrum(values, rate)
    if not np.all(np.isfinite(magnitudes)):
        raise ValueError("sample values produce nonfinite FFT metrics")
    has_spectral_energy = bool(np.any(magnitudes > 0))
    dominant_bin = int(np.argmax(magnitudes)) if has_spectral_energy else None
    result = {
        "count": count,
        "finite_count": count,
        "nonfinite_count": 0,
        "is_complex": bool(np.iscomplexobj(values)),
        "mean_real": float(np.real(mean)),
        "mean_imag": float(np.imag(mean)),
        "rms_magnitude": rms_magnitude,
        "peak_magnitude": scale,
        "dominant_bin_index": dominant_bin,
        "dominant_frequency": (
            float(frequencies[dominant_bin]) if dominant_bin is not None else None
        ),
        "dominant_frequency_unit": "Hz" if rate is not None else "cycles/sample",
        "frequency_resolution": float((rate if rate is not None else 1.0) / count),
        "frequency_resolution_unit": "Hz" if rate is not None else "cycles/sample",
        "spectral_energy_present": has_spectral_energy,
        "dominant_bin_ambiguous": not has_spectral_energy,
        "fft_window": "rectangular",
        "fft_includes_dc": True,
        "dominant_frequency_is_bin_estimate": has_spectral_energy,
    }
    if not np.iscomplexobj(values):
        result["minimum"] = float(np.min(values))
        result["maximum"] = float(np.max(values))
    for key, metric in result.items():
        if isinstance(metric, float) and not np.isfinite(metric):
            raise ValueError(f"sample values produce a nonfinite {key} metric")
    return result


def render_samples(samples, sample_rate: float | None = None) -> bytes:
    """Render time-domain, FFT magnitude, and (for complex input) I/Q plots.

    Every supplied sample is plotted; no decimation is performed. The spectrum
    uses the same raw, rectangular-window FFT convention as ``analyze_samples``.
    The PNG is rendered with a local Agg canvas and does not use pyplot state.
    """
    values, rate = _prepare_samples(samples, sample_rate)
    count = values.size
    complex_input = bool(np.iscomplexobj(values))
    magnitudes, frequencies = _spectrum(values, rate)
    if not np.all(np.isfinite(magnitudes)):
        raise ValueError("sample values produce a nonfinite FFT spectrum")
    if complex_input:
        order = np.fft.fftshift(np.fft.fftfreq(count))
        if rate is not None:
            order = order * rate
        magnitudes = np.fft.fftshift(magnitudes)
        frequencies = order

    frequency_unit = "Hz" if rate is not None else "cycles/sample"
    time_values = np.arange(count) / rate if rate is not None else np.arange(count)
    time_unit = "s" if rate is not None else "sample index"
    figure = Figure(figsize=(9, 7 if complex_input else 5), constrained_layout=True)
    axes = figure.subplots(3 if complex_input else 2, 1)
    if complex_input:
        time_axis, spectrum_axis, constellation_axis = axes
        time_axis.plot(time_values, np.real(values), label="Real")
        time_axis.plot(time_values, np.imag(values), label="Imaginary")
        time_axis.legend()
        constellation_axis.scatter(np.real(values), np.imag(values), s=12)
        constellation_axis.set_xlabel("Real")
        constellation_axis.set_ylabel("Imaginary")
        constellation_axis.set_title("Constellation")
    else:
        time_axis, spectrum_axis = axes
        time_axis.plot(time_values, values)

    time_axis.set_xlabel(time_unit)
    time_axis.set_ylabel("Amplitude")
    time_axis.set_title("Time domain")
    spectrum_axis.plot(frequencies, magnitudes)
    spectrum_axis.set_xlabel(f"Frequency ({frequency_unit})")
    spectrum_axis.set_ylabel("FFT magnitude (raw)")
    spectrum_axis.set_title("Magnitude spectrum (rectangular window; DC included)")

    canvas = FigureCanvasAgg(figure)
    output = BytesIO()
    canvas.print_png(output)
    figure.clear()
    return output.getvalue()
