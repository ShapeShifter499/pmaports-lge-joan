# pmaports — LG V30 (joan) fork

A fork of [postmarketOS pmaports](https://gitlab.postmarketos.org/postmarketOS/pmaports)
carrying the device and kernel packages for the **LG V30** (`lge-joan`, msm8998)
mainline port. Upstream's own README follows below.

| package | what it is |
|---|---|
| `device/testing/device-lge-joan` | device package and `deviceinfo` — fastboot, `qcom/msm8998-lge-joan` |
| `device/testing/linux-lge-joan` | mainline kernel, pinned to a commit of [`ShapeShifter499/linux-lg-v30-joan`](https://github.com/ShapeShifter499/linux-lg-v30-joan), with the joan GPU/display enablement series carried as patches |

Everything else in this tree is unmodified upstream pmaports.

## Building for the LG V30

This tree is self-contained. Every source is public and commit-pinned, and
nothing points at a local path, so a clone builds on any machine that can run
[pmbootstrap](https://wiki.postmarketos.org/wiki/Pmbootstrap).

You need pmbootstrap, roughly 25 GB of free space for the kernel build, and a
V30 with an unlocked bootloader.

### 1. Point pmbootstrap at this fork

```sh
git clone https://github.com/ShapeShifter499/pmaports-lge-joan
pmbootstrap -p "$PWD/pmaports-lge-joan" init
```

Answer the device prompts with vendor `lge` and codename `joan`.

`-p/--aports` has to be repeated on every later `pmbootstrap` call. To avoid
that, set it once instead:

```sh
pmbootstrap config aports "$PWD/pmaports-lge-joan"
```

It is stored in `~/.config/pmbootstrap_v3.cfg`.

### 2. Build the rootfs

```sh
pmbootstrap install
```

This builds `linux-lge-joan` from the pinned kernel tarball, which is the long
part of the run.

### 3. Flash

`deviceinfo` selects `fastboot`, and the kernel is packed into a boot image
with the device tree appended.

```sh
pmbootstrap flasher flash_kernel
pmbootstrap flasher flash_rootfs
```

To try a kernel without writing to the boot partition:

```sh
pmbootstrap flasher boot
```

## Packages that live in the other repo

Firmware, the ALSA UCM profile, and the VoLTE stack are **not** in this fork.
They are in
[`ShapeShifter499/lg-v30-joan-pmos-packages`](https://github.com/ShapeShifter499/lg-v30-joan-pmos-packages):

| package | why you want it |
|---|---|
| `firmware-lge-joan` | GPU, Bluetooth, modem, ADSP, IPA and WLAN firmware |
| `alsa-ucm-conf-lge-joan` | without it PipeWire shows "dummy output" and no audio devices |
| `joan-imsd`, `lge-joan-volte` | IMS/VoLTE — see that repo's `FIRST-INSTALL-VOLTE.md` |

`device-lge-joan` on this branch does **not** depend on any of them, so a plain
`pmbootstrap install` produces an image without firmware. (It does depend on the
redistributable `firmware-qcom-adreno-a530` from upstream, which supplies the
A530 PM4/PFP command processor firmware the GPU needs; the A540 GPMU and the
signed ZAP shader firmware still come from `firmware-lge-joan`.) To include
them, copy the package directories into this checkout and build them by name:

```sh
git clone https://github.com/ShapeShifter499/lg-v30-joan-pmos-packages
cp -r lg-v30-joan-pmos-packages/firmware-lge-joan \
      lg-v30-joan-pmos-packages/alsa-ucm-conf-lge-joan \
      pmaports-lge-joan/device/testing/
pmbootstrap build firmware-lge-joan alsa-ucm-conf-lge-joan
```

then add them to `deviceinfo`'s package list or install them on the device with
`apk add`.

The firmware is proprietary and is **not redistributed here**. `firmware-lge-joan`
fetches the remotely available files at build time from commit-pinned
[TheMuppets](https://github.com/TheMuppets) vendor trees; the modem/ADSP/IPA/WLAN
set comes from an owner-extracted, hash-verified tarball that each builder
prepares from their own device, following that repo's README. Two GPU variants are
carried: `H932` for that exact model, and `H930` for every other joan including
`H932PR`. Override with `pmos.joan_firmware_variant=h930|h932` on the kernel
command line.

## Caveats

Read these before filing a bug.

* This is `device/testing`. It is not in upstream pmaports and gets no upstream
  CI or review.
* The kernel is **pinned to one commit**, not tracking that repo's `master`.
  You get exactly that snapshot.
* `deviceinfo` sets no USB gadget IDs, so the pmOS default `18d1:d001` applies —
  `lsusb` reports that as "Nexus 4 (fastboot)", meaning a booted pmOS gadget
  looks identical to the bootloader. The other repo ships a
  `deviceinfo-usb.snippet` with Linux Foundation IDs you can paste in.
* VoLTE is a work in progress. It has been carried through a live call on one
  network; other carriers and USIM-based SIMs need more testing and logs.
* Hardware support is moving. For what actually works today, check
  [`ShapeShifter499/lg-v30-port`](https://github.com/ShapeShifter499/lg-v30-port)
  rather than trusting a status table here.

## Related repositories

* Kernel — [`ShapeShifter499/linux-lg-v30-joan`](https://github.com/ShapeShifter499/linux-lg-v30-joan)
* Firmware, audio, VoLTE packages — [`ShapeShifter499/lg-v30-joan-pmos-packages`](https://github.com/ShapeShifter499/lg-v30-joan-pmos-packages)
* Port notes and bring-up history — [`ShapeShifter499/lg-v30-port`](https://github.com/ShapeShifter499/lg-v30-port)

---

# postmarketOS aports repository

This repository contains the APKBUILD files for postmarketOS-specific packages, along with the required patches and scripts, if any.

There are many more packages defined in the [Alpine Linux aports](https://gitlab.alpinelinux.org/alpine/aports/) on which these packages depend.

Helpful resources:

* [Issues](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/work_items)
* [How to create a package](https://wiki.postmarketos.org/wiki/Create_a_package)
* [APKBUILD reference](https://wiki.alpinelinux.org/wiki/APKBUILD_Reference)
* [pmaports commit style](./COMMITSTYLE.md)
* [Approval rules](docs/merge-requests/approval-rules.md)
* [Alpine Linux aports](https://gitlab.alpinelinux.org/alpine/aports/)
* [Alpine Linux package search](https://pkgs.alpinelinux.org/packages)
* [postmarketOS package search](https://pkgs.postmarketos.org/packages)

## Git Hooks

You can find some useful git hooks in the `.githooks` directory.
To use them, run the following command after cloning this repository:

```sh
git config --local core.hooksPath .githooks
```
