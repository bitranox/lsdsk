# ADR 0002: Reading the USB Link of a USB-Attached Disk

**Status:** Accepted

## Context

A disk reached over USB has two links: the USB link between the machine and the bridge in the
enclosure, and the drive's own link behind that bridge (SATA, or PCIe for an NVMe drive). lsdsk
reads the second where the bridge passes ATA IDENTIFY through SAT, and reads nothing of the first.
So the most common USB fault - a USB3 drive that came up at USB2 speed because of a cable, a hub or
a half-seated plug, roughly forty times slower - is invisible, and on Linux such a disk is reported
as `sata` or `unknown` while Windows reports the same disk as `usb`.

Both platforms publish the USB link without privilege:

- Linux sysfs carries the running rate (`speed`) and lane counts (`rx_lanes`, `tx_lanes`) for every
  USB device, the device's own capability in `bos_descriptors` (kernel 6.7 and later), and a `peer`
  link from each USB3 hub port to its USB2 twin.
- Windows answers the parent hub's per-port queries: `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX`
  for the running speed, `..._EX_V2` for the supported protocols and the capable-of and
  operating-at SuperSpeed and SuperSpeedPlus flags, and
  `IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION` for lane rate and lane count.

## Decision

**Scope.** Linux and Windows in one release. Per USB disk: the running rate, what the device can
do, what the socket it is plugged into can do, the slowest running hub between that socket and the
root, whether it came up on the USB2 half of a USB3 socket, and the transport (UAS or BOT). No
advice to move a disk to a faster free socket: a USB port cannot be tied to a physical connector
reliably enough to satisfy the rule that a move is proposed only where the hardware says a real
connector exists.

**Capture.** A new `usb` section in both capture formats, empty by default so every existing capture
still loads. Linux records the sysfs attributes above for every USB device on the chain from the
disk to its root hub, the bound driver, and the port's `peer` path. Windows records the raw IOCTL
output buffers for the disk's port on its parent hub, and that hub's own port on the hub above it.
Decoding stays pure in `adapters/hw/decode/usb.py`, so the Windows mapping is tested on Linux as it
is today. A refused IOCTL is a `RefusedReading` named `usb-link`. A missing `bos_descriptors` on an
older kernel is not a refusal: nothing was refused, the capability is simply unread.

**Model.** `Disk` gains `usb: UsbLink | None`, beside `link` (the drive's own SATA figures from
IDENTIFY, unchanged) and `pcie`. `UsbLink` carries `running`, `device_max`, `port_max`, `upstream`,
`on_usb2_twin` and `transport`. Rates are a `UsbRate` enum - USB12M, USB480M, USB5G, USB10G,
USB20G - each with its usable bandwidth after the line encoding the specification defines (8b/10b
for 5 Gb/s, 128b/132b for 10 and 20 Gb/s). `achievable` requires both `device_max` and `port_max`,
exactly as `InterfaceLink.achievable_gbps` does: an end that was not read is never a capable end.
`Disk.bus` is `usb` on both platforms for a drive reached through a USB device.

**Spelling.** One closed form everywhere a figure is drawn - tables, the detail panel and a
finding's sentence: `USB10G (1.21 GB/s)`. It follows the rate printed on the box, as `Gen3x8` does
for PCIe, and the prefix keeps a USB rate from being read as a SATA rate in the same column.

**Rendering.** For a USB disk the disks table's `port`, `disk` and `link` columns show `port_max`,
`device_max` and `running`, coloured by the same three-number rule as a SATA or PCIe row. The
drive's own SATA figures move to the detail panel and the JSON. The panel names every USB field;
an upstream hop that was not read is `-`, a disk directly on the root hub has no upstream and is
`n/a`.

**Findings.** Graded by the existing three-number rule and the existing test for whether the drive
behind the link would notice the cap:

| Condition                                                    | Severity                                                 |
|--------------------------------------------------------------|----------------------------------------------------------|
| SuperSpeed-capable disk running on the USB2 twin of its port | warning, actionable: reseat it, check the cable or hub   |
| Running below what both the disk and the socket support      | warning, actionable                                      |
| The socket or a hub above it is the ceiling                  | warning if the drive behind would notice, otherwise hint |
| An end was not read                                          | no fault claimed; the shortfall is shown, unattributed   |

Hubs are never the subject of a finding. A USB3 hub enumerates its USB2 half as a separate device
that runs at 480 Mb/s while its capability descriptor reports SuperSpeed, so judging hubs would
raise a false warning on every USB3 hub in a machine.

## Consequences

- The cable and hub faults users actually meet on USB become findings with a concrete action.
- One disk reads the same on both platforms. On Linux, `bus` changes from `sata` or `unknown` to
  `usb` for these disks, which is visible to scripts filtering on it; this is why the change ships as
  a minor version.
- An NVMe drive behind a USB bridge stays opaque: no standard passthrough reaches it, and vendor
  commands per bridge chip are out of scope. Its USB link is still read.
- A USB4 or Thunderbolt enclosure presents its drive as PCIe NVMe, so it stays on the PCIe path and
  is not covered here.
- Two new identifiers have to be scrubbed from a capture before it becomes a fixture: the Windows
  USB instance ID embeds the device serial, and the BOS Container ID record is a per-device UUID.
  `tests/test_fixture_serials.py` checks both.
- Kernels before 6.7 report a USB disk's own capability as unread, so there no shortfall is blamed
  on either end.
- The design rests on one Windows capture of a UAS SSD and one Linux capture of a USB disk, both
  taken before any reader code is written, which also confirm every field above is published.
