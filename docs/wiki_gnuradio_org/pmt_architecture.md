# PMT Architecture and Immutability

## Overview

GNU Radio Polymorphic Types (PMTs) are a fundamental data interchange mechanism used
for metadata, control messages, and stream tags. PMTs are **strictly immutable** — once
a PMT object is created, its value cannot be modified.

## Immutability Property

All PMT types (booleans, integers, floats, complex numbers, strings, symbols,
vectors, uniform vectors, dictionaries, pairs, and lists) are immutable. This
design enables safe sharing of PMT data structures across threads without
synchronization overhead.

### Dictionary Immutability

PMT dictionaries (`pmt::dict`) are immutable. Functions that appear to modify a
dictionary actually create and return a **new** dictionary with the modification
applied:

```cpp
// This does NOT modify `original` in place
pmt::pmt_t original = pmt::make_dict();
pmt::pmt_t modified = pmt::dict_add(original, key, value);
// `original` is unchanged; `modified` is a new dictionary
```

To accumulate changes across multiple operations, you must reassign the
result:

```cpp
pmt::pmt_t d = pmt::make_dict();
d = pmt::dict_add(d, pmt::intern("frequency"), pmt::from_long(915000000));
d = pmt::dict_add(d, pmt::intern("gain"), pmt::from_double(42.0));
// d now contains both key-value pairs
```

Similarly, `pmt::dict_delete(d, key)` returns a new dictionary without the key;
`pmt::dict_update(d1, d2)` returns a new dictionary combining entries from both.

### Key Design Principle

**Adding a key to a PMT dictionary without mutating it in place** means assigning
the return value of `pmt::dict_add()`:

```cpp
d = pmt::dict_add(d, pmt::intern("new_key"), value);
```

Never call `pmt::dict_add(d, key, value)` without capturing the return value.

## Thread Safety

Because PMTs are immutable, they can be shared freely between threads. A
dictionary created in one block's work function can be passed to another block
via message ports without copying or locking. This is a core architectural
guarantee of the GNU Radio runtime.
