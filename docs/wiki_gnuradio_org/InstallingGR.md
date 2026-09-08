# InstallingGR


|  **Beginner Tutorials** Introducing GNU Radio
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. Installing GNU Radio
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
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |
| --- |

## Quick Start
| Platform  | Method  | GNU Radio version   |
| --- | --- | --- |
|   **Debian** ≥12
 **Ubuntu** ≥22.04
 **Linux Mint** ≥21.1
**Raspberry Pi OS** 64-bit ≥2021-10-30   |
```
sudo apt-get install gnuradio

```
 |  Ubuntu 24.10: v3.10.11.0  |
|  Ubuntu 24.04: v3.10.9.2  |
|  Ubuntu 22.04: v3.10.1.1  |
|  Debian 12: v3.10.5.1  |
|   **Fedora** ≥39   |
```
sudo dnf install gnuradio

```
 |  Fedora 40: v3.10.9.2  |
|  Fedora 41, 42…: v3.10.11.0  |
|   **Ubuntu** 20.04
Strongly recommended: [upgrade](https://ubuntu.com/tutorials/upgrading-ubuntu-desktop#1-before-you-start) your Ubuntu.
20.04 left supported lifetime May 2025.

 **Ubuntu** 22.04   |
```
sudo add-apt-repository ppa:gnuradio/gnuradio-releases
sudo apt-get update
sudo apt-get install gnuradio python3-packaging

```
 | v3.10.7.0   |
|   **Other Linux Distros**  |
```
sudo {apt,dnf,yay,emerge,…} install gnuradio

```
 | See [this table](https://repology.org/project/gnuradio/badges)  |
|   **Windows**  |  Download and install [Radioconda](https://github.com/ryanvolz/radioconda) by following the instructions at the link
and launch "GNU Radio Companion" from the Start menu   | v3.10.12.0   |
|   **macOS**  | Download and install [Radioconda](https://github.com/ryanvolz/radioconda) by following the instructions at the link   |
## Other Installation Methods
## Platform-specific guides
(Both source builds and binary installation methods)
  * [Linux install guide](https://wiki.gnuradio.org/index.php?title=LinuxInstall "LinuxInstall")
  * [Windows install guide](https://wiki.gnuradio.org/index.php?title=WindowsInstall "WindowsInstall")
  * [Mac OS X install guide](https://wiki.gnuradio.org/index.php?title=MacInstall "MacInstall")

## Cross-platform guides
  * [Conda install guide](https://wiki.gnuradio.org/index.php?title=CondaInstall "CondaInstall")
  * [PyBOMBS](https://github.com/gnuradio/pybombs#pybombs) - Note: We are no longer including PyBOMBS as a recommended method of installing GNU Radio, unless you want to play around with old versions (e.g. GR 3.7, 3.8, and OOTs of matching version)

## VMs and Live Images
Over the years a number of Live Images and VMs have been created. There are currently no official versions but here are some current options:
  * (**obsolete**)[Instant GNU Radio](https://github.com/bastibl/instant-gnuradio) A customizable, programmatically generated VM and live environment for GNU Radio.
  * (**obsolete**)[UbuntuVM](https://wiki.gnuradio.org/index.php?title=UbuntuVM "UbuntuVM") An Ubuntu 20.04 virtual machine image with GNU Radio 3.8.2.0, Fosphor, GQRX, and several other useful pieces of software. (Created using Instant GNU Radio)

## OK, it's installed, what now?
If the installation worked without any trouble, you're ready to use GNU Radio! If you have no idea how to do that, the best place to start is with the [Tutorials](https://wiki.gnuradio.org/index.php?title=Tutorials "Tutorials").
Optionally, you may run `volk_profile` on your terminal to help libvolk to determine the optimal kernels (may speed up GNU Radio).

[Categories](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Installation](https://wiki.gnuradio.org/index.php?title=Category:Installation "Category:Installation")
  * [Guide](https://wiki.gnuradio.org/index.php?title=Category:Guide "Category:Guide")
