# firmware-lge-joan

Firmware for the LG V30 (joan): a shared base plus one of two signing-family
packages.

| package | contents |
|---|---|
| `firmware-lge-joan` | A540 GPMU + QCA Bluetooth — identical on every joan |
| `firmware-lge-joan-h930` | modem, ADSP, IPA, WLAN, zap — **H930, US998, H932PR, all others** |
| `firmware-lge-joan-h932` | the same set for an **exact LG-H932** |
| `firmware-lge-joan-initramfs` | mkinitfs list for early GPU/BT firmware |

Install the base and exactly one family:

```sh
apk add firmware-lge-joan firmware-lge-joan-h930   # almost everyone
apk add firmware-lge-joan firmware-lge-joan-h932   # an exact LG-H932
```

They conflict, and there is deliberately no default. Both warn on install and
read the bootloader model from `/proc/cmdline` to tell you if you picked wrong.
`LG-H932PR` is **not** an H932 and takes h930.

The device packages in
[`pmaports-lge-joan`](https://github.com/ShapeShifter499/pmaports-lge-joan)
already pull the right pair: pick `joan` or `joan-h932` at `pmbootstrap init`.
That fork vendors this recipe under `device/testing/firmware-lge-joan`, so a
clone of pmaports does not need a copy-in.

## Why the split

The H932 is the T-Mobile model, signed with different keys. Measured against
stock firmware of the same Android release — `US99830b` and `H93230d`, both
Pie, both reporting Qualcomm build `MPSS.AT.2.5.c1.2-00056` — **38 of 47 files
differ**. Same build, different signatures.

The nine files that do match are individual segments and cannot be reused: an
image's `.mdt` signs the segment hash table, so a set has to come from one
device. LineageOS draws the same line by detecting an H932 at flash time; with
a package manager it is just a package.

The one measured exception is the zap shader, where the h930 payload is known
to run on a US998 — verified on hardware. For the modem set nobody has booted
one family's images on another model, so h930 is the *family* set, proven on
US998 and inferred for H930 and H932PR.

## Where the firmware comes from

All of it is mirrored into
[`firmware-lge-joan-blobs`](https://github.com/ShapeShifter499/firmware-lge-joan-blobs)
and pinned by commit, so a build depends on no third party staying reachable
and **does not need an owner-extracted tarball**. The GPU and Bluetooth files
originate from commit-pinned TheMuppets vendor trees; the rest comes off retail
devices.

Integrity is checked twice: the archive by `sha512`, then every file against
`MANIFEST.tsv` by `sha256` before it is installed.

WLAN `board.bin` is the stock generic board data from each variant's system
image, not per-unit factory calibration from any particular handset.

There is no `owner-firmware-lge-joan.tar`. If a build asks for that file, the
recipe is stale — use this package (pkgrel 8+), not
`ShapeShifter499/firmware-lge-joan`.

## Building

This directory is a standard Alpine `APKBUILD`. It is already in
`pmaports-lge-joan/device/testing/firmware-lge-joan`. Edit it there, or copy
this working copy over that path, then:

```sh
pmbootstrap checksum firmware-lge-joan   # only if you edited the recipe
pmbootstrap build firmware-lge-joan
```

`pmbootstrap build` fetches
`firmware-lge-joan-blobs` at the commit pinned in `APKBUILD`. A missing
`owner-firmware-lge-joan.tar` means you copied an old recipe.

## Licence

`LICENSE` and `NOTICE` install to `/usr/share/licenses/firmware-lge-joan/`, the
files and layout `firmware-qcom-adreno` uses. The Qualcomm licence permits
binary redistribution on condition the terms file ships with it and notices are
not removed. The zap shader is LG-signed rather than Qualcomm's, and these
images came off retail devices rather than from QTI.
