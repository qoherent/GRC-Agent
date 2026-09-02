# Verifying Flowgraph Behaviour With Probes

A flowgraph that starts without error has not been shown to work. Console output
alone reports failures, not measured values, so functional verification needs a
probe wired into the graph *before* the run.

## Wiring a probe so measurements reach the console

Probes must be in the graph before it is executed — they cannot be attached to a
run in progress. Two standard arrangements:

- **Rate measurement.** Connect `blocks_probe_rate` to the stream under test and
  wire its message output to `blocks_message_debug` on the `print` port. The
  measured rate is printed to the console as the flowgraph runs.
- **Power measurement.** Use `analog_probe_avg_mag_sqrd_x` on the stream, then poll
  it either with `variable_function_probe` or from an Embedded Python Block, and
  print the polled value.

Either way the measured value appears in the flowgraph's console output, which is
where a bounded run's results can be read after it completes.

## Why a probe rather than a GUI sink

QT GUI sinks render to a window a human watches; they produce no console output and
no artifact that survives the run. A probe plus `blocks_message_debug` turns a
behavioural question ("is this actually producing 48 kHz?") into a line of text,
which is what makes an automated or unattended check possible.
