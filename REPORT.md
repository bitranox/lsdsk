# The one-page report

What `lsdsk` prints when nobody is sitting at the terminal, and what to ask for
when somebody is but the caller is not a person. Back to the [README](README.md).

`uvx lsdsk@latest report` asks for that page by name, whatever the terminal
looks like, and the exit code is the findings' either way.

Ask for it explicitly wherever a terminal is allocated but nobody is watching -
a notebook cell, `script`, expect, a job runner that gives its children a pty.
lsdsk cannot tell those from somebody sitting at a shell, and a full-screen view
there waits for a keypress that never comes.

Nothing has to be selected and no subcommand has to be guessed, so
somebody who does not yet know what is wrong does not have to know what to ask
for. What is wrong comes first, so stopping after the first screen still shows
everything actionable, and each command in the table below is one section of the
same report for when you already know which one you want.

```
lsdsk  linux-sas-hba   19 disks on 5 controllers

    PROBLEMS      7 warning   6 hint
 !  /dev/nvme0n1  SAMSUNG MZVPV512HDGL-00000 logged 2 media errors
 !  /dev/sdc      Samsung SSD 870 EVO 4TB has 99345 interface CRC errors
 !  /dev/sdd      Samsung SSD 870 EVO 4TB has 2179485 interface CRC errors
 !  /dev/sde      Samsung SSD 870 EVO 500GB has 430 interface CRC errors
 !  /dev/sdj      Samsung SSD 870 EVO 4TB has 462640 interface CRC errors
 !  /dev/sdl      Hitachi HDS722020ALA330 has 4 reallocated sectors
                  and 7 more, run `lsdsk findings`

Topology on linux-sas-hba
showing storage and the bridges above it; --tree-density to change the detail level
legacy = no PCIe capability
linux-sas-hba   2 root complexes (0000:00, 0000:ff)   root ports to PCIe Gen3x8   95 PCI devices
   │     address       capable                running                name
   ├─┬── 0000:00:01.0  Gen3x4 (3.94 GB/s)     Gen3x4 (3.94 GB/s)     Intel Corporation Xeon E7 v2/Xeon E5 v2/Core i7 PCI Express Root>
   │ └── 0000:05:00.0  Gen3x4 (3.94 GB/s)     Gen3x4 (3.94 GB/s)     Samsung Electronics Co Ltd NVMe SSD Controller SM951/PM951
   │     device        model             size  kind  bus   port                disk                link                temp  worn
!  │     /dev/nvme0n1  SAMSUNG MZVPV>  477GiB  SSD   NVME  Gen3x4 (3.94 GB/s)  Gen3x4 (3.94 GB/s)  Gen3x4 (3.94 GB/s)   39C   59%
   │     address       capable                running                name
   ├─┬── 0000:00:03.0  Gen3x8 (7.88 GB/s)     Gen3x8 (7.88 GB/s)     Intel Corporation Xeon E7 v2/Xeon E5 v2/Core i7 PCI Express Root>
~  │ └── 0000:03:00.0  Gen4x8 (15.75 GB/s)    Gen3x8 (7.88 GB/s)     Broadcom / LSI Fusion-MPT 12GSAS/PCIe Secure SAS38xx
   │     device        model             size  kind  bus   port                disk                link                temp  worn
~  │     /dev/sda      Samsung SSD 8>  3.6TiB  SSD   SATA  12G (1.20 GB/s)     6G (0.60 GB/s)      6G (0.60 GB/s)       36C    1%
~  │     /dev/sdb      Samsung SSD 8>  466GiB  SSD   SATA  12G (1.20 GB/s)     6G (0.60 GB/s)      6G (0.60 GB/s)       30C    2%
   │     ...

Controllers on linux-sas-hba        ...
Disks on linux-sas-hba              ...
Disk health on linux-sas-hba        ...
SMART attributes on linux-sas-hba   ...
mainboard not named by firmware     13 ports   2 free   ...
Counter trends on linux-sas-hba     ...
Findings on linux-sas-hba           ...
```

Three speeds per drive, because they answer different questions. `port` is what
the seat can give, `disk` is what the drive can do, and `link` is what the two
of them actually agreed on. An orange `disk` means the drive cannot use the port
it occupies, which is a placement question. A red `link` means both ends could
have gone faster and did not, which is a fault.

`lsdsk report` answers the one thing the terminal test cannot see. Some callers hand
their child a pseudo-terminal on both ends - a notebook cell, `script`, `expect`,
a job runner - and there nothing distinguishes a program from a person, so the
interactive view opens and waits for a keypress nobody can send. Name the page
in anything unattended rather than reasoning about whether that caller counts as
a terminal.
