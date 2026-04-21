# IQ Complex Tutorial
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#searchInput)
This tutorial originates from discussions on discuss-gnuradio@gnu.org. We will explain why simulating digital communications requires equivalent baseband representation of signals--which in fact are complex signals. For this reason, complex signals are essential in GNURadio. 
This tutorial is also intended for non-specialists, as it involves as little maths as possible while presenting most results using GNURadio's flowgraph. Some examples involving simple modulation schemes used in HAM radio are presented. While introducing complex signals can be seen as increasing complexity, we will see that it drastically simplifies the understanding of certain concepts, such as synchronization. 
If you are searching for more detailed information, please refer to the literature--such as references [[1]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre1),[[2]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre2),[[3]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre3). 
## Contents
  * [1 Some maths](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Some_maths)
  * [2 Why we need complex and IQ signals](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Why_we_need_complex_and_IQ_signals)
  * [3 Spectral properties of signals](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Spectral_properties_of_signals)
    * [3.1 continuous real signal](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#continuous_real_signal)
    * [3.2 continuous complex signal](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#continuous_complex_signal)
    * [3.3 sampled signals](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#sampled_signals)
  * [4 Complex envelope, equivalent baseband signal](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Complex_envelope,_equivalent_baseband_signal)
    * [4.1 Equivalent baseband, Envelope, Complex signals?](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Equivalent_baseband,_Envelope,_Complex_signals?)
  * [5 IQ modulator and demodulator](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#IQ_modulator_and_demodulator)
  * [6 Some examples of EqBB signals](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Some_examples_of_EqBB_signals)
    * [6.1 complex envelope of a pure sine wave](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#complex_envelope_of_a_pure_sine_wave)
    * [6.2 AM mod demod example](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#AM_mod_demod_example)
      * [6.2.1 Further work : construct the AM demodulator flowgraph](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Further_work_:_construct_the_AM_demodulator_flowgraph)
    * [6.3 QPSK example](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#QPSK_example)
  * [7 Equivalent baseband representation](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Equivalent_baseband_representation)
    * [7.1 Bandpass filter](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Bandpass_filter)
    * [7.2 Unsynchronized demodulator](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Unsynchronized_demodulator)
    * [7.3 Channel noise](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Channel_noise)
  * [8 Tx/Rx PSK equivalent baseband simulation](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Tx/Rx_PSK_equivalent_baseband_simulation)
    * [8.1 GNURadio XLating filter](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#GNURadio_XLating_filter)
    * [8.2 Asynchronism in real hardware](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#Asynchronism_in_real_hardware)
  * [9 References](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#References)
  * [10 About figures](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#About_figures)


## Some maths
This section summarizes complex number properties used in this tutorial. More information can be found on [complex number Wikipedia page](https://en.wikipedia.org/wiki/Complex_number "wikipedia:Complex number"). 
[![](https://wiki.gnuradio.org/images/e/e9/IQ_complex_tutorial_polar2.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_polar2.png)Complex number _z_ =_a_ + _jb_
A complex number is a number of the form _a_ + _jb_ , where _a_ and _b_ are real numbers, and _j_ is an indeterminate satisfying _j_ 2=-1 (Mathematician prefer using _i_ instead of _j_ used by physicist and radio engineers). For example, z1=2 + _j_ 3 is a complex number. The real part Re{_z_} of z1 is 2 and its imaginary part Im{_z_} is 3. 
z=a+jb Re{z}=a Im{z}=b
  
Complex numbers can be represented in the complex plane as vectors. The modulus or magnitude _r_ of a complex number _z_ = _a_ + _jb_ is      r=|z|=a2+b2
The phase φ of _z_ (referred to as the _argument_) is the angle of the radial Oz on the positive real axis.       ϕ=arg⁡(z)=arctan⁡(b/a) (for a≠0)
Together, r and φ provide another means of representing complex numbers--both the polar and exponential forms.      z=r(cos(ϕ)+jsin(ϕ))=rejϕ [![](https://wiki.gnuradio.org/images/7/77/IQ_complex_tutorial_polar1.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_polar1.png)The complex plane
The exponential form is convenient for computing the multiplication of two complex numbers.      z1=r1ejϕ1     z2=r2ejϕ2     z=z1z2=r1r2ej(ϕ1+ϕ2)
The following complex numbers have a unit magnitude _r_ =1 :      +1=ej0     +j=ejπ/2     −1=ejπ     −j=ej3π/2
A complex signal _c(t)_ can be seen as two real signals _a(t), b(t)_ --often written as _i(t), q(t)_ --and combined to create a complex signal. It can also be represented by its amplitude over time _r(t)_ as well as its phase variation over time _φ(t)_     c(t)=a(t)+jb(t)=r(t)ejϕ(t)
## Why we need complex and IQ signals
GNURadio software is mainly used to design and study radio communications. Making high frequency transmission requires modulating a high frequency carrier of frequency _f 0_. The most common modulation for analog transmissions are: amplitude modulation (AM), phase modulation (PM), and frequency modulation (FM). 
[![](https://wiki.gnuradio.org/images/thumb/c/c6/IQ_complex_tutorial_AM_spectrum.png/400px-IQ_complex_tutorial_AM_spectrum.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_AM_spectrum.png)AM spectrum
For analog AM, the modulated signal _m(t)_ is simply the mathematical product of the carrier _c(t)_ and the baseband signal _a(t)_. The corresponding hardware is a mixer whose scheme and mathematical representation is a multiplier.      m(t)=a(t)c(t)=a(t)cos⁡(2πf0t)
We call _a(t)_ a baseband signal since its spectrum is in a low frequency range--starting near 0 Hz (e.g. [0-20kHz] for a HiFi audio signal). 
The spectrum of an AM modulated signal _M(f)_ is the translation of the audio spectrum _A(f)_ around ± _F 0_ with _A(f)_ being the entire spectrum of the modulating signal, using both positive and negative frequencies      M(f)=12(A(f−f0)+A(f+f0))
Important - Negative frequencies are often omitted in spectrum representation since, for real signal (_a(t)_ , _m(t)_ are real) the power spectrum are symmetric around zero (more details on this later). 
Up to now we have been dealing with real signals. The need for complex signals appears in the next step. Simulation requires sampled signal. Sampling is the operation of observing a continuous signal and taking a finite number of samples at a given sampling rate _f s_ (i.e. one sample each 1/_f s_ second). Because a simulator can only make calculations on a finite number of samples, it requires a sampled signal. Nyquist Sampling theorem states that the sampling rate must be greater than twice the maximum frequency _F Max_ in order to reconstruct the original signal from the sampled signal.      fs>2FMax
For a HiFi audio signal, the maximum audio frequency FMaxAudio is close to 20 kHz, so the sampling rate must be higher than 40 kHz (44.1 kHz is often used in computer sound cards, 8 kHz is used for mobile phones since voice has a lower frequency range than HiFi audio). 
For an AM signal modulated by an audio signal, the maximum frequency of the modulated spectrum is FMax=F0+FMaxAudio. Direct sampling of such a signal is not possible with conventional hardware such as a low cost SDR dongle. If the carrier frequency is close to 1 GHz, the sampling rate should be at least 2 GHz. This is obviously too much for the computer to handle (higher than some CPU clocks). 
Flowgraph [IQ_tutorial_AM_TX_real.grc](https://wiki.gnuradio.org/images/9/9f/IQ_tutorial_AM_TX_real.grc "IQ tutorial AM TX real.grc") illustrates amplitude modulation using only real blocks (excepted for bits source). As a consequence, the maximum carrier frequency is limited to several tens of kHz. 
  * study the modulator part which simply multiplies the baseband signal and the sinusoidal carrier
  * look at the influence of the carrier frequency on the modulated signal spectrum (carrier frequency must stay lower than half the sampling rate)
  * look at the spectrum shape for sawtooth input and random bit sequence (QT GUI chooser and selector)
  * When transmitting random bits, you can deactivate the interpolating FIR Filter and replace it by a root raised cosine filter


## Spectral properties of signals
Amplitude spectrum is calculated using the Fourier Transform. It represents how the power is spread in the frequency domain. It allows for determining the signal bandwidth. Power Spectral Density or PSD correspond to the average magnitude of the Amplitude spectrum. 
In this section we summarize some properties of the phase arg _{X(f)}_ and magnitude |_X(f)_ |. We first consider properties for continuous signal. Then we will investigate additional properties of sampled signal (those used in GNURadio). 
Given a signal _x(t)_ its amplitude/phase spectrum is denoted _X(f)_ which is a complex function given by :      X(f)=∫x(t)e−2jπftdt
### continuous real signal
Every real signal has a spectrum whose magnitude is symmetric and phase is anti-symmetric.      x(t)∈ℝ     X(−f)=X*(f)     |X(−f)|=|X(f)|     arg{X(−f)}=−arg{X(f)} [![](https://wiki.gnuradio.org/images/thumb/c/c8/IQ_complex_tutorial_complex_spectrum.png/500px-IQ_complex_tutorial_complex_spectrum.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_complex_spectrum.png)Complex signal spectrum and, sampled complex signal spectrum (only first 3 patterns represented)
### continuous complex signal
The main difference with real signal is that: 
  * any complex signal having non null imaginary part exhibits a non-symmetric spectrum.
  * as a consequence, every non-symmetric spectrum corresponds to a complex signal


### sampled signals
We consider a signal _x(t)_ and we note its sampled version _x s(t)_ sampled at frequency _F s_. 
The spectrum _X s(f)_ of the sampled signal is a periodic function of period _F s_.      Xs(f)=X(f−kFs)
So the sampled signal spectrum verify :      Xs(f+kFs)=Xs(f)
Generally the bandwidth of _X(f)_ is lower then _F s_/2. The infinite sum defined above exhibit no aliasing (no superposition of patterns _X(f)_ , _X(f+F s)_ and _X(f-F s)_). In that case, as stated in the Nyquist theorem of sampling, the original spectrum/signal can be recovered by filtering the sampled signal. 
When the maximum frequency of the signal spectrum do not respect Nyquist theorem, one should filter the signal with a low pass filter having a cutt-off frequency lower then _F s_/2 before sampling: this correspond to the anti-aliasing filter used in every SDR hardware. 
## Complex envelope, equivalent baseband signal
Baseband signals have a spectrum at low frequency near 0 Hz. Audio, video and NRZ line code are baseband signals. 
Passband signals have no energy near 0 Hz and a spectrum located near a high frequency (generally the carrier frequency). Analog and digital AM, PM and FM modulated signals are passband signals. 
A theorem ([[1]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre1),[[2]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre2),[[3]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre3)) states that any high frequency passband signal having a limited bandwidth _B_ can be represented by a baseband equivalent signal having the same bandwidth. This baseband equivalent signal also called the complex envelope is used in simulators since it allows to lower sampling rate as compared to directly sampling the passband signal. 
The equivalent baseband representation helps us for the simulation of passband signals. At this step we need some math. We will consider a carrier modulated in phase and/or amplitude (in the sake of simplicity, Frequency modulation is not considered but it can be related to phase modulation.). Such a modulated signal _m(t)_ and it's complex representation m~(t) is :      m(t)=a(t)cos⁡(2πF0t+ϕ(t))     m~(t)=a(t)ej(2πF0t+ϕ(t))=a(t)ejϕ(t)ej2πF0t=mbb(t)ej2πF0t
For modulated signal, the complex representation is obtained by replacing the cos function by an exponent function . For a more general definition see [[1]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre1),[[2]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre2),[[3]](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#ancre3) . The real signal corresponds to the real part of the complex signal m(t)=Re(m~(t)). 
[![](https://wiki.gnuradio.org/images/thumb/0/03/IQ_complex_tutorial_EQ_BB.png/400px-IQ_complex_tutorial_EQ_BB.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_EQ_BB.png)AM spectrum
One important property of m~(t) spectrum M~(f) is that it has only energy in the positive frequency range and verify.      M~(f)=M+(f)
where =M+(f) denotes the restriction of =M(f) to the positive frequency range. 
Let us now look at the complex envelope or equivalent baseband signal mbb(t) of passband signal m(t) defined by :      mbb(t)=m~(t)e−j2πF0t=a(t)ejϕ(t)
Multiplying a signal by e+j2πF0t correspond to a frequency translation of the spectrum so that we have:      Mbb(f)=M+(f+F0)
So the spectrum of the complex envelope is a baseband signal whose spectrum has the same shape as =M+(f). For this reason, knowing m~(t) or Mbb(f) is sufficient to reconstruct m(t) or M(f). 
### Equivalent baseband, Envelope, Complex signals?
Terms **equivalent baseband signal** , **complex envelope** and **complex signal** can be seen as equivalent terms referring to the same thing. 
While simulating some radio we have mainly two type of signals: 
  * any low frequency or baseband signal are real signal, they are represented by real signals
  * every passband signal must be represented by their equivalent baseband complex signal (excepted for systems having very low carrier frequency)


So in a gnuradio flowgraph, there is no ambiguity: every complex signal (blue interconnections) is implicitly an equivalent representation of a passband signal so we will simply call it a **complex signal**. 
## IQ modulator and demodulator
[![](https://wiki.gnuradio.org/images/thumb/1/18/IQ_complex_tutorial_IQ.png/200px-IQ_complex_tutorial_IQ.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_IQ.png)Complex signal in the IQ plane [![](https://wiki.gnuradio.org/images/thumb/5/5d/IQ_complex_tutorial_IQ_Mod_Demod-crop.png/400px-IQ_complex_tutorial_IQ_Mod_Demod-crop.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_IQ_Mod_Demod-crop.png)IQ modulator and demodulator
Let us now come to hardware and SDR and first rewrite the equivalent baseband signal and the modulated signal _m(t)_ :      mbb(t)=a(t)ejϕ(t)=i(t)+jq(t)     m(t)=Re[(i(t)+jq(t))ej2πF0t]     m(t)=i(t)cos⁡(2πF0t)−q(t)sin⁡(2πF0t)
The phase _φ(t)_ of the modulated signal _m(t)_ , is identical to the phase of the complex signal _c(t)=i(t)+jq(t)_. The equivalent baseband signal _c(t)_ is represented in a complex plane also refeered to as the IQ plane. The resulting _m(t)_ can be any modulated in AM, PM or even FM signal. 
  * i(t)=a(t)cos⁡(ϕ(t))
  * q(t)=a(t)sin⁡(ϕ(t))


As a result, the modulated signal _m(t)_ is the addition of : 
  * i(t)cos⁡(2πF0t) which is an AM modulated signal, the product of _i(t)_ by a signal In phase with the carrier (i stand for In phase)
  * −q(t)sin⁡(2πF0t) which is an AM modulated signal, the product of _q(t)_ by a signal in Quadrature with the carrier (q stand for Quadrature)


The corresponding hardware is called an IQ modulator. Every modern radio communication uses IQ modulator for emitting and IQ demodulator for receiving. The IQ demodulator is able to recover incoming _i(t)_ and _q(t)_. If the amplitude of the recovered carrier is 2, and if modulator and demodulator carrier are synchronous (same frequency and phase) the output of the IQ demodulator correspond to input _i(t)_ and _q(t)_.      i^(t)=i(t)     q^(t)=q(t)
In real hardware, carrier are not synchronous and the receiver must compensate any phase and frequency difference between emitter and receiver. This is done using some hardware and/or software such as symbol sync and Costas loop. 
SDR Module such as USRP [(USRP N320 block Diagram)](https://www.ettus.com/all-products/usrp-n320/%7C) and SDR Dongle [(RTL-2832)](https://www.programmersought.com/article/59454592969/%7C) input and output are the 2 real signals _i(t)_ and _q(t)_ combined to form the complex signal _i(t) + j q(t)_ which turns to be the equivalent baseband of the modulated emitted or received signal. These hardware are based on IQ modulator and IQ demodulator associated with mixers when intermediate frequency (IF) is used. 
## Some examples of EqBB signals
In order to get familiar with complex signal, let us consider some basic examples. 
First, we suppose our emitter carrier is cos⁡(2πF0t) so that every equivalent baseband signal will be defined according to this reference. 
### complex envelope of a pure sine wave
We will consider a pure sine wave, close to the carrier having a _Δf_ frequency shift and _φ_ phase shift as compared to the carrier. After some math we get its complex equivalent signal.      m(t)=Acos⁡(2π(F0+Δf)t+ϕ)     m~(t)=Aej(2π(F0+Δf)t+ϕ)     mbb(t)=Aej(2πΔft+ϕ)=Aej2πΔftejϕ
The complex envelope of the carrier itself is found for _Δf_ =0 and _φ_ =0 which yields mbb(t)=A. We conclude that in a GNURadio flowgraph, the carrier is represented by a continuous components, a pure DC signal ; this may seem counter intuitive. The spectrum Mbb(f) is a single peak at _f=0_ which can be represented using the Dirac _δ(f) function (or distribution)_ : Mbb(f)=δ(f)). Shifting this spectrum of +F0 towards positive frequency we get the positive part of the carrier spectrum (a peak at +F0) : M+(f)=δ(f−F0)). The negative part of the spectrum is simply obtained by symmetry of the real part (a peak at −F0). 
This can be simulated with GNURadio flowgraph [IQ_tutorial_eq_bb.grc](https://wiki.gnuradio.org/images/9/90/IQ_tutorial_eq_bb.grc "IQ tutorial eq bb.grc"). The spectrum of our carrier is centered at 0 Hz. The Frequency sink has a feature to shift this spectrum around F0 which is a parameter of the sink. In a simulation, it is not necessary to represent the negative part of the spectrum since _m(t_) being real, it spectrum is obviously symmetric. 
We will now consider _Δf_ ≠0 which simulate a signal not exactly synchronous to the carrier. This yields      mbb(t)=Aej2πΔft
The complex envelope is rotating vector (you can simulate this with _Δf_ =1 Hz giving a vector which rotate at 60 rpm or 1 turn per second) , its spectrum is a single peak at _f=+Δf_. This complex signal exhibits a non symmetric spectrum (no peak at _f=-Δf_). Change _Δf_ to -0.5 Hz, the vector now rotates counter clockwise at 30 rpm or 0.5 turn per second. 
Exercise: Open flowgraph [IQ_tutorial_eq_bb.grc](https://wiki.gnuradio.org/images/9/90/IQ_tutorial_eq_bb.grc "IQ tutorial eq bb.grc"). Parameter delta_f is set with an increment of 1/12, which correspond to 5 rotation per minute. 
  * For _Δf_ =1/12=0.0833, what is the speed of rotation of the complex signal?
  * What do you observe when _Δf_ =-1/12=-0.0833 ?
  * Slowly increase _Δf_ to reach _f s/2_ and observe the spectrum really has a single peak at _Δf_. Explain your observation when _Δf >fs/2'._
  * Do the same for a negative _Δf_
  * For _Δf =f s_ what is the equivelant baseband frequency. What is the carrier frequency ?
  * Set _Δf_ =0 and _φ_ ≠0. Discuss the simulated equivalent baseband signal.


### AM mod demod example
This example will consider signal baseband signal _a(t)_ modulating a carrier at F0 in AM, and its demodulation. As no phase modulation in used, _φ(t)_ =0 and consequently _q(t)_ =0.      m(t)=a(t)cos⁡(2πF0t)=i(t)cos⁡(2πF0t)−q(t)sin⁡(2πF0t)     i(t)=a(t)     q(t)=0
AM modulation is a special case for which the equivalent baseband complex signal has a null imaginary part and is real. Considering the schematic diagram of an IQ modulator demodulator, when _q(t))_ is null the diagram is simplified (imaginary path is not used) yielding the well known AM modulation/demodulation scheme. 
Remind that in GNURadio flowgraph : 
  * orange connections correspond to real signals (float numbers)
  * blue connections correspond to complex signals (complex numbers)


Flowgraph [IQ_tutorial_AM_TX_complex.grc](https://wiki.gnuradio.org/images/1/1c/IQ_tutorial_AM_TX_complex.grc "IQ tutorial AM TX complex.grc") contains two equivalent diagram for an AM modulation with a sawtooth input: 
  * the upper one uses real signals. It is the exact AM modulator uses at the beginning of this tutorial. 
    * sampling frequency is 200 kHz, 40 times the input rate which equal 5 kHz.
    * allowing a maximum carrier frequency close to 100kHz

[![](https://wiki.gnuradio.org/images/thumb/e/e0/IQ_complex_tutorial_AM_TX_complex.png/600px-IQ_complex_tutorial_AM_TX_complex.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_AM_TX_complex.png)AM modulator flowgraph
  * the lower one uses an equivalent baseband representation. 
    * sampling frequency is 25 kHz, 5 times the input rate which equal 5 kHz.
    * the sawtoooth correspond to identical generator in both modulator
    * each blue input or output is the baseband equivalent of the corresponding signal in the upper AM modulator.
    * the carrier frequency can be any value compatible with connected Hardware
    * the carrier equivalent signal equal 1 (as stated in the previous section) so it has been disable and replaced by a complex constant
    * the Hardware input is the complex equivalent baseband signal
    * the carrier frequency is not needed nor used in complex blocks; excepted in the QT GUI spectrum to label the center frequency which is 0 but correspond to the _F >sub>0_.


Three blocks are unusefull and can be removed from the lower complex modulator: 
  * save the current flowgraph as IQ_tutorial_AM_TX_complex_2.grc.
  * remove the complex multiplier, the carrier equivalent baseband (complex constant=1) and the carrier complex source (the disabled one). Reconnect complex to float and throttle blocks to obtain the flowgraph sketched on the right.


You can now run this flowgraph and compare signal and their spectrum in both modulator. Once again, these simulations suppose perfectly synchronous emitter and receiver carrier which is quite far from reality. 
#### Further work : construct the AM demodulator flowgraph
Use the flowgraph IQ_tutorial_AM_TX_complex_2.grc that you created, add blocks to perform AM demodulation and to recover the input signal _a(t)_ from the modulated signal. You have to use the equivalent baseband representation of the demodulator.   
| 1st indication   |  
| --- |  
| Remind and use the relation between _a(t)_ and _c(t)=i(t)+jq(t)_ given above.   |  
| 2nd indication   |  
| --- |  
| An AM demodulator extract the amplitude (magnitude) of the modulated signal...   |  
| Solution including filter   |  
| --- |  
|  [IQ_tutorial_AM_TX_complex_3.grc](https://wiki.gnuradio.org/images/c/cf/IQ_tutorial_AM_TX_complex_3.grc "IQ tutorial AM TX complex 3.grc") Running this flowgraph, demodulation is obtained taking the real part (imperfect solution), or the magnitude (good solution) of the complex signal. 
  * these two methods give exactly the same results
  * these two methods are close to, but differ from original input signal due to the low-pass filter (only 6 peak are taken from the sawtooth spectrum, 3 for positive frequency and 3 others for negatives ones. )
  * in a real system, bandpass filtering before demodulation is used. As will be shown, the lowpass filter is equivalent to a bandpass filter acting on the passband modulated signal
  * Only taking magnitude is a good AM demodulation since it is less sensitive to frequency and phase lack of synchronism found in real systems.

 |  
### QPSK example
QPSK digital signal exhibit four phase state ϕ∈{π/4,3π/4,−3π/4,−π/4}.      mbb(t)=a(t)ejϕ(t)=i(t)+jq(t)
And the baseband equivalent signal also exhibit 4 different values each one being used to code a 2 bits sequence 00 01 10 or 11 :      mbb(t)∈{1+j,−1+j,−1−j,1−j}
Normally, we would use a GNURadio "constellation modulator" to simulate QPSK as is done in the excellent [Guided Tutorial on PSK Demodulation](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_PSK_Demodulation "Guided Tutorial PSK Demodulation"). 
[![](https://wiki.gnuradio.org/images/thumb/c/cf/IQ_complex_tutorial_QPSK.png/600px-IQ_complex_tutorial_QPSK.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_QPSK.png)QPSK modulator
For the present tutorial we will simulate a QPSK without Nyquist filter in order to get phase states which can be simply displayed on a constellation sink. This is not possible with constellation modulator. Our QPSK modulator (complex representation) is build taking into account that the complex signal exhibit 4 different values, its obvious that both _i(t)_ and _q(t)_ have only 2 states so they are binary symmetric NRZ line codes:      i(t),q(t)∈{+1,−1}
Flowgraph [IQ_tutorial_QPSK.grc](https://wiki.gnuradio.org/images/6/67/IQ_tutorial_QPSK.grc "IQ tutorial QPSK.grc") generates 2 sequences of bits, interpolates them to get 2 binary symmetric NRZ line codes. The NRZ signals are combined to create the complex equivalent baseband signal of the QPSK which can be transmitted to any SDR emitter. 
Simulate this flowgraph : 
  * stop the QT GUI spectrum to observe that the complex baseband signal spectrum is no longer symmetric as expected for complex signals.
  * use spectrum averaging to see that despite of the previous observation, the power spectral density (average of the magnitude spectrum) is symmetric around 0 (which correspond to _F 0_ for the modulated signal.
  * Disable the interpolating filters and enable both root raised cosine filter (the filter used in every QPSK emitter). This yields the real spectrum of a QPSK.


## Equivalent baseband representation
This diagram sketch a classical Emitter/Receiver (Tx/Rx) transmission including channel noise, and its equivalent baseband representation which could be used for a GNURadio simulation. 
[![](https://wiki.gnuradio.org/images/thumb/0/0c/IQ_complex_tutorial_bb_eq.png/800px-IQ_complex_tutorial_bb_eq.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_bb_eq.png)Digital Emitter Receiver based on IQ modulator and its baseband equivalent representation
  * this transceiver can be used to generate and transmit any modern digital communications like OOK, ASK, PSK, and QAM depending on choosen signal _i(t)_ and _q(t)_
  * quantities with an hat like i^(t) should be similar or equal to corresponding i(t)
  * signal _i(t)_ and _q(t)_ are generated using Dirac impulse weighted by amplitudes _a k_ and _b k_ and a shaping filter _h 1(t)_
  * the considered channel is Additive White Gaussian Noise (AWGN)
  * IQ modulator and IQ demodulator are not necessarily synchronized
  * Baseband filter are used to limit the bandwidth in the emitter and limit noise in the receiver. In a well construct Transceiver chain, _h 1(t)_ and _h 3(t)_ should be Root Nyquist filters
  * A Bandpass filter is used in the channel, it can represent the channel effect as well as any additional filter used on the modulated signal


One may observe that this transceiver don't include non linear effect suchs as amplifier intermodulation. In fact, complex baseband representation don't permit precise modelization of non linear effects so they are not considered here. Complex signals on the baseband equivalent representation are represented using double arrow. 
Every linear band limited system has an equivalent baseband which is build using the following rules. 
  * Replace IQ modulator inputs _i(t)_ and _q(t)_ by a complex signal _i(t) + jq(t)_
  * Similarly replace IQ demodulator outputs by a complex signal
  * Keep any baseband filter acting on baseband signal unchanged (filter _h 1(t)_ is acting on _i(t)_ and _q(t)_ so it can be replaced by a single filter acting on complex signal _i(t) + jq(t)_. However one could have used two identical filters each acting on one real signal)
  * Replace IQ modulator by a multiplication by _a_ (the modulator carrier is used as a reference to define complex equivalent baseband, no multiplier if _a_ =1)
  * Replace IQ demodulator by a multiplication by bej(2πΔft+ϕ) (no multiplier if b=1 and demodulator is synchronized to modulator 
    * This result from section [ complex envelope of a pure sine wave](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial#complex_envelope_of_a_pure_sine_wave)
  * Replace any bandpass filter by its equivalent baseband complex filter as defined below
  * Replace channel passband noise by its equivalent baseband complex noise as defined below


### Bandpass filter
[![](https://wiki.gnuradio.org/images/thumb/1/1f/IQ_complex_tutorial_complex_filter.png/500px-IQ_complex_tutorial_complex_filter.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_complex_tutorial_complex_filter.png)Equivalent baseband filter of a bandpass filter
Any bandpass filter with frequency transfer function H(f) having a limited bandwidth can be represented by an equivalent baseband filter Hbb(f) . The baseband filter frequency transfer is the positive part of the transfer function shifted toward 0 (same process as above for AM Spectrum)      Hbb(f)=H+(f+F0)
So we observe that the equivalent filter is a lowpass filter. 
As H(f) is not necessarily symmetric around _F 0_, Hbb(f) can be unsymmetric: this correspond to a complex time transfer function, what we will call a complex filter. 
So the baseband equivalent of a band pass filter is a complex filter acting on a complex signal. 
Normally, when filtering complex signal in GNURadio, in most situation complex taps must be used. In case your baseband filter is symmetric around _F 0_ it turns to be a real filter, it can be represented using real taps. 
### Unsynchronized demodulator
For a receiver carrier _p'(t)_ given by      m(t)=2bcos⁡(2π(F0+Δf)t+ϕ)
It is found that the equivalent baseband is a multiplication with      mbb(t)=be2j(π(Δft+ϕ)
Which is similar (excepted for a factor 2) to the result given for the baseband equivalent of a pure sine wave close to the carrier. The factor 2 difference come from the demodulation process. 
### Channel noise
Any passband noise N(t) having a limited bandwidth can be represented by an equivalent baseband noise Nbb(t) . The baseband equivalent noise spectrum is the positive part of the passband noise spectrum shifted toward 0 (same process as above for AM Spectrum and for filter)      Nbb(f)=N+(f+F0)
So we find that the baseband equivalent noise is a low frequency noise. Furthermore, as for filters, the baseband equivalent noise is complex, its real part and imaginary part having the same variance (power). 
## Tx/Rx PSK equivalent baseband simulation
[![](https://wiki.gnuradio.org/images/thumb/c/cd/IQ_tutorial_QPSK1.png/800px-IQ_tutorial_QPSK1.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_tutorial_QPSK1.png)PSK modulation
In this section we will illustrate equivalent baseband blocks introduced above: 
  * complex noise
  * complex filters
  * IQ demodulator with carrier asynchronism,


This will be done using a simple QPSK and BPSK transceiver simulation. We will not really investigate demodulation but concentrate on what should be done to compensate for the channel impairments. 
Let us first examine this flowgraph. The upper part of the flowgraph generate a QPSK signal as used in a previous flowgraph. The lower part is a modified version of the first one. 
Questions : 
  * If we do not account for the multiplier, what type of signal will generate the second part of this flowgraph (at the throttle output)?
  * What is the effect the multiplier (source is at 25 Khz which is 1/4 of the sampling rate) ?

  
| Solution   |  
| --- |  
| the second signal is a BPSK (_i(t)_ is an NRZ Line code, _q(t)_ is 0. The two phase states of this BPSK are 0° and 180°.   |  
| Without multiplier this BPSK would be centered at _F 0_. As it is multiplied by a complex exponent at 25 kHz, it will be shift at _F 0_+ 25 kHz   |  
Open [IQ_tutorial_QPSK_TX_RX.grc](https://wiki.gnuradio.org/images/a/a5/IQ_tutorial_QPSK_TX_RX.grc "IQ tutorial QPSK TX RX.grc"). Note that BPSK constellation block is connected before the multiplier, while BPSK spectrum is evaluated after the multiplier. 
  * Simulate the flowgraph. Adjust _Delta_f (BPSK shift)_ to 0. Observe the constellation and the spectrum of both signals
  * Increase noise level, observe the complex noise on BPSK and QPSK constellation
  * Change _Delta_f (BPSK shift)_ and observe how the BPSK Spectrum is shifted. 
    * What is the BPSK center frequency ?
    * Explain why the BPSK constellation in this case is rotating


We will now investigate the channel frequency offset parameter. This parameter is used to simulate the frequency offset between a receiver and an emitter. 
Stop the simulation. Disable _delta_f_over_fs_ QT Gui range and enabled the one which was disabled (larger range) and run the flowgraph. 
  * select the spectrum tab, and increase _Delta_f (BPSK shift)_ to 25 kHz
  * change _delta_f_over_fs_ value and observe how it shifts the spectrum of the received signal the same way as our multiplier does.


Let's suppose we receive a modulated at the output of an unsynchronized receiver (hardware). Let's suppose that we have measured the carrier frequency difference between emitter end receiver. The modulated signal spectrum is not centered around _F 0_. There is several way to compensate for this effect: 
  * Specify a receiver frequency which compensate the frequency difference (most SDR Source in GNURadio can specify a frequency shift which is given in PPM (Parts per million).
  * Multiply the received signal by e−2j(π(Δft)
  * Use a GNURadio Xlating filter block which perform the previous multiplication and a filtering.


### GNURadio XLating filter
[![](https://wiki.gnuradio.org/images/thumb/5/5b/IQ_tutorial_QPSK_TX_RX_2.png/800px-IQ_tutorial_QPSK_TX_RX_2.png)](https://wiki.gnuradio.org/index.php?title=File:IQ_tutorial_QPSK_TX_RX_2.png)Basic Tx/Rx using a Xlating filter at receiver
GNURadio XLating filter can perform the following actions 
  * It can shift the spectrum in the frequency domain
  * It can filter the result with the specified filter
  * It can decimate the signal


The Xlating filter is useful every time a signal spectrum is not centered, and/or when you need to select one signal in a spectrum where several channel are in use. 
Open [IQ_tutorial_QPSK_TX_RX_2.grc](https://wiki.gnuradio.org/images/2/26/IQ_tutorial_QPSK_TX_RX_2.grc "IQ tutorial QPSK TX RX 2.grc"). 
This flowgraph 
  * generate a single signal composed of: 
    * our QPSK signal at _F 0_
    * the BPSK signal at _F 0_+25 kHz
  * simulate a channel frequency offset
  * use a Xlating filter to shift the received signal (compensate the channel and/or select the desired channel)
  * filter the signal to select only one signal BPSK or QPSK (low pass with 12 kHz cutt-off frequency) 
    * note: filtering is performed after Xlating so thaht we can display filtered and unfiltered signals on the same spectrum.
  * display the constellation of the demodulated channel (constellation are now different from a _perfect constellation_ , this is due to the low pass filter.


Simulate this flowgraph. By default the QPSK signal is demodulated. 
  * observe that the constellation is close to the QPSK one (excepted for filter effect, at this point we should use Nyquist filters to recover the QPSK constellation)
  * Slowly increase Xlating filter frequency offset and observe the spectrum shift
  * Select the Xlating filter frequency offset to demodulate the BPSK (25kHz) and observe how it is uneasy or impossible to get a correct constellation. 
    * You should get approximately 2 set of points, but these point are shift as compared to the perfect BPSK constellation. This is normal, we compensate frequency while in a real receiver it is necessary to compensate both frequency and phase shift
    * In fact simulating a delta_f frequency which change during simulation is equivalent to having a phase **and** a frequency shift.


### Asynchronism in real hardware
The above simulation have explained the basis of asynchronism found in any hardware Tx/Rx and some basic method to recover synchronism. 
However keep in mind that dealing with real hardware is more complicated then described here. The frequency and phase shift between emitter and receiver should be considered as dynamic and changing continously and randomly with time. 
As a consequence, we need more robust block to continuously synchronize emitter and receiver. Fortunately, digital signal processing offer many solutions to these impairments. Curious reader are encouraged to read the excellent [GNURadio Guided Tutorial on PSK Demodulation](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_PSK_Demodulation "Guided Tutorial PSK Demodulation"). 
## References
Further reading for complex equivalent baseband signal: 
  * [1] Proakis J., _Digital Communication_ , McGraw Hill Series in Electrical and Computer Engineering, Singapore, 1989
  * [2] Gallager R., _Principles of digital communication_ , Cambridge University Press Cambridge, UK, 2008
  * [3] Benedetto S. and Biglieri E., _Principles of digital transmission : with wireless applications_ , Kluwer Academic/Plenum Publishers, NY, 1999


## About figures
Most figures were generated from .grc flowgraph referenced in the text, and from .odg and .tex file. For completeness, these files are included in the following compressed archive [IQ_complex_tutorial_files.zip.grc](https://wiki.gnuradio.org/images/d/d0/IQ_complex_tutorial_files.zip.grc "IQ complex tutorial files.zip.grc") so that anyone can easily improve this tutorial. If you modify some figure, please update this archive too. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&oldid=15457](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&oldid=15457)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=IQ+Complex+Tutorial "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:IQ_Complex_Tutorial "Discussion about the content page \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial)
  * [View source](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&action=history "Past revisions of this page \[h\]")


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
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/IQ_Complex_Tutorial "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/IQ_Complex_Tutorial "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&oldid=15457 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=IQ_Complex_Tutorial&action=info "More information about this page")


  * This page was last edited on 25 November 2025, at 16:19.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


