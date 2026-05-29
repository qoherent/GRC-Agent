# Type Conversion and Scaling in GNU Radio

## Float32 Standard Range

GNU Radio uses **IEEE-754 32-bit floating point** (`float32` / `float`) as its
standard sample data type. All signal processing blocks operating on `complex`
or `float` streams expect sample values in the normalized range:

**[-1.0, 1.0]**

This normalization ensures consistent behavior across all DSP blocks: filters,
multipliers, adders, FFTs, and AGC all operate on this normalized scale without
needing to know the original bit-width of the source.

## Integer Type Conversions

When converting between float32 and integer types, scaling factors are required:

### Float32 to Int16 (Short)

Signed 16-bit integer (`int16` / `short`) has range **[-32768, 32767]**. To
utilize the full dynamic range without clipping, multiply float32 samples by
**32767** (the maximum positive representable value):

```
scale_factor = 32767

int16_sample = float32_sample * 32767
```

This maps -1.0 → -32767 and 1.0 → 32767. The slight asymmetry (-32768 is
unused) prevents ambiguity at the negative extreme.

### Int16 (Short) to Float32

To convert back, divide by 32767:

```
float32_sample = int16_sample / 32767.0
```

## GNU Radio Block Usage

GNU Radio provides conversion blocks that handle this scaling automatically:

- **Short To Float** (`blocks_short_to_float`): Divides by the configured scale
  factor to normalize to [-1.0, 1.0].

- **Float To Short** (`blocks_float_to_short`): Multiplies by the configured
  scale factor to produce int16 samples.

The scale factor parameter on these blocks defaults to 1.0, meaning the user
must configure it to the appropriate value (typically 32767) for proper dynamic
range utilization.

## Other Type Conversions

| Type | Range | Scale Factor (float → integer) |
|------|-------|-------------------------------|
| Byte (uint8) | [0, 255] | 127.5 (offset: 127.5) |
| Short (int16) | [-32768, 32767] | 32767 |
| Int (int32) | [-2147483648, 2147483647] | 2147483647 |
| Char (int8) | [-128, 127] | 127 |

The general formula for integer conversion is:

```
integer_sample = round(float_sample * max_positive)
```

Where `max_positive` is the maximum positive value of the target integer type.
