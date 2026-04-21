# File transfer using Packet and BPSK
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#searchInput)
**NOTE: This tutorial does not contain any Forward Error Correction, flow control, or any other methods one would expect from something like TCP/IP.**
**Please leave comments in the Discussion tab above, or on the GNU Radio General Chat channel.** See [Chat](https://wiki.gnuradio.org/index.php?title=Chat "Chat").
  
This tutorial demonstrates file transfer from one computer to another using packet communications with BPSK modulation. A gradual, iterative approach is used throughout the tutorial. This step-by-step method reflects a realistic workflow for developing GNU Radio flowgraphs. Many developers fall into the trap of attempting to solve all problems at once, making it difficult to diagnose and fix problems. This tutorial guides the reader in avoiding those common pitfalls. As a prerequisite, the following tutorials must be studied before beginning this one: 
  * [QPSK Mod and Demod](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod "QPSK Mod and Demod")
  * [Simulation Example: BPSK Demodulation](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation "Simulation example: BPSK Demodulation")
  * [Packet Communications](https://wiki.gnuradio.org/index.php?title=Packet_Communications "Packet Communications")


The QPSK Mod and Demod tutorial provides a detailed overview of GNU Radio modulation and demodulation using QPSK and is highly recommended. The BPSK Demodulation tutorial presents a similar tutorial for BPSK and is less extensive because many concepts overlap with the QPSK tutorial. At the conclusion of both tutorials, example flowgraphs are available for download. Armed with enough knowledge to be dangerous, beginners often assume these flowgraphs are sufficient for robust communications. A common mistake is attempting to implement file transfer using the following steps: 
  1. Take the final BPSK demodulation flowgraph
  2. Replace the random source with a file source
  3. Pack the bits on the receiver chain and add a file sink


This approach fails because modulation and demodulation operate only at the bitstream level. There is no inherent mechanism to determine where packets begin or end, nor whether the received bits are correct. Consequently, the received data is corrupted by incorrect byte alignment and initialization effects. These issues are addressed through framing, commonly referred to as packet communications. The Packet Communications tutorial provides a general flowgraph for any modulation scheme. This tutorial begins with a naive flowgraph based on these tutorials. Incremental improvements are then applied to achieve file transfer for real software-defined radios. 
## Contents
  * [1 Stage 1: Initial Packet Communications Flowgraph](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_1:_Initial_Packet_Communications_Flowgraph)
  * [2 Stage 2: Adding Preamble and Postamble](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_2:_Adding_Preamble_and_Postamble)
  * [3 Stage 3: Adjusting Transmit Amplitude and Symbol Sync](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_3:_Adjusting_Transmit_Amplitude_and_Symbol_Sync)
  * [4 Stage 4: Channel Simulation](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_4:_Channel_Simulation)
  * [5 Stage 5: Adding File Transfer Blocks](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_5:_Adding_File_Transfer_Blocks)
    * [5.1 Test File Creation](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Test_File_Creation)
      * [5.1.1 File Creation with Python Snippet (GNU Radio versions 3.10.5.1 and later)](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#File_Creation_with_Python_Snippet_\(GNU_Radio_versions_3.10.5.1_and_later\))
      * [5.1.2 Legacy GNU Radio (GNU Radio versions earlier than 3.10.5.1)](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Legacy_GNU_Radio_\(GNU_Radio_versions_earlier_than_3.10.5.1\))
    * [5.2 File Source and Sink](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#File_Source_and_Sink)
    * [5.3 Increased Preamble Size](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Increased_Preamble_Size)
    * [5.4 Test Results](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Test_Results)
  * [6 Stage 6: Single SDR Loopback Test](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_6:_Single_SDR_Loopback_Test)
    * [6.1 Setting SDR Parameters](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Setting_SDR_Parameters)
      * [6.1.1 Sample Rate](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Sample_Rate)
      * [6.1.2 Frequency](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Frequency)
      * [6.1.3 Transmit and Receive Gains](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Transmit_and_Receive_Gains)
    * [6.2 File Transfer](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#File_Transfer)
    * [6.3 Troubleshooting](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Troubleshooting)
  * [7 Stage 7: Separate Radios for Transmit and Receive](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Stage_7:_Separate_Radios_for_Transmit_and_Receive)
    * [7.1 Setting USRP and ADALM-PLUTO Parameters](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Setting_USRP_and_ADALM-PLUTO_Parameters)
      * [7.1.1 Frequency](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Frequency_2)
      * [7.1.2 Transmit and Receive Gains](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Transmit_and_Receive_Gains_2)
      * [7.1.3 File Transfer](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#File_Transfer_2)
    * [7.2 Receiving with RTL-SDR](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Receiving_with_RTL-SDR)
      * [7.2.1 Frequency](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Frequency_3)
      * [7.2.2 Transmit and Receive Gains](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Transmit_and_Receive_Gains_3)
      * [7.2.3 File Transfer](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#File_Transfer_3)
  * [8 Over-the-air Transmission](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Over-the-air_Transmission)
  * [9 Conclusion](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Conclusion)
  * [10 Index of Files Used](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK#Index_of_Files_Used)


## Stage 1: Initial Packet Communications Flowgraph
Before introducing real hardware, the first objective is to develop a flowgraph that operates correctly under ideal, simulated conditions. We begin with a flowgraph derived from the BPSK Demodulation and Packet Communications tutorials. 
Download: [Media:Bpsk_file_transfer1.grc](https://wiki.gnuradio.org/images/e/e7/Bpsk_file_transfer1.grc "Bpsk file transfer1.grc")  
[![](https://wiki.gnuradio.org/images/thumb/7/7f/Bpsk_file_transfer1_fg.png/800px-Bpsk_file_transfer1_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer1_fg.png)
This flowgraph adopts most concepts from the referenced tutorials, with a few minor deviations. For example, the variable block _hdr_ uses a 64-bit access code instead of a 32-bit access code. This access code is taken from the _packet_utils_ module and provides a more robust synchronization word. Additional variable blocks, _packets_ and _payload_size_ , allow these values to be adjusted during testing. 
Notably, file source and file sink blocks are not present at this stage. Early in development, file-based testing is cumbersome and inefficient. Evaluating demodulated files often requires external tools, and because files are typically binary, inspection is difficult. Early implementations are also prone to errors. It is therefore more effective to use simpler blocks for transmitting and receiving data. For this reason, the Vector Source block is used to transmit payloads, and the Message Debug block is used to print received payloads directly to the console. 
For this flowgraph, the following list comprehension is used as the input to the Vector Source block: 

```
[p + 48 for p in range(packets) for _ in range(payload_size)]

```

This list contains sufficient data as defined by the variables _packets_ and _payload_size_. Its length is _packets × payload_size_. The first payload contains the value 48 repeated _payload_size_ times. The second payload contains similar repeated values of 49. The fifth and final packet contains repeated values of 52. These values were chosen because values 48 to 52 corresponds to the ASCII characters "0" to "4" . These values will be reused later when transitioning to a file source. 
Running the flowgraph produces the following console output: 

```
***** VERBOSE PDU DEBUG PRINT ******
()
pdu length =         64 bytes
pdu vector contents = 
0000: 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 
0010: 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 
0020: 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 
0030: 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 31 
************************************
***** VERBOSE PDU DEBUG PRINT ******
()
pdu length =         64 bytes
pdu vector contents = 
0000: 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 
0010: 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 
0020: 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 
0030: 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 
************************************
***** VERBOSE PDU DEBUG PRINT ******
()
pdu length =         64 bytes
pdu vector contents = 
0000: 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 
0010: 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 
0020: 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 
0030: 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 
************************************

```

Only three of the five packets are successfully demodulated. Since hexadecimal 0x30 corresponds to decimal 48 and 0x34 corresponds to decimal 52, the first and last packets are missing. Increasing the packet count does not resolve this issue. 
These errors occur because the system lacks a preamble and postamble. A preamble allows the Symbol Sync and Costas Loop blocks to lock, while a postamble provides extra samples to flush the buffers introduced by various signal processing blocks. These additions are implemented in the next stage. 
## Stage 2: Adding Preamble and Postamble
Each packet requires additional bits before and after the header, payload, and CRC. A straightforward implementation is to extend the Tagged Stream Mux from two inputs to four. The relevant portion is shown below, and the complete flowgraph can be downloaded here: [Media:Bpsk_file_transfer2.grc](https://wiki.gnuradio.org/images/e/ec/Bpsk_file_transfer2.grc "Bpsk file transfer2.grc")
[![](https://wiki.gnuradio.org/images/thumb/b/bb/Bpsk_file_transfer2_tsmux.png/800px-Bpsk_file_transfer2_tsmux.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer2_tsmux.png)
Additional variable blocks, _preamble_size_ and _postamble_size_ , are introduced for flexibility. The Tagged Stream Mux block now includes separate inputs for the preamble and postamble. Each is generated using a Vector Source block followed by a Stream to Tagged Stream block. 
When this flowgraph is run, all five packets are successfully received. Increasing the packet count confirms that all packets are demodulated correctly under ideal conditions. This achieves the first major goal: reliable packet reception in a noise-free simulation. 
  
[![](https://wiki.gnuradio.org/images/thumb/d/dd/Bpsk_file_transfer2_plot.png/400px-Bpsk_file_transfer2_plot.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer2_plot.png)
  
Several observations can be made from the time sink plots above. First, a stream tag indicates a packet length of 108 bytes, corresponding to the output of the Tagged Stream Mux. This value, however, is incorrect. After the Constellation Modulator, the packet length increases by a factor of 32 due to byte unpacking (8 bits per byte) and sample interpolation (4 samples per symbol). A Tagged Stream Multiply Length Tag block could be used to correct this error, but that block is unnecessary for the purposes of this tutorial. 
Second, the transmitted signal exceeds a magnitude of 1.0 due to intersymbol interference introduced by the RRC filter in the constellation modulator. Most GNU Radio SDR transmitter blocks assume a maximum amplitude of 1.0, so this signal must be scaled to prevent distortion. 
Finally, the Symbol Sync block exhibits difficulty in locking. A temporary lock is observed near 3 ms, followed by instability at 4 ms, suggesting underdamping. Because transmitter scaling affects symbol synchronization, the next stage addresses both amplitude scaling and Symbol Sync tuning. 
## Stage 3: Adjusting Transmit Amplitude and Symbol Sync
To accommodate most SDRs, the transmitter amplitude must be reduced. This modification affects the response of the symbol sync block, and it must be tuned appropriately. 
Download: [Media:Bpsk_file_transfer3.grc](https://wiki.gnuradio.org/images/a/ab/Bpsk_file_transfer3.grc "Bpsk file transfer3.grc")
The relevant portion is shown below: 
[![](https://wiki.gnuradio.org/images/thumb/e/e6/Bpsk_file_transfer3_scale.png/800px-Bpsk_file_transfer3_scale.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer3_scale.png)
A Multiply Const block with a factor of 0.5 is inserted after the constellation modulator. This scaling improves transmitter compatibility but negatively impacts Symbol Sync performance. After extensive experimentation, stable operation was achieved by reducing the expected TED gain to 0.1. 
[![](https://wiki.gnuradio.org/images/thumb/e/e5/Bpsk_file_transfer3_sync.png/250px-Bpsk_file_transfer3_sync.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer3_sync.png)
While additional Symbol Sync parameters can be tuned, including loop bandwidth and damping factor, this tutorial limits adjustments to the TED gain for simplicity. 
[![](https://wiki.gnuradio.org/images/thumb/7/7f/Bpsk_file_transfer3_plot.png/400px-Bpsk_file_transfer3_plot.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer3_plot.png)
All transmitted samples now remain below a magnitude of 1.0, and symbol synchronization stabilizes within 5 ms. Packet reception remains successful. The next goal is to verify operation under channel impairments. 
## Stage 4: Channel Simulation
To emulate real hardware effects, a Channel Model block is introduced and the relevant section is shown below. 
Download: [Media:Bpsk_file_transfer4.grc](https://wiki.gnuradio.org/images/c/c5/Bpsk_file_transfer4.grc "Bpsk file transfer4.grc")
[![](https://wiki.gnuradio.org/images/f/f5/Bpsk_file_transfer4_channel.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer4_channel.png)
GUI Range blocks control the noise voltage, frequency offset, and timing offset. We provide some guidelines on possible values to use during simulation. Typically, the radios' oscillator stability is the primary factor in frequency offset and timing offset. Oscillator stability is commonly shown in data sheets for many software-defined radios. 
Frequency offset is calculated as: 

```
frequency_offset = 2 × oscillator_stability × carrier_frequency / sample_rate

```

For two radios, the maximum error is twice the oscillator stability. This error is multiplied by the carrier frequency to find its frequency offset. This offset is further scaled by the sample rate for the Channel Model block. For two radios with 20 ppm oscillator stability at 2.4 GHz and 1 Msps, the frequency offset for the Channel Model block is 0.096. 
Timing offset is calculated as: 

```
timing_offset = 1 + 2 × oscillator_stability

```

For the same example above, this equation yields a timing offset of 1.00004. 
The noise voltage is merely the noise amplitude and also the square root of the noise power. For any given SNR, an approximate noise voltage is calculated as: 

```
 noise_voltage = sqrt(transmit_amplitude ** 2 / (10 ** (SNR_DB/10)))  

```

For the given flowgraph, the throttle rate is reduced to 8000 samples/s to simplify observation. By default, the Channel Model's parameters are set with a noise voltage of 0.1, frequency offset 0.1, and frequency offset 1.0001. We observe successful packet reception during steady-state operation. 
## Stage 5: Adding File Transfer Blocks
In the previous section, the Vector Source block was set to repeat, and therefore successful reception can only be proved in the long run. We make adjustments to guarantee reception for finite packets, and we also introduce the file source and sink blocks. A screenshot of the flowgraph is shown below. 
Download: [Media:Bpsk_file_transfer5.grc](https://wiki.gnuradio.org/images/6/6e/Bpsk_file_transfer5.grc "Bpsk file transfer5.grc")   
[![](https://wiki.gnuradio.org/images/thumb/a/a5/Bpsk_file_transfer5_fg.png/800px-Bpsk_file_transfer5_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer5_fg.png)
### Test File Creation
#### File Creation with Python Snippet (GNU Radio versions 3.10.5.1 and later)
A transmit file is created using a Python Snippet block configured to run during initialization. Although there are many other ways to create the transmit file, using the snippet block allows the flowgraph and corresponding GRC file to be self-contained and to run without dependencies. The file _bpsk_transmit.txt_ contains ten payloads of repeated ASCII characters from "0" to "9", each of length _payload_size_. 
[![](https://wiki.gnuradio.org/images/thumb/0/0e/Bpsk_file_transfer5_txfile.png/400px-Bpsk_file_transfer5_txfile.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer5_txfile.png)
#### Legacy GNU Radio (GNU Radio versions earlier than 3.10.5.1)
This Python Snippet block only works for GNU Radio versions 3.10.5.1 and later. If using an older version of GNU Radio, the transmit file must be created externally and saved in the same directory as the flowgraph. The following can be run on the command line to create the transmit file with the default payload of 64 bytes: 

```
python3 -c "f=open('bpsk_transmit.txt','w'); f.write(''.join([chr(v)*64 for v in range(0x30,0x3A)]))"

```

Once this file is created, the Python Snippet block should be disabled to prevent further errors. 
### File Source and Sink
The file source replaces the Vector Source, and the file sink saves the received payload. The Message Debug block is retained for convenient inspection. 
### Increased Preamble Size
To ensure successful reception of the first packet, the preamble size is increased to 250 bytes. While inefficient, this approach simplifies the design and ensures control loops lock reliably. 
### Test Results
All ten packets are received correctly. A Python Snippet block compares the transmit and receive files and prints the results after the flowgraph stops. 
[![](https://wiki.gnuradio.org/images/thumb/3/3d/Bpsk_file_transfer5_cmp.png/400px-Bpsk_file_transfer5_cmp.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer5_cmp.png)
We now have a robust flowgraph that performs well under simulated channel effects, and we are ready to use real hardware. 
## Stage 6: Single SDR Loopback Test
Adding real hardware poses unique challenges to development. We continue the strategy of using small, incremental improvements to reduce errors. These examples focus on USRP and ADALM-PLUTO radios and their corresponding blocks, but most of these principles also apply to other software-defined radios. We begin with loopback testing, where the transmitter is directly connected to the receiver. Loopback testing with a single SDR device allows high-SNR validation using minimal hardware. Many SDR models allow loopback testing by making a wired connection between the transmitter and receiver via an SMA cable **and attenuator**. We recommend an attenuation of at least 30 dB for safety. 
This stage consists of two parts. In the first part, we configure the radio settings including gain calibration. In the second part, we use these settings to validate file transfer. 
### Setting SDR Parameters
SDR radios contain varying models and daughter cards which require proper configuration. We begin with the following flowgraph to aid in configuration. 
USRP Download: [Media:Bpsk_file_transfer6.grc](https://wiki.gnuradio.org/images/3/36/Bpsk_file_transfer6.grc "Bpsk file transfer6.grc")
ADALM-PLUTO Download: [Media:Bpsk_file_transfer6_pluto.grc](https://wiki.gnuradio.org/images/3/3b/Bpsk_file_transfer6_pluto.grc "Bpsk file transfer6 pluto.grc")
These flowgraphs contain the following modifications from the previous stage. The USRP or PlutoSDR Sink replaces the virtual sink, and the USRP or PlutoSDR Source replaces the virtual source. The entire channel simulation section is no longer needed and therefore removed. The tag gate and throttle block are also removed. The file source block has been set to repeat for continuous operation. The file sink and message debug blocks are disabled to prevent unnecessary output. 
Additional variable blocks are added for radio configuration. They include the sample rate, frequency, transmit gain, and receiver gain. These variable blocks are used in the USRP or PlutoSDR Sink and USRP or PlutoSDR Source blocks. Therefore, changing these variables also changes the SDR radio configuration. 
[![](https://wiki.gnuradio.org/images/thumb/d/df/Bpsk_file_transfer6_init_varblocks.png/500px-Bpsk_file_transfer6_init_varblocks.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer6_init_varblocks.png)
#### Sample Rate
The sample rate is configured by the variable block with ID samp_rate. The attached flowgraph sets this value to 1 million. This rate is suitable for many USRP models, including the B series, N series, and X series and for ADALM-PLUTO. The sample rate is low enough for most computers to operate, but it may need to be modified depending on the hardware. 
#### Frequency
The default frequency is set to 2435 MHz, which is part of an ISM band [[1]](https://en.wikipedia.org/wiki/ISM_radio_band). This frequency is not guaranteed to work for all SDRs. We recommend you consult the user's manual or data sheets to verify the operational range. This frequency may need to be changed to accommodate your device. 
#### Transmit and Receive Gains
For safety, the default transmit and receive gains are set to zero. GUI Range blocks are used for these variables, as they must be adjusted during flowgraph operation. With the correct sample rate and frequency now set, we run the flowgraph. Initially, we expect the amplitude in the time sink to be fairly low. It is also possible that no plot is seen if the received signal is below the trigger level. 
Since the flowgraph has been tuned to 0.5 amplitude in previous stages, we recommend tuning the gains until that amplitude is seen in the Recovered Symbols trace. First increment the receive gain by 0.05. Then increment the transmit gain by 0.05. Repeat this process until the desired amplitude is seen. In the screenshot below, we show the initial and final gain settings of the flowgraph. For this example, the ideal settings were a transmit gain of 0.30 and receive gain of 0.35. Write down your settings to be used in the next step before closing the flowgraph! 
**NOTE:** The ADALM-PLUTO has a transmit **attenuator** parameter instead of transmit gain. Therefore, increasing the transmit power requires lowering the attenutation. 
[![](https://wiki.gnuradio.org/images/thumb/8/8f/Bpsk_file_transfer6_gains.png/800px-Bpsk_file_transfer6_gains.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer6_gains.png)
### File Transfer
Now that the correct radio settings have been found, we proceed to file transfer. 
USRP Download: [Media:Bpsk_file_transfer_loopback.grc](https://wiki.gnuradio.org/images/7/74/Bpsk_file_transfer_loopback.grc "Bpsk file transfer loopback.grc")
ADALM-PLUTO Download: [Media:Bpsk_file_transfer_loopback_pluto.grc](https://wiki.gnuradio.org/images/0/08/Bpsk_file_transfer_loopback_pluto.grc "Bpsk file transfer loopback pluto.grc")
The attached flowgraph reverts the file source to non-repeat. The file sink and message debug blocks are enabled. For proper operation, the user must manually enter the correct sample rate and frequency in the variable blocks, and the correct transmit and receive gain in the GUI Range blocks. For the gains, they are entered in the parameter 'Default Value'. An example screenshot is shown below. 
[![](https://wiki.gnuradio.org/images/thumb/b/b7/Bpsk_file_transfer6_varblocks.png/500px-Bpsk_file_transfer6_varblocks.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_file_transfer6_varblocks.png)
With the correct radio settings, one should see successful packet reception and file transfer. Because real hardware is used, there is no guarantee of success on every run. Nevertheless, the flowgraph should be successful many more times than not. 
### Troubleshooting
If consistent packet errors are observed, we suggest the following corrective actions. First, increase the variables 'preamble_size' and 'postamble_size' 64 bytes at a time to further aid synchronization. Also, for the USRP, two-stage tuning may help in reducing the effects of local oscillator leakage. For USRP models, this may be accomplished by setting the LO offset to the USRP Sink by 5 MHz and the LO offset of the USRP Source to -5 MHz. In the frequency parameter, use uhd.tune_request(frequency, 5e6) for the USRP sink and uhd.tune_request(frequency, -5e6) for the USRP source. 
## Stage 7: Separate Radios for Transmit and Receive
The final stage separates the transmitter and receiver across two radios and two flowgraphs. It may be easier to manage the testing if they are run from two separate computers as well (when available). 
In Stage 6 we presented tests with two SDR devices: USRP and ADALM-PLUTO. In this section we add a third receiver: RTL-SDR. This gives us two transmitters (USRP and ADALM-PLUTO) and three receivers (USRP, ADALM-PLUTO, and RTL-SDR). By mixing and matching, we have six combinations available. 
### Setting USRP and ADALM-PLUTO Parameters
#### Frequency
The default frequency is set to 2435 MHz, which is part of an ISM band [[2]](https://en.wikipedia.org/wiki/ISM_radio_band). However, the RTL-SDR V3 only goes to 1.7GHz, so a lower frequency is chosen when using that device. A frequency of 905.2MHz (also in an ISM band) is used in the included files for the RTL-SDR. 
#### Transmit and Receive Gains
In Stage 6 we had flowgraphs to set the transmit and receive gains for a single device. It turns out that we can use those flowgraphs for two devices just by how we connect them. For example, using device A to transmit and device B to receive, we connect the transmit port of device A to the receive port of device B using an SMA cable **and attenuator**. This Stage 7 drawing illustrates the connections. 
[![](https://wiki.gnuradio.org/images/thumb/e/ec/Stage7_drawing.png/800px-Stage7_drawing.png)](https://wiki.gnuradio.org/index.php?title=File:Stage7_drawing.png)
Then by running `bpsk_file_transfer6` (for USRP) or `bpsk_file_transfer6_pluto` (for ADALM-PLUTO) as appropriate for the two devices, the transmit gain on device A and the receive gain on device B can be adjusted as was done in Stage 6. Leave the transmit from B and receive on A settings at zero gain. Make sure to write down the gain settings before terminating the flowgraphs. The special case for receiving on an RTL-SDR is addressed below. 
#### File Transfer
Now that the correct radio settings have been found, we proceed to file transfer using the same loopback flowgraphs as in Stage 6 and the connections shown above. 
Those loopback flowgraphs revert the file source to non-repeat. The file sink and message debug blocks are enabled. 
For proper operation, the user must manually enter the correct sample rate and frequency in the variable blocks, and the correct transmit and receive gain in the GUI Range blocks. For the gains, they are entered in the parameter 'Default Value'. 
For Stage 6, the loopback flowgraphs contained the transmitter and receiver, thus assuring that the transmitter was not started before the receiver. For Stage 7, the transmitter and receiver are in different locations, running on different copies of the loopback flowgraphs, so the receiver must be started manually before the transmitter. 
Specifically, the receiver for device B must have its gain set and flowgraph started first. Then the transmitter for device A must have its gain set and flowgraph started. Once the console output of device B shows the received file data, both flowgraphs may be terminated. 
USRP Download: [Media:Bpsk_file_transfer_loopback.grc](https://wiki.gnuradio.org/images/7/74/Bpsk_file_transfer_loopback.grc "Bpsk file transfer loopback.grc")
ADALM-PLUTO Download: [Media:Bpsk_file_transfer_loopback_pluto.grc](https://wiki.gnuradio.org/images/0/08/Bpsk_file_transfer_loopback_pluto.grc "Bpsk file transfer loopback pluto.grc")
### Receiving with RTL-SDR
This section was designed and tested with a RTL-SDR Blog V3. Other models would require changes to the gain settings and possibly other parameters. 
#### Frequency
A frequency of 905.2MHz (also in an ISM band) is used in the included files. Note that the transmitter must be on the same frequency. 
#### Transmit and Receive Gains
Load and execute [Media:Bpsk_file_transfer7.grc](https://wiki.gnuradio.org/images/8/8c/Bpsk_file_transfer7.grc "Bpsk file transfer7.grc") (for USRP) or [Media:Bpsk_file_transfer7_pluto.grc](https://wiki.gnuradio.org/images/1/18/Bpsk_file_transfer7_pluto.grc "Bpsk file transfer7 pluto.grc") (for ADALM-PLUTO) as appropriate. 
Load and execute [Media:Bpsk_file_transfer7_receive_gain.grc](https://wiki.gnuradio.org/images/1/15/Bpsk_file_transfer7_receive_gain.grc "Bpsk file transfer7 receive gain.grc"). 
Adjust the transmit and receive gains as was done in Stage 6. Leave the transmit from B and receive on A settings at zero gain. Make sure to write down the gain settings before terminating the flowgraphs. 
#### File Transfer
Set the default receive gain to the value determined above and execute [Media:Bpsk_file_transfer_receive_rtlsdr.grc](https://wiki.gnuradio.org/images/4/4f/Bpsk_file_transfer_receive_rtlsdr.grc "Bpsk file transfer receive rtlsdr.grc"). **NOTE:** You must start the receiver first! 
Set the default transmit gain to the value determined above and execute [Media:Bpsk_file_transfer7_transmit.grc](https://wiki.gnuradio.org/images/8/84/Bpsk_file_transfer7_transmit.grc "Bpsk file transfer7 transmit.grc") (for USRP) or [Media:Bpsk_file_transfer7_transmit_pluto.grc](https://wiki.gnuradio.org/images/e/e0/Bpsk_file_transfer7_transmit_pluto.grc "Bpsk file transfer7 transmit pluto.grc") (for ADALM-PLUTO) as appropriate. 
Once the console output of device B shows the received file data, both flowgraphs may be terminated. 
## Over-the-air Transmission
For many users, over-the-air transmission may be the ultimate goal. We offer the following suggestions and disclaimers. First and foremost, study your region's regulations and verify that the desired transmit frequency is permitted. Next, the desired receiver amplitude of 0.5 may not be realistic. Additional tuning may be needed for the lower received amplitude. Alternatively, automatic gain control may be needed to compensate for the varying amplitude. Be aware that no compensation is made for interference, therefore one should expect higher packet errors with wireless communications. 
## Conclusion
This tutorial demonstrated file transfer using BPSK with packet communications through an incremental development process. File-based blocks were intentionally introduced late to simplify debugging. 
The flowgraph was simplified for demonstration purposes, and many improvements remain possible: 
  * Allow variable length files
  * Improve efficiency with a larger initial preamble, followed by smaller packet preambles
  * Automatic gain control for varying receiver power
  * Frequency lock loop block for better coarse frequency correction
  * Forward error correction for improved packet reception
  * Reliable delivery mechanisms to compensate for packet errors


Finally, we encourage users to review other communication methods. Typically, phase shift keying systems like BPSK and QPSK are often taught first because transmission is relatively simple. However, PSK may not be suited for all use cases. Other modulation schemes such as GMSK or multiplexing schemes like OFDM offer improved robustness and are recommended for further study. 
## Index of Files Used

```
Bpsk_file_transfer1.grc                 Stage 1
Bpsk_file_transfer2.grc                 Stage 2
Bpsk_file_transfer3.grc                 Stage 3
Bpsk_file_transfer4.grc                 Stage 4
Bpsk_file_transfer5.grc                 Stage 5
Bpsk_file_transfer6.grc                 Stage 6 gain set USRP
Bpsk_file_transfer6_pluto.grc           Stage 6 gain set ADALM-PLUTO
Bpsk_file_transfer_loopback.grc         Stage 6 loopback USRP
Bpsk_file_transfer_loopback_pluto.grc   Stage 6 loopback ADALM-PLUTO
Bpsk_file_transfer7.grc                 Stage 7 gain set USRP                     905.2MHz
Bpsk_file_transfer7_pluto.grc           Stage 7 gain set ADALM-PLUTO              905.2MHz
Bpsk_file_transfer7_receive_gain.grc    Stage 7 gain set RTL-SDR                  905.2MHz
Bpsk_file_transfer_receive_rtlsdr.grc   Stage 7 receive file RTL-SDR              905.2MHz
Bpsk_file_transfer7_transmit.grc        Stage 7 send file USRP to RTL-SDR         905.2MHz
Bpsk_file_transfer7_transmit_pluto.grc  Stage 7 send file ADALM-PLUTO to RTL-SDR  905.2MHz

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&oldid=16067](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&oldid=16067)"
[Categories](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Tutorials "Category:Tutorials")
  * [Tested With 3.10](https://wiki.gnuradio.org/index.php?title=Category:Tested_With_3.10 "Category:Tested With 3.10")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=File+transfer+using+Packet+and+BPSK "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:File_transfer_using_Packet_and_BPSK "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK)
  * [View source](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/File_transfer_using_Packet_and_BPSK "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/File_transfer_using_Packet_and_BPSK "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&oldid=16067 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=File_transfer_using_Packet_and_BPSK&action=info "More information about this page")


  * This page was last edited on 14 March 2026, at 23:10.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


