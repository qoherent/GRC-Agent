# Digital Modulation & Constellation Objects in GRC

When searching for a 16-QAM constellation helper function to upgrade a modulation scheme (e.g., from QPSK to 16-QAM), you do not need to rely on native Python helpers or edit the modulator block directly. Instead, GNU Radio Companion uses a referenced `variable_constellation` object.

### How to Upgrade Modulation Schemes
To change a modulation scheme, perform this exact two-step update:
1. **Update the Constellation Object:** You must directly edit the `variable_constellation` block (which is often named `qpsk_obj` or similar). Use `update_params` to change its `type` parameter to `16qam`. Do not touch the modulator block. 
2. **Update the Data Source:** The `max` parameter in `analog_random_source_x` is an exclusive upper bound. For a 16-QAM alphabet, you must use `update_params` to set `max: 16` (do not set it to 15).
