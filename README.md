# pmaports — LG V30 (joan) fork

A fork of [postmarketOS pmaports](https://gitlab.postmarketos.org/postmarketOS/pmaports)
carrying the device and kernel packages for the **LG V30** (`lge-joan`, msm8998)
mainline port. Upstream's own README follows below.

| package | what it is |
|---|---|
| `device/testing/device-lge-joan` | H930, US998, H932PR and every other non-H932 — depends on `firmware-lge-joan-h930` |
| `device/testing/device-lge-joan-h932` | exact T-Mobile H932 only — depends on `firmware-lge-joan-h932` |
| `device/testing/linux-lge-joan` | mainline kernel, pinned to a commit of [`ShapeShifter499/linux-lg-v30-joan`](https://github.com/ShapeShifter499/linux-lg-v30-joan) |
| `device/testing/firmware-lge-joan` | text-only recipe: shared GPU/BT plus `-h930` / `-h932`. Fetches [`firmware-lge-joan-blobs`](https://github.com/ShapeShifter499/firmware-lge-joan-blobs) at a commit pin. **No owner tarball, no copy-in.** |
| `device/testing/alsa-ucm-conf-lge-joan` | ALSA UCM so PipeWire sees the jack instead of dummy output |
| `device/testing/joan-imsd` | 3GPP IMS SIP UA (VoLTE). OpenRC `joan-imsd`, CLI `joan-ims dial` |
| `device/testing/lge-joan-volte` | first-boot metapackage: MM + 81voltd + rmtfs + calls + joan-imsd |

Everything else in this tree is unmodified upstream pmaports. The GPU/display
enablement lives in that kernel pin, not as a carried patch series here.

## Building for the LG V30

This tree is self-contained for a first image. The kernel tarball, firmware
**recipe**, ALSA UCM profile, and VoLTE stack are in-tree. Proprietary firmware
blobs are fetched at build time from the commit pin in
`firmware-lge-joan/APKBUILD`. There is **no** `owner-firmware-lge-joan.tar`
to prepare and **no** extra clone/copy.

You need pmbootstrap, roughly 25 GB of free space for the kernel build, and a
V30 with an unlocked bootloader. The H932 has no usable `fastboot boot` /
`fastboot flash`; see Caveats.

### 1. Point pmbootstrap at this fork

```sh
git clone https://github.com/ShapeShifter499/pmaports-lge-joan
cd pmaports-lge-joan
git remote add upstream https://gitlab.postmarketos.org/postmarketOS/pmaports.git
git fetch --depth 1 upstream main
cd ..
pmbootstrap -p "$PWD/pmaports-lge-joan" init
```

Both git commands are required. pmbootstrap needs upstream pmaports reachable
from this clone for two separate reasons, and skipping either gives a
different error.

It identifies a pmaports checkout by searching the remotes for one whose
**URL** is upstream pmaports. A plain clone of this fork only carries the
GitHub URL, so without `git remote add`:

```
ERROR: pmaports: could not find remote name for any URL
'['https://gitlab.postmarketos.org/postmarketOS/pmaports.git', ...]'
in git repository: /path/to/pmaports-lge-joan
```

It then reads the release channel definitions with
`git show <remote>/main:channels.cfg`, which needs that remote actually
fetched. With the remote added but not fetched:

```
ERROR: Failed to read channels.cfg from 'upstream/main' branch of your
local pmaports clone
```

The remote's name does not matter — only its URL is matched. `--depth 1` is
enough; the full upstream history is not needed. If you have already hit
either error, run both commands in your existing clone and re-run `init`.

Answer the device prompts with vendor `lge`, and pick the codename that matches
your handset:

| codename | for |
|---|---|
| `joan` | H930, US998, H932PR and every other V30 |
| `joan-h932` | an **exact LG-H932** (T-Mobile) |

The H932 is signed with different keys, so it needs its own firmware: measured
against stock images of the same Android release and the same Qualcomm build,
38 of 47 firmware files differ. This is the same distinction LineageOS makes by
detecting an H932 while flashing; here it is just a different device package,
and the correct firmware follows automatically.

`LG-H932PR` is **not** an H932 and uses `joan`. If you are unsure, check
`androidboot.vendor.lge.model.name` in `/proc/cmdline` on the stock ROM.

`-p/--aports` has to be repeated on every later `pmbootstrap` call. To avoid
that, set it once instead:

```sh
pmbootstrap config aports "$PWD/pmaports-lge-joan"
```

It is stored in `~/.config/pmbootstrap_v3.cfg`.

`joan` pulls `firmware-lge-joan-h930`; `joan-h932` pulls
`firmware-lge-joan-h932`. Do not install both. The firmware recipe is already
in this tree; `pmbootstrap install` fetches
[`firmware-lge-joan-blobs`](https://github.com/ShapeShifter499/firmware-lge-joan-blobs)
itself. Do **not** copy `ShapeShifter499/firmware-lge-joan` into pmaports —
that is the retired owner-tarball recipe and is the source of:

```
sha512sum: can't open '.../owner-firmware-lge-joan.tar': No such file or directory
ERROR: Couldn't build aarch64/firmware-lge-joan-*.apk
```

Optional extra-packages working copies still live in
[`lg-v30-joan-pmos-packages`](https://github.com/ShapeShifter499/lg-v30-joan-pmos-packages);
they are already vendored here, so a first image does not copy them in.

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

On an **H932**, `fastboot boot` and `fastboot flash` both return
`unknown command`. Do not use `pmbootstrap install --android-recovery-zip` —
that installer repartitions `system` and destroys a LineageOS install. The
working H932 path is a microSD rootfs plus writing `boot.img` from a rooted
Android/`dd` shell (or the laf slot). US998 still has usable fastboot.

## Packages that live in the other repo

The firmware **recipe**, ALSA UCM, and VoLTE stack are in this fork. Proprietary
blobs are **not** — they are fetched at build time from
[`firmware-lge-joan-blobs`](https://github.com/ShapeShifter499/firmware-lge-joan-blobs).
Working copies of the extra packages still live in
[`ShapeShifter499/lg-v30-joan-pmos-packages`](https://github.com/ShapeShifter499/lg-v30-joan-pmos-packages);
a first `pmbootstrap install` does not copy them in.

A first image already has GPU, Bluetooth, modem, ADSP, IPA, WLAN, zap, the
joan UCM profile, and the VoLTE metapackage (`lge-joan-volte` → `joan-imsd`,
ModemManager, 81voltd, rmtfs, Calls). First-boot IMS steps:
`device/testing/lge-joan-volte/FIRST-INSTALL-VOLTE.md`.

## Caveats

Read these before filing a bug.

* This is `device/testing`. It is not in upstream pmaports and gets no upstream
  CI or review.
* The kernel is **pinned to one commit**, not tracking that repo's `master`.
  You get exactly that snapshot.
* `deviceinfo` sets Linux Foundation USB gadget IDs (`1d6b:0104`) so a booted
  pmOS gadget is not `lsusb`'s "Nexus 4 (fastboot)" (`18d1:d001`).
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
