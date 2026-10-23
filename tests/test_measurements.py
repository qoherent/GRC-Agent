import math

import numpy as np
import pytest

from grc_agent.measurements import analyze_samples, render_samples


def test_analytic_real_tone_metrics_and_frequency_units():
    count = 32
    bin_index = 5
    amplitude = 2.5
    samples = amplitude * np.cos(2 * np.pi * bin_index * np.arange(count) / count)

    result = analyze_samples(samples, sample_rate=3200)

    assert result["count"] == count
    assert result["finite_count"] == count
    assert result["nonfinite_count"] == 0
    assert result["mean_real"] == pytest.approx(0, abs=1e-14)
    assert result["mean_imag"] == 0
    assert result["rms_magnitude"] == pytest.approx(amplitude / np.sqrt(2))
    assert result["peak_magnitude"] == pytest.approx(amplitude)
    assert result["minimum"] == pytest.approx(-amplitude)
    assert result["maximum"] == pytest.approx(amplitude)
    assert result["dominant_bin_index"] == bin_index
    assert result["dominant_frequency"] == pytest.approx(bin_index * 100)
    assert result["dominant_frequency_unit"] == "Hz"
    assert result["frequency_resolution"] == pytest.approx(100)
    assert result["frequency_resolution_unit"] == "Hz"
    assert result["fft_window"] == "rectangular"
    assert result["fft_includes_dc"] is True
    assert result["dominant_frequency_is_bin_estimate"] is True


def test_dc_included_and_dominant_at_zero_frequency():
    result = analyze_samples(np.full(8, 3.0))

    assert result["dominant_bin_index"] == 0
    assert result["dominant_frequency"] == 0
    assert result["dominant_frequency_unit"] == "cycles/sample"
    assert result["spectral_energy_present"] is True
    assert result["frequency_resolution"] == pytest.approx(1 / 8)
    assert result["minimum"] == result["maximum"] == 3


def test_all_zero_samples_report_zero_metrics():
    result = analyze_samples(np.zeros(6))

    assert result["rms_magnitude"] == 0
    assert result["peak_magnitude"] == 0
    assert result["dominant_bin_index"] is None
    assert result["dominant_frequency"] is None
    assert result["dominant_bin_ambiguous"] is True
    assert result["spectral_energy_present"] is False
    assert result["dominant_frequency_is_bin_estimate"] is False


def test_int16_extreme_values_are_promoted_before_magnitude_arithmetic():
    samples = np.array([-32768, 32767], dtype=np.int16)
    expected_rms = math.sqrt((32768**2 + 32767**2) / 2)

    result = analyze_samples(samples)

    assert result["mean_real"] == pytest.approx(-0.5)
    assert result["rms_magnitude"] == pytest.approx(expected_rms)
    assert result["peak_magnitude"] == pytest.approx(32768)
    assert result["minimum"] == -32768
    assert result["maximum"] == 32767


def test_uint8_peak_and_rms_do_not_overflow():
    result = analyze_samples(np.array([255], dtype=np.uint8))

    assert result["mean_real"] == 255
    assert result["rms_magnitude"] == pytest.approx(math.sqrt(255**2))
    assert result["peak_magnitude"] == 255
    assert result["minimum"] == result["maximum"] == 255


def test_finite_values_with_nonfinite_derived_spectrum_are_refused():
    with pytest.raises(ValueError, match="nonfinite FFT"):
        analyze_samples([1e308, 1e308])


@pytest.mark.parametrize("samples", [[], np.array([], dtype=np.complex128)])
def test_empty_samples_are_refused(samples):
    with pytest.raises(ValueError, match="empty"):
        analyze_samples(samples)


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_nonfinite_samples_are_refused_without_metrics(bad_value):
    with pytest.raises(ValueError, match="nonfinite"):
        analyze_samples(np.array([1.0, bad_value]))


def test_negative_frequency_complex_tone():
    count = 24
    bin_index = 7
    samples = np.exp(-2j * np.pi * bin_index * np.arange(count) / count)

    result = analyze_samples(samples)

    assert result["is_complex"] is True
    assert result["dominant_frequency"] == pytest.approx(-bin_index / count)
    assert result["dominant_frequency_unit"] == "cycles/sample"
    assert result["mean_imag"] == pytest.approx(0, abs=1e-14)
    assert "minimum" not in result
    assert "maximum" not in result


def test_render_samples_returns_png_for_real_input():
    png = render_samples(np.array([0.0, 1.0, 0.0, -1.0]))

    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


def test_render_samples_returns_png_with_complex_plots():
    png = render_samples(np.exp(2j * np.pi * np.arange(8) / 8), sample_rate=8)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


@pytest.mark.parametrize("rate", [0, -1, np.inf, np.nan, True, "10"])
def test_invalid_sample_rate_is_refused(rate):
    with pytest.raises(ValueError, match="sample_rate"):
        analyze_samples([1, 2], sample_rate=rate)


@pytest.mark.parametrize("samples", [["1", "2"], [True, False], [[1, 2]]])
def test_invalid_sample_dtype_or_shape_is_refused(samples):
    with pytest.raises(ValueError):
        analyze_samples(samples)


def test_timedelta_dtype_is_not_accepted_as_numeric_samples():
    with pytest.raises(ValueError, match="numeric dtype"):
        analyze_samples(np.array([1, 2], dtype="timedelta64[s]"))
