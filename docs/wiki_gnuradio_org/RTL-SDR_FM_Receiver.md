# RTL-SDR FM Receiver
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types")
  5. [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types")
  6. [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits")
  7. [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors")
  8. [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters")

Creating and Modifying Python Blocks 
  1. [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block")
  2. [Python Block With Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors")
  3. [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing")
  4. [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags")

DSP Blocks 
  1. [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example")
  2. [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps")
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. RTL-SDR FM Receiver
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial describes how to receive broadcast commercial radio stations transmitting Frequency Modulated (FM) signals using the RTL-SDR receiver. 
The previous tutorial, [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files"), demonstrates how to read and write radio waveform captures as binary files. The next tutorial, [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver"), describes how to build a broadcast FM receiver using the Ettus Research B200/B205mini receiver. 
The following hardware is needed for this tutorial: 
  * RTL-based SDR (aka "RTL-SDR"); this flowgraph has been tested on the Nooelec NESDR SMArt, the Nooelec Nano Three, the RTL-SDR.com v3, and a generic RTL-SDR with a R820T RF front end. The RTL-SDR.com v4 only works if you've [loaded the v4 drivers](https://www.rtl-sdr.com/V4/).
  * An antenna. As this is tutorial is designed to receive and demodulate FM broadcast signals, which operate in the range of 88 - 108 MHz, a VHF antenna designed to operate in this range is ideal. The number and quality of FM broadcast signals you will be able to receive is related to the how well the antenna operates within that range, as well as the distance between your receive antenna and the FM broadcast towers.


Connect the antenna to the RTL-SDR, and plug the RTL-SDR into the USB port on your computer. 
## Contents
  * [1 RTL-SDR Basic Specifications](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#RTL-SDR_Basic_Specifications)
  * [2 Start a New Flowgraph](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Start_a_New_Flowgraph)
  * [3 Configure the RTL-SDR](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Configure_the_RTL-SDR)
  * [4 Add Time & Frequency Plots](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Add_Time_&_Frequency_Plots)
  * [5 FM Demodulator](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#FM_Demodulator)
  * [6 Diagnosing Overrun Problems](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Diagnosing_Overrun_Problems)
  * [7 Additional Feature - Volume Control](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Additional_Feature_-_Volume_Control)
  * [8 Stereo Version flowgraph](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Stereo_Version_flowgraph)
  * [9 Next Tutorial - Ettus Research B200/B205mini Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver#Next_Tutorial_-_Ettus_Research_B200/B205mini_Receiver)


## RTL-SDR Basic Specifications
A RTL-SDR is not a single make / model of software defined radio. Several manufacturers make such SDRs, including RTL-SDR Blog and Nooelec, among others. The RTL-SDR is so-called because it is based on [the Realtek RTL2832U chip](https://www.realtek.com/Product/Index?id=615). This chip provides for analog-to-digital conversion (ADC) and conversion from real to complex samples. Combined with a RF front end chip, such as the Elonics E4000 or the Rafael Micro R802T, R820T2 or R828D, the RTL-SDR provides the capability to capture signals up to roughly 2.5 MHz wide well into the UHF frequency range. 
Some standard specifications of RTL-SDRs are: 
  * Frequency Range: Depends on the specific RF front end chip. The Elonics E4000 specifies a frequency range of 64 - 1700 MHz, but will operate with reduced performance outside of this range. The R820T2 operates from 500 kHz - 1766 MHz. To see the specific RF front end chip, use the "rtl_test" command.
  * Digitizer: 8 bits
  * Sample Rates: 225 - 300 kHz and 900 kHz - 3.2 MHz. However, typically, sample rates above 2.56 MHz will see dropped samples as the RTL-SDR USB connection is not able to keep pace with sample production. Further, there are some sample rates above 2 MHz which may not be available due to the specific RF front end chip. For example, the Elonics E4000 [may have issues above 2 MHz](https://groups.google.com/g/ultra-cheap-sdr/c/r_BLWQ5C4mw).
  * RF Gain Values: Depends on the specific RF front end chip. The Rafael Micro-based chips (R820T, R820T2, R828D) have allowable RF gain values of 0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, and 49.6 dB. However, if provided a gain value within the upper and lower limits that does not match one of these values, the RTL-SDR will typically select the next allowable lower value. For example, if you attempt to set the gain to 5 dB, the RTL-SDR will set the gain to 3.7 dB (the next allowable lower value).


## Start a New Flowgraph
For this, you can reference [the "Your First Flowgraph" tutorial](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph"). This will step you through starting Gnu Radio Companion for the first time. If you already have opened Gnu Radio Companion and have created flowgraphs, in this case, you can select "File -> New -> QT GUI" (the default selection) to start a new flowgraph. There's also an icon in the upper, left that will do the same thing. Once you've opened this new flowgraph, follow the steps in the "Your First Flowgraph" link to setup the "Options" block and save the file. 
## Configure the RTL-SDR
Add the **Soapy RTLSDR Source** block to the flowgraph. Soapy [[1]](https://github.com/pothosware/SoapySDR) is a SDR support library which interfaces with different SDR hardware. 
[![](https://wiki.gnuradio.org/images/b/b7/RTL_SDR_FM_add_rtlsdr_block.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_add_rtlsdr_block.png)
The receiver needs to be configured with a proper sampling rate, center frequency and gain value. The RTL-SDR supports multiple sampling rates but in this tutorial the maximum sampling rate of 2.048 MHz is chosen. Update the _samp_rate_ variable with the value 2048000: 
[![](https://wiki.gnuradio.org/images/b/be/RTL_SDR_FM_set_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_set_samp_rate.png)
Notice that the sampling rate within the RTL-SDR block has been updated: 
[![](https://wiki.gnuradio.org/images/6/69/RTL_SDR_FM_rtlsdr_updated_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_rtlsdr_updated_samp_rate.png)
The RTL-SDR source block still has an undefined _freq_. The _freq_ variable needs to be given a frequency associated with a radio station. This tutorial uses the frequency allocations within the United State of America, therefore you will need to modify them according to the allocation for your country. Within the USA, the smallest frequency of a radio station is 87.9 MHz and the largest frequency is 107.9 MHz [[2]](https://en.wikipedia.org/wiki/FM_broadcasting_in_the_United_States), and each channel is separated by 200 kHz. 
Add two (2) **QT GUI Range** blocks into the flowgraph. The properties need to be changed in order to incorporate the frequency allocations for broadcast FM as well as the adjustable gain of the RTL-SDR. 
Open the properties of the first **QT GUI Range** block and enter the following values: 
  * ID: freq
  * Label: Center Frequency
  * Default Value: the frequency of your favorite FM broadcast station. If you don't know, use the start frequency [for your country](https://mytuner-radio.com/radio/worldwide-frequencies/). For example, [Rwanda](https://mytuner-radio.com/radio/country/rwanda-stations/frequency/fm) runs from 87 - 108 MHz. You can use 87e6 for the default value. For this tutorial, we're using 88.5e6.
  * Start: Use the start frequency of your country. This tutorial assumes the USA, which starts at 88 MHz. We're going to drop it down a little bit to 87.7e6.
  * Stop: 107.9e6
  * Step: Use the step size of your country. We're using 200 kHz (200e3), which is the USA separation.
  * GUI Hint: 0,0,1,10


NOTE: We're going to use the _GUI Hint_ properties for each of the **QT GUI Range** blocks so that they're at the top of the window created when you run the flowgraph. 
[![](https://wiki.gnuradio.org/images/a/a6/QT-GUI-Range-general-properties-freq-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:QT-GUI-Range-general-properties-freq-annotated.png)
Open the properties of the second **QT GUI Range** block and enter the following values: 
  * ID: rfGain
  * Label: RF Gain (dB)
  * Default Value: 10
  * Start: 0
  * Stop: 50
  * Step: 1
  * GUI Hint: 1,0,1,10


Many RTL-SDRs operate from the Rafael Micro R820T, R820T2 or R828D (RTL-SDR.com v4) RF chip. They use an amplifier that allow for a set of 29 values ranging from 0 - 49.6. The start, stop and step size listed here will get you pretty close to those values without having to enter each of the 29 values as a separate vector. Trust us. This is easier. 
[![](https://wiki.gnuradio.org/images/5/5a/QT-GUI-Range-general-properties-rfGain-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:QT-GUI-Range-general-properties-rfGain-annotated.png)
The _freq_ variable within the **Soapy RTLSDR Source** block is now defined. The flowgraph should look like the following: 
[![](https://wiki.gnuradio.org/images/b/be/RtlsdrFmReceiverSourceOnly.jpg)](https://wiki.gnuradio.org/index.php?title=File:RtlsdrFmReceiverSourceOnly.jpg)
Open the **Soapy RTLSDR** Source block properties. Navigate to the _RF Options_ and enter _rfGain_ within the _RF Gain_ window: 
[![](https://wiki.gnuradio.org/images/5/5e/Soapy-RTLSDR-Source-RF-Options-properties-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:Soapy-RTLSDR-Source-RF-Options-properties-annotated.png)
## Add Time & Frequency Plots
Drag in a **QT GUI Time Sink** and **QT GUI Frequency Sink** : 
Open the **QT GUI Frequency Sink** and enter _freq_ as the Center Frequency (Hz): 
[![](https://wiki.gnuradio.org/images/a/aa/RTL_SDR_FM_freq_sink_center_freq.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_freq_sink_center_freq.png)
Connect the **QT GUI Time Sink** and **QT GUI Frequency Sink** blocks: 
[![](https://wiki.gnuradio.org/images/1/1f/RtlsdrFmReceiverNoDemod.jpg)](https://wiki.gnuradio.org/index.php?title=File:RtlsdrFmReceiverNoDemod.jpg)
Run the flowgraph. A time plot and frequency plot will be displayed. This simple flowgraph represents a basic tuner application, such that dragging the slider bar or entering a frequency manually will retune the RTL-SDR and start producing sampled data. 
[![](https://wiki.gnuradio.org/images/thumb/6/6a/RTL-SDR-FM-Receiver-10-gain.png/500px-RTL-SDR-FM-Receiver-10-gain.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-10-gain.png)
Due to the nature of the RTL-SDR and how it presents data to Gnu Radio, a **QT GUI Time Sink** at the output of the RTL-SDR source block gives an indication of signal strength and potential overload. The image above, with the _RF Gain_ set to 10, shows the data in the time sink as relatively low amplitude. Depending on the antenna connected to your specific RTL-SDR, your time domain may be higher or lower amplitude. Further, your spectral display (the **QT GUI Frequency Sink**) will also appear differently. 
Adjusting the _RF Gain_ value higher adds gain to the RF front end of the RTL-SDR. This will boost the signal strength and increase the signal-to-noise ratio (SNR). For example, increasing the gain to 31 dB will increase the amplitude within the time sink, as shown below: 
[![](https://wiki.gnuradio.org/images/thumb/0/03/RTL-SDR-FM-Receiver-31-gain.png/500px-RTL-SDR-FM-Receiver-31-gain.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-31-gain.png)
Increasing the gain to where the amplitudes within the time sink are close to, but not surpassing, the top and bottom of the display (with limits set to +/-1, which is the default) is optimal for proper reception. 
## FM Demodulator
An **Audio Sink** block is needed to play the sound of a demodulated FM radio station. Drag in an **Audio Sink** block and open the properties. Notice there are a couple choices for sampling rates to choose from. Select **48 kHz** : 
[![](https://wiki.gnuradio.org/images/9/9a/RTL_SDR_FM_audio_sink_properties.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_audio_sink_properties.png)
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/5/5e/RTL_SDR_FM_flowgraph_with_audio_sink.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_flowgraph_with_audio_sink.png)
The question is how to get from the output of the RTL-SDR which is complex IQ sampled at 2.048 MHz to the input of the **Audio Sink** block which requires real samples at a sampling rate of 48 kHz? The rest of this tutorial will work backwards from the **Audio Sink** and establishing blocks and connections towards the output of the RTL-SDR. 
The next block that is needed is the an FM demodulator. Drag in the **WBFM Receive** block, which takes complex IQ as an input, demodulates the FM thereby producing real output samples and also performs a decimation. 
[![](https://wiki.gnuradio.org/images/a/a6/RTL_SDR_FM_add_wbfm_receive.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_add_wbfm_receive.png)
  
Recall from earlier in the tutorial that FM broadcast channels allocated 200 kHz, therefore we want to process as much of that bandwidth as possible with the FM demodulator. The **WBFM Receive** block can perform a decimation from a larger input sampling rate to the required **Audio Sink** input of 48 kHz. The decimation factor must be an integer, and 4*48 kHz = 192 kHz which is close to the total bandwidth of the frequency allocation. 
Add a **WBFM Receive** block, open the properties and enter in the quadrature rate of 192 kHz and an audio decimation of 4. Note that the quadrature rate must be evenly divisible by the audio decimation factor, and that the audio decimation must be an integer. 
[![](https://wiki.gnuradio.org/images/e/e9/RTL_SDR_FM_wbfm_receive_properties.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_wbfm_receive_properties.png)
  
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/d/d5/RTL_SDR_FM_flowgraph_with_WBFM.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_flowgraph_with_WBFM.png)
  
A sample rate change is needed to convert from the RTL-SDR output of 2.048 MHz to the WBFM input of 192 kHz. The required sampling rate change can be simplified as 192000/2048000 = 192/2048 = 3/32, a rational ratio. Therefore the **Rational Resampler** block can be used to implement the sample rate change. 
Drag in the **Rational Resampler** block and open the properties. Enter 3 for the interpolation and 32 for the decimation: 
[![](https://wiki.gnuradio.org/images/5/5f/RTL_SDR_FM_rational_resampler_properties.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_rational_resampler_properties.png)
The flowgraph is now complete and should look like the following. 
[![](https://wiki.gnuradio.org/images/thumb/6/6e/RTL-SDR-FM-Receiver-original.jpg/800px-RTL-SDR-FM-Receiver-original.jpg)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-original.jpg)
Run the flowgraph. The same GUI window that was displayed previously will appear but now audio should be playing through your computer. You can drag the bar at the top of the screen to tune to different channels. 
[![](https://wiki.gnuradio.org/images/thumb/f/f2/RTL-SDR-FM-Receiver-90M9CF.png/500px-RTL-SDR-FM-Receiver-90M9CF.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-90M9CF.png)
## Diagnosing Overrun Problems
When you run your flowgraph if you get a string of “aUaUaU” and the audio comes in and out periodically then you have probably entered a sample rate wrong somewhere along the way. Double check all of the sample rate values and interpolation and decimation rate changes. 
[![](https://wiki.gnuradio.org/images/a/a4/RTL_SDR_FM_sample_rate_overflows.png)](https://wiki.gnuradio.org/index.php?title=File:RTL_SDR_FM_sample_rate_overflows.png)
## Additional Feature - Volume Control
An additional you can add to this flowgraph is a volume control. Volume is controlled by the amplitude of the signal going into the [Audio Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink"). We'll add another [QT GUI Range](https://wiki.gnuradio.org/index.php?title=QT_GUI_Range "QT GUI Range") block as well as a [Multiply Const](https://wiki.gnuradio.org/index.php?title=Multiply_Const "Multiply Const") block to adjust the volume. Place the **Multiply Const** block between the **WBFM Receive** and **Audio Sink** blocks. Your flowgraph should be similar to this: 
[![](https://wiki.gnuradio.org/images/thumb/1/11/RTL-SDR-FM-Receiver-with-volume-control.jpg/800px-RTL-SDR-FM-Receiver-with-volume-control.jpg)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-with-volume-control.jpg)
Open the properties for the just-added **QT GUI Range** block and enter the following values: 
  * ID: volume
  * Label: Volume (dB)
  * Default Value: -6
  * Start: -100
  * Stop: 10
  * Step: 0.1
  * GUI Hint: 3,0,1,10


[![](https://wiki.gnuradio.org/images/thumb/1/17/RTL-SDR-FM-Receiver-volume-range-properties-annotated.png/600px-RTL-SDR-FM-Receiver-volume-range-properties-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-volume-range-properties-annotated.png)
Open the properties of the **Multiply Const** block and enter the following values: 
  * IO Type: float
  * Constant: 10**(volume/20)


[![](https://wiki.gnuradio.org/images/f/f0/RTL-SDR-FM-Receiver-Multiply-Const-properties-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-Multiply-Const-properties-annotated.png)
Run the flowgraph. Now the display will have a slider for the audio volume, as shown below. Using decibel (dB, a logarithmic scale) values as opposed to linear values (e.g. 0 - 100, for example) means that the audio adjustments will more closely follow human hearing, which is also logarithmic. Values less than 0 will lower the volume, while values greater than 0 will raise the volume. A value of 0 will be no change in the amplitude. NOTE: Amplitudes greater than 1 or less than -1 going into an **Audio Sink** block will typically cause audio distortion. 
[![](https://wiki.gnuradio.org/images/1/1b/RTL-SDR-FM-Receiver-display-with-volume-control.png)](https://wiki.gnuradio.org/index.php?title=File:RTL-SDR-FM-Receiver-display-with-volume-control.png)
## Stereo Version flowgraph
A stereo version flowgraph file can be found in [Media:RTL_SDR_rcv.grc](https://wiki.gnuradio.org/images/1/1c/RTL_SDR_rcv.grc "RTL SDR rcv.grc"). 
## Next Tutorial - Ettus Research B200/B205mini Receiver
The next tutorial, [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver"), describes how to build a broadcast FM receiver using the Ettus Research B200/B205mini receiver. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&oldid=15410](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&oldid=15410)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=RTL-SDR+FM+Receiver "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:RTL-SDR_FM_Receiver "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver)
  * [View source](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&action=history "Past revisions of this page \[alt-shift-h\]")


More
### Search
[](https://wiki.gnuradio.org/index.php?title=Main_Page "Visit the main page")
###  Navigation
  * [Wiki Home](https://wiki.gnuradio.org/index.php?title=Main_Page)
  * [GNU Radio Website](https://gnuradio.org)
  * [FAQ](https://wiki.gnuradio.org/index.php?title=FAQ)
  * [Get a Wiki Account](https://wiki.gnuradio.org/index.php?title=Wiki_account)


###  Guides
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Tutorials)
  * [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR)
  * [Contributing](https://wiki.gnuradio.org/index.php?title=Development)


###  Wiki Tools
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[alt-shift-r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[alt-shift-x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/RTL-SDR_FM_Receiver "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/RTL-SDR_FM_Receiver "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&oldid=15410 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver&action=info "More information about this page")


  * This page was last edited on 7 October 2025, at 01:58.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


