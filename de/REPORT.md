# Der Bericht auf einer Seite

[English](../REPORT.md) | **Deutsch**

Über `lsdsk report` kann man diese Seite ausgeben, und der Exit-Code hängt von den Befunden ab.

Nichts muss ausgewählt und kein Unterbefehl erraten werden. Wer noch nicht
weiss, was defekt ist, muss also auch nicht wissen, wonach er fragen soll. Was
defekt ist, steht zuerst, sodass schon der erste Bildschirm alles Handelnswerte
zeigt; und jeder Befehl in der Tabelle ist ein Abschnitt desselben Berichts, für
den Fall, dass Sie bereits wissen, welchen Sie brauchen.

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

Drei Geschwindigkeiten pro Laufwerk, weil sie verschiedene Fragen beantworten.
`port` ist, was der Steckplatz hergibt, `disk`, was das Laufwerk könnte, und
`link`, worauf die beiden sich tatsächlich geeinigt haben. Ein oranges `disk`
heisst, dass das Laufwerk den Anschluss nicht ausnutzen kann, den es belegt: eine
Frage der Bestückung. Ein rotes `link` heisst, dass beide Enden schneller
gekonnt hätten und es nicht getan haben: ein Fehler.
