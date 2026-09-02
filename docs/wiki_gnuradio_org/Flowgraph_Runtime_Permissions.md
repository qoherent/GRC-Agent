# Flowgraph Runtime Permissions

Two classes of runtime failure look like flowgraph defects but are operating-system
permission problems. Neither is fixed by changing the graph, and neither should be
fixed by running GNU Radio as root.

## SDR USB permission errors (LIBUSB_ERROR_ACCESS)

A flowgraph using UHD/USRP, RTL-SDR, HackRF, bladeRF or ADALM-PLUTO hardware that
fails at start with `LIBUSB_ERROR_ACCESS`, `usb_open failed`, or `Permission denied`
on the USB device is being refused by udev, not by GNU Radio. The user's account
lacks write access to the device node.

Do not run the flowgraph with `sudo`. Install the driver's udev rules package
instead — `uhd-host` for USRP, `rtl-sdr` for RTL-SDR dongles, `hackrf` for HackRF —
then reload the rules and reconnect the hardware:

```
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and replug the device afterwards so the new rules apply to a fresh device
node. Adding the user to broad groups such as `plugdev`, `dialout` or `usrp` is a
blunter workaround and is not needed once the vendor rules are installed.

## TUN/TAP allocation failures (TUNSETIFF, EPERM)

A flowgraph using `network_tuntap_pdu` or another TUN/TAP block that fails with
`tun_alloc`, `TUNSETIFF`, or `EPERM` while allocating the interface is missing the
`CAP_NET_ADMIN` capability. This is a permission fault, not a graph defect.

Creating a *named* network interface requires `CAP_NET_ADMIN`. A world-accessible
`/dev/net/tun` does not substitute for it — the device node permits opening the
driver, not creating an interface.

The remedy is to create the interface once, outside the application, and let the
flowgraph attach to it unprivileged:

```
sudo ip tuntap add dev tap0 mode tap user $USER
```

Match the block's `ifname` parameter and its mode: `tap` for TAP (Ethernet frames),
`tun` for TUN (IP packets). Once the interface exists and is owned by the user, the
flowgraph attaches to it with no elevated privileges at all.

The interface survives flowgraph restarts but not a reboot. Re-create it after each
boot, or make it durable with a systemd unit.

Do not advise running the application as root, and do not `setcap` the Python
interpreter — that grants every script the interpreter runs the same capability.
The posture is the same as for SDR USB errors: fix the permission out of band, then
run unprivileged.
