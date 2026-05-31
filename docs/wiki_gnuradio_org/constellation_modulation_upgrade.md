---
title: 16-QAM Constellation Helper Function and Modulation Upgrade
tags: [16-QAM, constellation helper function, upgrade modulation, variable_constellation, qpsk_obj, random source, QPSK, 16QAM]
---

# Digital Modulation & Constellation Objects in GRC

*(Search Keywords: 16-QAM constellation helper function, upgrade modulation scheme, variable_constellation, QPSK to 16-QAM)*

In GNU Radio Companion, modulator blocks (like `digital_constellation_modulator`) do not define the constellation type directly. Instead, they reference a separate `variable_constellation` block via their `constellation` parameter.

### How to Upgrade Modulation Schemes
To change a modulation scheme (e.g., from QPSK to 16-QAM), you must NOT edit the modulator block. Instead, perform this exact two-step update:
1. **Update the Constellation Object:** You must directly edit the `variable_constellation` block (which is often named `qpsk_obj` or similar). Use `update_params` to change its `type` parameter to `16qam`. Do not touch the modulator block. 
2. **Update the Data Source:** The `max` parameter in `analog_random_source_x` is an exclusive upper bound. For a 16-QAM alphabet, you must use `update_params` to set `max: 16` (do not set it to 15).
