# GNU Radio Native GRC-Facing Helpers & Utilities Reference

This reference report documents the Python helper functions, constellation objects, filter design helpers, and native CLI utilities that GNU Radio users frequently enter into GRC variable blocks and parameter fields. The information below is introspected programmatically from the live installation on this machine.

## 1. Constellation Generators (`gnuradio.digital`)

Constellation generator functions and classes are used to create the configuration objects required by blocks like *Constellation Decoder*, *Constellation Receiver*, or *Constellation Encoder*.

### 1.1 Python Constellation Generator Functions

These are standard Python helper functions that return constellation objects or points.

#### `digital.psk_constellation`

**Signature:** `digital.psk_constellation(m=4, mod_code='gray', differential=True)`

Creates a PSK constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `m` | `4` | Parameter for constellation generation |
| `mod_code` | `'gray'` | Parameter for constellation generation |
| `differential` | `True` | Parameter for constellation generation |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.psk_constellation(4, 'gray', True)`
- For an 8PSK: `digital.psk_constellation(8, 'natural', False)`

#### `digital.qam_constellation`

**Signature:** `digital.qam_constellation(constellation_points=16, differential=True, mod_code='none', large_ampls_to_corners=False)`

Creates a QAM constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `constellation_points` | `16` | Parameter for constellation generation |
| `differential` | `True` | Parameter for constellation generation |
| `mod_code` | `'none'` | Parameter for constellation generation |
| `large_ampls_to_corners` | `False` | Parameter for constellation generation |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.qam_constellation(16, True, 'none', False)`
- For a 64QAM: `digital.qam_constellation(64, False, 'gray')`

#### `digital.bpsk_constellation`

**Signature:** `digital.bpsk_constellation()`

Creates a BPSK constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.bpsk_constellation()`

#### `digital.qpsk_constellation`

**Signature:** `digital.qpsk_constellation(mod_code='gray')`

Creates a QPSK constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `mod_code` | `'gray'` | Parameter for constellation generation |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.qpsk_constellation('gray')`
- As a GRC Parameter String: `digital.qpsk_constellation('none')`

#### `digital.dqpsk_constellation`

**Signature:** `digital.dqpsk_constellation(mod_code='gray')`

Creates a DQPSK constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `mod_code` | `'gray'` | Parameter for constellation generation |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.dqpsk_constellation()`

#### `digital.dbpsk_constellation`

**Signature:** `digital.dbpsk_constellation()`

Creates a DBPSK constellation object.

| Parameter | Default Value | Description |
| --- | --- | --- |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.dbpsk_constellation()`

#### `digital.qam32_holeinside_constellation`

**Signature:** `digital.qam32_holeinside_constellation(large_ampls_to_corners=False)`

Creates a 32-QAM constellation with a hole inside.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `large_ampls_to_corners` | `False` | Parameter for constellation generation |

**GRC Parameter Examples:**
- As a GRC Parameter String: `digital.qam32_holeinside_constellation()`

### 1.2 GRC Constellation Classes

These are the underlying C++ / Pybind11 constellation classes that can be instantiated directly. Many of them represent specific pre-configured constellations.

#### `digital.constellation_bpsk`

Digital BPSK constellation class.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_bpsk()`

#### `digital.constellation_qpsk`

Digital QPSK constellation class.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_qpsk()`

#### `digital.constellation_8psk`

Digital 8PSK constellation class.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_8psk()`

#### `digital.constellation_8psk_natural`

Digital 8PSK constellation class with natural mapping.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_8psk_natural()`

#### `digital.constellation_dqpsk`

Digital DQPSK constellation class.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_dqpsk()`

#### `digital.constellation_16qam`

Digital 16QAM constellation class.

*Takes no arguments (pre-configured).*

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_16qam()`

#### `digital.constellation_calcdist`

General constellation that calculates Euclidean distance for hard decisions.

| Argument | Type | Description |
| --- | --- | --- |
| `constell` | `List[complex]` | List of constellation points (e.g. `[-1-1j, -1+1j, 1-1j, 1+1j]`) |
| `pre_diff_code` | `List[int]` | List of alphabet symbols before differential coding |
| `rotational_symmetry` | `int` | Number of rotations around the unit circle with the same representation |
| `dimensionality` | `int` | Number of dimensions to the constellation (usually 1) |
| `normalization` | `constellation.normalization` | Normalization method (`digital.constellation.AMPLITUDE_NORMALIZATION` or `POWER_NORMALIZATION`) |

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_calcdist([-1, 1], [0, 1], 2, 1)`

#### `digital.constellation_rect`

Rectangular digital constellation class.

| Argument | Type | Description |
| --- | --- | --- |
| `constell` | `List[complex]` | List of constellation points |
| `pre_diff_code` | `List[int]` | List of alphabet symbols |
| `rotational_symmetry` | `int` | Rotational symmetry order |
| `real_sectors` | `int` | Number of sectors along the real axis |
| `imag_sectors` | `int` | Number of sectors along the imaginary axis |
| `width_real_sectors` | `float` | Width of each real sector |
| `width_imag_sectors` | `float` | Width of each imaginary sector |
| `normalization` | `constellation.normalization` | Normalization method |

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_rect(digital.qam_constellation(16).points(), list(range(16)), 4, 4, 4, 1.0, 1.0)`

#### `digital.constellation_psk`

PSK constellation class where space is divided into pie slices.

| Argument | Type | Description |
| --- | --- | --- |
| `constell` | `List[complex]` | List of constellation points |
| `pre_diff_code` | `List[int]` | List of alphabet symbols |
| `n_sectors` | `int` | Number of pie slices (sectors) |

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_psk(digital.psk_constellation(4).points(), list(range(4)), 4)`

#### `digital.constellation_expl_rect`

Rectangular constellation class with explicit sector-to-point mapping.

| Argument | Type | Description |
| --- | --- | --- |
| `constellation` | `List[complex]` | List of constellation points |
| `pre_diff_code` | `List[int]` | List of alphabet symbols |
| `rotational_symmetry` | `int` | Rotational symmetry order |
| `real_sectors` | `int` | Number of sectors along the real axis |
| `imag_sectors` | `int` | Number of sectors along the imaginary axis |
| `width_real_sectors` | `float` | Width of each real sector |
| `width_imag_sectors` | `float` | Width of each imaginary sector |
| `sector_values` | `List[int]` | Explicit list mapping sector IDs to constellation points |

**GRC Parameter Examples:**
- GRC Variable Value: `digital.constellation_expl_rect()`

## 2. Filter Design Helpers

Filter design helpers are used to calculate the taps (coefficients) for filter blocks like *FIR Filter*, *Frequency Xlating FIR Filter*, and *Decimating FIR Filter*.

### 2.1 Window-Method Filter Design (`gnuradio.filter.firdes`)

These functions use the window method to calculate FIR filter taps.

#### `firdes.low_pass`

**Signature:** `low_pass(gain: float, sampling_freq: float, cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a low-pass FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.low_pass(1.0, samp_rate, 5000, 1000)`
- GRC Variable Taps: `firdes.low_pass(1.0, samp_rate, 5000, 1000, fft.window.WIN_HAMMING)`

#### `firdes.low_pass_2`

**Signature:** `low_pass_2(gain: float, sampling_freq: float, cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a low-pass FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps More attenuation --> more taps. The window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.low_pass_2(1.0, samp_rate, 5000, 1000, 60)`

#### `firdes.high_pass`

**Signature:** `high_pass(gain: float, sampling_freq: float, cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a high-pass FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. The window determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.high_pass(1.0, samp_rate, 1000, 200)`

#### `firdes.high_pass_2`

**Signature:** `high_pass_2(gain: float, sampling_freq: float, cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a high-pass FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps More attenuation --> more taps. The window determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.high_pass(1.0, samp_rate, 1000, 200)`

#### `firdes.band_pass`

**Signature:** `band_pass(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a band-pass FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. The window determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_pass(1.0, samp_rate, 1000, 4000, 500)`

#### `firdes.band_pass_2`

**Signature:** `band_pass_2(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a band-pass FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps. More attenuation --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_pass(1.0, samp_rate, 1000, 4000, 500)`

#### `firdes.band_reject`

**Signature:** `band_reject(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a band-reject FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_reject(...)`

#### `firdes.band_reject_2`

**Signature:** `band_reject_2(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[float]`

Use \"window method\" to design a band-reject FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps More attenuation --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_reject_2(...)`

#### `firdes.complex_band_pass`

**Signature:** `complex_band_pass(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[complex]`

Use the \"window method\" to design a complex band-pass FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. The window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_pass(1.0, samp_rate, 1000, 4000, 500)`

#### `firdes.complex_band_pass_2`

**Signature:** `complex_band_pass_2(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[complex]`

Use \"window method\" to design a complex band-pass FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps More attenuation --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.band_pass(1.0, samp_rate, 1000, 4000, 500)`

#### `firdes.complex_band_reject`

**Signature:** `complex_band_reject(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[complex]`

Use the \"window method\" to design a complex band-reject FIR filter. The normalized width of the transition band is what sets the number of taps required. Narrow --> more taps. The window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.complex_band_reject(...)`

#### `firdes.complex_band_reject_2`

**Signature:** `complex_band_reject_2(gain: float, sampling_freq: float, low_cutoff_freq: float, high_cutoff_freq: float, transition_width: float, attenuation_dB: float, window: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_HAMMING: 0>, param: float = 6.76) -> List[complex]`

Use \"window method\" to design a complex band-reject FIR filter. The normalized width of the transition band and the required stop band attenuation is what sets the number of taps required. Narrow --> more taps More attenuation --> more taps. Window type determines maximum attenuation and passband ripple.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `low_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `high_cutoff_freq` | `float` | `Required` | Filter design parameter |
| `transition_width` | `float` | `Required` | Filter design parameter |
| `attenuation_dB` | `float` | `Required` | Filter design parameter |
| `window` | `win_type` | `WIN_HAMMING` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.complex_band_reject_2(...)`

#### `firdes.root_raised_cosine`

**Signature:** `root_raised_cosine(gain: float, sampling_freq: float, symbol_rate: float, alpha: float, ntaps: int) -> List[float]`

design a Root Cosine FIR Filter (do we need a window?)

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `sampling_freq` | `float` | `Required` | Filter design parameter |
| `symbol_rate` | `float` | `Required` | Filter design parameter |
| `alpha` | `float` | `Required` | Filter design parameter |
| `ntaps` | `int` | `Required` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.root_raised_cosine(1.0, samp_rate, symbol_rate, 0.35, 11*sps)`

#### `firdes.gaussian`

**Signature:** `gaussian(gain: float, spb: float, bt: float, ntaps: int) -> List[float]`

design a Gaussian filter

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `gain` | `float` | `Required` | Filter design parameter |
| `spb` | `float` | `Required` | Filter design parameter |
| `bt` | `float` | `Required` | Filter design parameter |
| `ntaps` | `int` | `Required` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.gaussian(1.0, spb, 0.3, 11*spb)`

#### `firdes.hilbert`

**Signature:** `hilbert(ntaps: int = 19, windowtype: gnuradio.fft.fft_python.window.win_type = <win_type.WIN_RECTANGULAR: 3>, param: float = 6.76) -> List[float]`

design a Hilbert Transform Filter

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `ntaps` | `int` | `19` | Filter design parameter |
| `windowtype` | `win_type` | `<win_type.WIN_RECTANGULAR: 3>` | Filter design parameter |
| `param` | `float` | `6.76` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.hilbert(...)`

#### `firdes.window`

**Signature:** `window(type: gnuradio.fft.fft_python.window.win_type, ntaps: int, param: float) -> List[float]`

Calculates taps using the window method.

| Parameter | Type | Default Value | Description |
| --- | --- | --- | --- |
| `type` | `win_type` | `Required` | Filter design parameter |
| `ntaps` | `int` | `Required` | Filter design parameter |
| `param` | `float` | `Required` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `firdes.window(fft.window.WIN_BLACKMAN, 65, 0)`

### 2.2 Optimal (Parks-McClellan/Remez) Filter Design (`gnuradio.filter.optfir`)

These functions use the Parks-McClellan (Remez Exchange) algorithm to design optimal FIR filters. They are highly efficient for designing filters with strict passband ripple and stopband attenuation requirements.

#### `optfir.low_pass`

**Signature:** `optfir.low_pass(gain, Fs, freq1, freq2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal low pass FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq1` | `Required` | Filter design parameter |
| `freq2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.low_pass(1.0, samp_rate, 4000, 5000, 0.1, 60)`
- GRC Variable Taps: `optfir.low_pass(1.0, samp_rate, 4000, 5000, 0.1, 60, nextra_taps=4)`

#### `optfir.high_pass`

**Signature:** `optfir.high_pass(gain, Fs, freq1, freq2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal high pass FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq1` | `Required` | Filter design parameter |
| `freq2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.high_pass(1.0, samp_rate, 4000, 5000, 0.1, 60)`

#### `optfir.band_pass`

**Signature:** `optfir.band_pass(gain, Fs, freq_sb1, freq_pb1, freq_pb2, freq_sb2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal band pass FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq_sb1` | `Required` | Filter design parameter |
| `freq_pb1` | `Required` | Filter design parameter |
| `freq_pb2` | `Required` | Filter design parameter |
| `freq_sb2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.band_pass(1.0, samp_rate, 800, 1000, 4000, 4200, 0.1, 60)`

#### `optfir.band_reject`

**Signature:** `optfir.band_reject(gain, Fs, freq_pb1, freq_sb1, freq_sb2, freq_pb2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal band reject FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq_pb1` | `Required` | Filter design parameter |
| `freq_sb1` | `Required` | Filter design parameter |
| `freq_sb2` | `Required` | Filter design parameter |
| `freq_pb2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.band_reject(...)`

#### `optfir.complex_band_pass`

**Signature:** `optfir.complex_band_pass(gain, Fs, freq_sb1, freq_pb1, freq_pb2, freq_sb2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal complex band pass FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq_sb1` | `Required` | Filter design parameter |
| `freq_pb1` | `Required` | Filter design parameter |
| `freq_pb2` | `Required` | Filter design parameter |
| `freq_sb2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.complex_band_pass(...)`

#### `optfir.complex_band_reject`

**Signature:** `optfir.complex_band_reject(gain, Fs, freq_pb1, freq_sb1, freq_sb2, freq_pb2, passband_ripple_db, stopband_atten_db, nextra_taps=2)`

Designs an optimal complex band reject FIR filter using the Remez algorithm.

| Parameter | Default Value | Description |
| --- | --- | --- |
| `gain` | `Required` | Filter design parameter |
| `Fs` | `Required` | Filter design parameter |
| `freq_pb1` | `Required` | Filter design parameter |
| `freq_sb1` | `Required` | Filter design parameter |
| `freq_sb2` | `Required` | Filter design parameter |
| `freq_pb2` | `Required` | Filter design parameter |
| `passband_ripple_db` | `Required` | Filter design parameter |
| `stopband_atten_db` | `Required` | Filter design parameter |
| `nextra_taps` | `2` | Filter design parameter |

**GRC Parameter Examples:**
- GRC Variable Taps: `optfir.complex_band_reject(...)`

### 2.3 GRC-Facing Window Types (`gnuradio.fft.window`)

When using the window method, the window type is specified using the enum values inside `gnuradio.fft.window` (also re-exported as `gnuradio.filter.window`):

| Window Type Enum | Description |
| --- | --- |
| `fft.window.WIN_BARTLETT` | Window type constant |
| `fft.window.WIN_BLACKMAN` | Window type constant |
| `fft.window.WIN_BLACKMAN_HARRIS` | Window type constant |
| `fft.window.WIN_BLACKMAN_NUTTALL` | Window type constant |
| `fft.window.WIN_BLACKMAN_hARRIS` | Window type constant |
| `fft.window.WIN_EXPONENTIAL` | Window type constant |
| `fft.window.WIN_FLATTOP` | Window type constant |
| `fft.window.WIN_GAUSSIAN` | Window type constant |
| `fft.window.WIN_HAMMING` | Window type constant |
| `fft.window.WIN_HANN` | Window type constant |
| `fft.window.WIN_HANNING` | Window type constant |
| `fft.window.WIN_KAISER` | Window type constant |
| `fft.window.WIN_NUTTALL` | Window type constant |
| `fft.window.WIN_NUTTALL_CFD` | Window type constant |
| `fft.window.WIN_PARZEN` | Window type constant |
| `fft.window.WIN_RECTANGULAR` | Window type constant |
| `fft.window.WIN_RIEMANN` | Window type constant |
| `fft.window.WIN_TUKEY` | Window type constant |
| `fft.window.WIN_WELCH` | Window type constant |

## 3. GNU Radio CLI Utilities

These command-line utilities are part of the installed GNU Radio framework and are used for compiling, designing, or managing GNU Radio applications.

### `grcc`

**Command:** `grcc --help`

```
usage: grcc [-h] [-o DIR] [-u] [-r] GRC_FILE [GRC_FILE ...]

Compile a GRC file (.grc) into a GNU Radio Python program and run it.

positional arguments:
  GRC_FILE              .grc file to compile

options:
  -h, --help            show this help message and exit
  -o DIR, --output DIR  Output directory for compiled program [default=.]
  -u, --user-lib-dir    Output to default hier_block library (overwrites -o)
  -r, --run             Run the program after compiling [default=False]
```

### `gr_modtool`

**Command:** `gr_modtool --help`

```
Usage: gr_modtool [OPTIONS] COMMAND [ARGS]...

  A tool for editing GNU Radio out-of-tree modules.

Options:
  --help  Show this message and exit.

Commands:
  add       Adds a block to the out-of-tree module.
  bind      Generate Python bindings for GR block
  disable   Disable selected block in module.
  info      Return information about a given module
  makeyaml  Generate YAML files for GRC block bindings.
  newmod    Create new empty module, use add to add blocks.
  rename    Rename a block inside a module.
  rm        Remove a block from a module.
  update    Update the grc bindings for a block

  Manipulate with GNU Radio modules source code tree. Call it without options
  to run specified command interactively
```

### `gr_filter_design`

**Command:** `gr_filter_design --help`

```
Usage: gr_filter_design: [options] (input_filename)

Options:
  -h, --help  show this help message and exit
```

### `gnuradio-config-info`

**Command:** `gnuradio-config-info --help`

```
Program options: gnuradio-config-info [options]:
  -h [ --help ]         print help message
  --print-all           print all information
  --prefix              print GNU Radio installation prefix
  --sysconfdir          print GNU Radio system configuration directory
  --prefsdir            print GNU Radio preferences directory
  --userprefsdir        print GNU Radio user preferences directory
  --prefs               print GNU Radio preferences
  --builddate           print GNU Radio build date (RFC2822 format)
  --enabled-components  print GNU Radio build time enabled components
  --cc                  print GNU Radio C compiler version
  --cxx                 print GNU Radio C++ compiler version
  --cflags              print GNU Radio CFLAGS
  -v [ --version ]      print GNU Radio version
  --pybind              print pybind11 version used in this build
```