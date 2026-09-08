# Pushbutton IQ Recorder with descriptive filenames



## Application
When doing field work, capturing Raw I/Q for post processing is a helpful way to get the best signal quality of a capture while spending minimal time in a non-lab environment. Changing filenames manually for every capture can be laborious and subject to user error. Furthermore, starting and stopping a flowgraph can be tricky if we are tuning the SDR frontend parameters to find optimal level/frequency/sample rate. If a non-descriptive filename is used such as `capture_file.cfile` it does nothing to describe the situation to the user during the analysis stage as it doesn't include any information about the RF samples we've captured. This is critical to the post-analysis process.
  * What was the sample rate?
  * What was the Center Frequency?
  * When was it recorded?
  * What gain setting did we use?
  * What were we even trying to capture?

These are all questions we ask after the fact, especially if a lot of time has gone by since we did the recordings.
Including those pieces of information in the filename is one way to address this is.


## Assumptions
  * GNURadio 3.10+
  * directory `/home/<username>/data/iq_captures` is present on filesystem (the flowgraph will automatically find your `/home/<username>` path).

## Goals
#### Create a File Sink with Dynamic Information in the Filename
  * timestamp of recording
  * radio parameters
  * User Input Note for clarity

#### Only Record the file on an User Input Trigger
  * Define User Input as Momentary Switch in GUI
  * Set Conditional Statement in File Sink Block

## Content
The flowgraph for this tutorial is shown below along with the GRC file needed if you would like to test it out. This flowgraph is intended for use by anyone with GNURadio 3.10+ installed. It does not use an actual SDR frontend which allows users to test without hardware, but that also means that the values for center frequency and gain are merely representative so, when changed at runtime they won't be reflected in the synthetic spectrum when running the flowgraph. The aim of this tutorial is merely to demonstrate the mechanism. For an example that uses real SDR hardware, try <https://github.com/muaddib1984/wavetrap>
[](https://wiki.gnuradio.org/index.php?title=File:Pushbutton_iq_recorder_whole_graph.png)
[Pushbutton IQ Recorder](https://wiki.gnuradio.org/images/5/5d/Iq_recorder_tutorial.grc "Iq recorder tutorial.grc")
## Create Synthetic Spectrum for the Flowgraph
This screenshot shows the blocks used to generate some synthetic spectrum with intermittent narrowband carriers:
[](https://wiki.gnuradio.org/index.php?title=File:Pushbutton_synth_signal.png)
This is what the simulated spectrum looks like when running in GNURadio:
[](https://wiki.gnuradio.org/index.php?title=File:Synth_spectrum_plot.gif)
## Create a File Sink with Dynamic Information in the Filename
In the following example we will: Use some Pythonic syntax to leverage the Runtime Callbacks in our flowgraph and allow the filenames to change dynamically based on timestamp and radio parameters.
We will need to import the appropriate modules to get the user's home directory and create a date/timestamp
[](https://wiki.gnuradio.org/index.php?title=File:Variables_and_imports.png)
The filesink contains the filename variable and appends the properly formatted date/timestamp. Since we may want to trigger multiple recordings after starting the flowgraph, the date/timestamp needs to be generated each time the user triggers a recording, so the syntax must be expressed inside block. If we didn't do this, the date/timestamp would only be set once during the initial setup of the flowgraph.
#### Filepath
Then we will use two different variable blocks to define first, the top-level directory (`/home/<username>`) and second, the appropriate subdirectory `data/iq_captures` for the recordings. This can be changed to your preference.
[](https://wiki.gnuradio.org/index.php?title=File:Top_level_variable.png) [](https://wiki.gnuradio.org/index.php?title=File:Subdir_variable.png)
#### SDR Frontend Information
We then create the first part of the filename which contains the SDR Frontend parameters and a user note to describe the capture. These will be updated at runtime in the filename if they are changed.
[](https://wiki.gnuradio.org/index.php?title=File:Filename_variable.png)
This section of the flowgraph is shown here:
#### Timestamps
If a different format for timestamps is needed the syntax can be modified to suit your needs. The current format is:
`time.time()).strftime('%Y_%m_%d_%H_%M_%S')`
This can be tested in a python interactive terminal and adjusted to preference. The necessary code to do this is:

```
import time
from datetime import datetime
str(datetime.fromtimestamp(time.time()).strftime('%Y_%m_%d_%H_%M_%S'))

```



#### Full Filename and Path
The below python syntax is put in the "file" parameter of the file sink block. It includes the radio parameters with the date/timestamp. This is incomplete however, as it would record constantly from the time the flowgraph starts. When recording raw IQ we need to be mindful of disk space as raw IQ can take up space fast. In the next section we will show how to trigger the recording with a momentary pushbutton as a safety for our disk space and to keep our recordings limited to only the signals we want to record.
`filename+str(datetime.fromtimestamp(time.time()).strftime('%Y_%m_%d_%H_%M_%S'))+".cfile"`
In our current example, using the initial parameters, the filename would be: "RECORDING_NOTE_915000000Hz_1000000sps_50dB_2023_01_04_01_02_39.cfile"


#### User Input Note to Describe the Capture
  * We use the QT Entry Widget block to put an editable text field in our flowgraph's GUI
  * NOTE: once the text is changed, the user must hit the ENTER key to update the note

[](https://wiki.gnuradio.org/index.php?title=File:Entry_widget_parameters.png)

Example:
## Only Trigger Recording on User Input
Using python conditional statement, we can send the I/Q samples to `/dev/null` until the record button is pressed (and held). When the record button is pressed, the I/Q samples will begin streaming to a file with a timestamp and radio parameters of the flowgraph's state at the time the button was pressed. When released it will send the samples back to `/dev/null`
[](https://wiki.gnuradio.org/index.php?title=File:Momentary_record_button_parameters.png)
We will also add a QT GUI LED Indicator to turn red when we are recording to disk, it will be green when sending samples to `/dev/null`.
[](https://wiki.gnuradio.org/index.php?title=File:LED_parameters.png)
#### /dev/Null or Filename with Python Conditional Statement
  * We will send the IQ samples to `/dev/null` until the trigger event occurs, at that time the expression will be evaluated to switch the path to the predefined path from the flowgraph.

Below is the full python expression to insert in the 'filename' parameter of the file sink block. Note that we simply add a conditional at the end, which is based on the momentary pushbutton's state.
`filename+str(datetime.fromtimestamp(time.time()).strftime('%Y_%m_%d_%H_%M_%S'))+".cfile" if rec_button == 1 else "/dev/null"`
[](https://wiki.gnuradio.org/index.php?title=File:File_sink_parameters.png)
Here is a brief demo of the flowgraph in action showing the subdirectory adding new timestamped files as we click and hold the momentary pushbutton:
[](https://wiki.gnuradio.org/index.php?title=File:Pushbutton_iq_demo.gif)
NOTE: we use the command `watch ls -l` in the subdirectory to show the updating file names
## Prerequisites
  * [Intro to GR usage: GRC and flowgraphs](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")
