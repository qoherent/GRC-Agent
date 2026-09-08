# M-ASK, M-PSK, and QAM-M Mod and Demod


Purpose of the LAB: To put into practice M-ASK, M-PSK and QAM-M mod/demod and observe their performances/differences.
Educational objectives:
- Consider M-ary modulations with different schemes.
- Visualize the constellation of modulations.
- Analyze their spectral efficiency and noise sensitivity
- Compare performances via BER

## BPSK/2-ASK
At first, run [Media:BPSK.grc](https://wiki.gnuradio.org/images/9/9e/BPSK.grc "BPSK.grc") corresponding to the flowgraph :
[](https://wiki.gnuradio.org/index.php?title=File:BPSK.png)
Refer to the section <https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation> for explanation.
### Variables
Contents of the variables 'Delay', 'rrc-taps' and 'timing_loop_bw'
Delay: int (5.5 * sps + 7)
rrc_ taps : firdes.root _raised_ cosine ( nfilts , nfilts , 1.0/float( sps ), Alpha, 11* sps * nfilts )
timing_loop_bw : 0.0628
### Blocks Setting
[](https://wiki.gnuradio.org/index.php?title=File:Block_setting.png)
## M-PSK
### QPSK
Modify the previous flowgraph to obtain a QPSK [Media:QPSK.grc](https://wiki.gnuradio.org/images/5/58/QPSK.grc "QPSK.grc")
[](https://wiki.gnuradio.org/index.php?title=File:QPSK.png) - Edit Constellation object QPSK.
- Modulus Edit 4.
- Add a K-bit Unpack block (K: 2) to be placed as shown in the flowgraph
- Multiply the delay by 2 --> int (5.5 * sps + 7)*2
- Observe the BER as the noise power increases.
### 8-PSK
Modify the previous flowgraph to obtain an 8-PSK [Media:8psk.grc](https://wiki.gnuradio.org/images/0/04/8psk.grc "8psk.grc")
[](https://wiki.gnuradio.org/index.php?title=File:8psk.png) - Edit Constellation object 8PSK
- Modulus Edit 8
- K-bit Unpack block (K: 3).
- Observe the BER as the noise power increases.
- Multiply the delay by 3 --> int (5.5 * sps + 7)*3

```
[](https://wiki.gnuradio.org/index.php?title=File:8PSK_Output.png)

```

## M-ASK
### 4-ASK
Modify the QPSK flowgraph to obtain a 4-ASK.
- Replace the Constellation Object by Constellation Rect Object block
[](https://wiki.gnuradio.org/index.php?title=File:4ASK_Const.png)
4-ASK [Media:4ASK.grc](https://wiki.gnuradio.org/images/7/75/4ASK.grc "4ASK.grc") whose flowgraph is given below :
[](https://wiki.gnuradio.org/index.php?title=File:4ASK.png) - Modify Constellation Rect Object as shown above.
- Modulus Edit 4.
- K-bit Unpack block (K: 2)
- Multiply the delay by 2 --> int (5.5 * sps + 7)*2
- Observe the BER as the noise power increases.
[](https://wiki.gnuradio.org/index.php?title=File:4ASK_Output.png)
### 8-ASK
8-ASK [Media:8ASK.grc](https://wiki.gnuradio.org/images/f/fd/8ASK.grc "8ASK.grc") whose flowgraph is given below :
[](https://wiki.gnuradio.org/index.php?title=File:8ASK.png)
- Edit Constellation Rect Object as shown

```
[](https://wiki.gnuradio.org/index.php?title=File:8ASK_Const.png)

```

## QAM-M
QAM-16 [Media:QAM16.grc](https://wiki.gnuradio.org/images/e/e9/QAM16.grc "QAM16.grc") whose flowgraph is given below :
[](https://wiki.gnuradio.org/index.php?title=File:QAM16.png)
- Modify the QPSK flowgraph to obtain a 16-QAM
- Edit Constellation object 16QAM
- Modulus Edit 16
- K-bit Unpack block (K: 4).
- Observe the BER as the noise power increases.
- Multiply the delay by 4 --> int (5.5 * sps + 7)*4
[](https://wiki.gnuradio.org/index.php?title=File:16QAM_Output.png)
