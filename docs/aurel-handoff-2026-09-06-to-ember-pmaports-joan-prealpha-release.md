# Aurel → Ember handoff: pmOS joan prealpha release — bench test + release gate

- **Author:** Aurel Nymvale
- **Harness:model:** Hermes-Agent:deepseek/deepseek-v4-pro
- **Date:** 2026-09-06 (rev 1)
- **To:** Ember

## Context

Lance's standing goal: a **prealpha pmOS release for the LG V30 (joan)** — SD-card flavor, internal flavor, and advanced loose images, for global joan **and** H932. This alpha exists because someone in the Telegram LG V30 chat wanted to run the stuff we know works. Scope is deliberately **known-working only**; every broken feature is disclosed in plain terms. Lance flashes and tests himself.

Hard stop rules (verbatim intent, binds the recipient):
- **"Don't push anything over, stage it like someone who is about to install this build. I'll flash it myself and test it"** — no GitHub release posting until Lance reviews.
- **"Run it by me before posting to releases"** — present the draft release (notes + assets + hashes) to Lance and get his go before publishing anything.
- The pmaports **fork** (`ShapeShifter499/pmaports-lge-joan`) is the approved push target for code/docs. Release zips are NOT pushed anywhere yet — they are staged, awaiting bench qualification.

## Repo + tree state (verified fresh at handoff time)

- **Fork** `~/vibe-coding-projects/coding/pmaports-lg-v30-clean`, branch `joan/readme-build-guide`, tip `3bbdff8cf5`, **clean tree**, pushed.
- **Prealpha workspace** `~/vibe-coding-projects/coding/lg-v30-pmos-prealpha`, tip `bea04e2`, **clean tree**, local-only (no remote). Release artifacts under `out/release/`.
- **pmbootstrap.cfg** is already `device = lge-joan` (Lance flipped it back himself at ~17:35). No cleanup needed.
- Build cache: `/data/buildcache/pmbootstrap-lg-v30-prealpha-v8/`.

## What's done (verified facts, not intentions)

1. **Four recovery-flashable zips, rebuilt 12:22 today with the root-discovery fix** (see below). All four verified just now:
   - zip-root `boot.img` (33,132,544 B) present in all four, and its header cmdline is **label-based** — `panic=5 pmos.force-partition-resize  pmos_rootfsopts=defaults`, no `PARTUUID`/`root=` args.
   - patched `chroot/bin/pmos_install` (5,355 B) present in all four.
   - Hashes (match `out/release/SHA256SUMS` exactly):
     - `lge-joan-prealpha-20260906-recovery-sd.zip` = `7fdeb1b05d08d7c0bbc7b344dc72e9f09cd8894422c62f43f40916b477ed2d91` (global, laf target)
     - `lge-joan-prealpha-20260906-recovery-internal.zip` = `6bed5c8594bfbaf7bee258a3deb1f6e699440f582a1728b103a4667a52f19eeb` (global, boot+userdata)
     - `lge-joan-prealpha-20260906-h932-sdcard.zip` = `999633484b45d1608081a4d5fd8e75b5d908c07f4505bfa26e0459c8d91ac8b2` (H932, laf target)
     - `lge-joan-prealpha-20260906-h932-internal.zip` = `730c720bac9bfc157e7792124929a03a4bedb48de2b3649cb9116e8824bf4705` (H932, boot+userdata)
2. **Global SD zip staged on nym-nest-family** at `~/joan-test-assets/lge-joan-prealpha-20260906-recovery-sd.zip`, sha256 **verified on the remote** = `7fdeb1b0…` (matches local + SHA256SUMS). Ready to sideload.
3. Installer patches shipped in all four zips (from `/tmp/zipfix/patch_installer.py`, idempotent):
   - **subpartition resolution** for `external_sd` uses real `/dev/block/<sd>p{1,2}` nodes (upstream referenced `/dev/mapper/` nodes that never exist — kpartx is skipped).
   - **device-resolution fallback** for LOS-recovery's missing fstab: storage-topology scan of `/dev/block/*` (`removable=1` OR `device/type=SD`, not the eMMC) — joan's kernel lies about `removable`, so topology is required.
   - **kernel-partition resolution** split into guarded single-command substitutions — busybox ash `set -e` kills the shell on a failing `$(...)` even behind `||`; every substitution in fallback paths now ends `|| true` / `|| echo`. This is a latent **upstream** installer bug that only surfaces on recovery-without-fstab installs like ours; worth filing upstream later.
4. Docs in `out/release/`: `ALPHA-STATUS.md` (works / doesn't-work tables: display+GPU, touch, battery %, Wi-Fi scan+connect, BT, cellular data, wired headphones work; cameras, microphones, fingerprint, speaker/earpiece, jack plug detection, calls/VoLTE pending mic path do NOT; GPS/NFC/sensors/vibrator untested), `FLASH-SDCARD.md`, `FLASH-INTERNAL.md`, `ADVANCED-INSTALL.md`, `H932-RECOVERY.md`, `SHA256SUMS`. SD docs carry loud "card is wiped, back it up" + "laf IS download mode — dd-backup laf/lafbak and know the restore path" warnings, per Lance.
5. Fork README + all pmaports commits pushed (trailers on every commit — the commit-msg hook enforces `Signed-off-by: Lance <Gero3977@gmail.com>` + `Assisted-by: Hermes-Agent:<provider>/<model>`; keep using it).

## The blocker just fixed — and the upstream answer Lance asked for

User-reported: **`[pmOS-rd] ERROR: failed to mount subpartitions`** on laf boot.

Root cause: the recovery-zip's bundled boot.img came from pmbootstrap's **cached chroot**, which still held `pmos_boot_uuid=c3de66d4… pmos_root_uuid=bf4a3d78…` baked in from my earlier internal-install build. The on-phone installer fresh-formats partitions with **random new UUIDs** (it only writes labels), so the initramfs's authoritative-UUID lookup found nothing and refused to fall back — by design, UUID-specified roots are authoritative.

Fix shipped: boot.img cmdline is now **label-based** (`pmOS_root`), which survives the installer's fresh formats via the initramfs's label fallback. The patched boot.img ships at **zip root** and the patched installer flashes that one first (falls back to the rootfs-bundled copy if absent).

Is this how upstream does it? **Yes — and we can prove it.** The H932 zip's boot.img had **no UUID args at all** because that zip was built from a freshly-created chroot; upstream fresh builds naturally emit label-based boot images. The global zip inherited stale UUIDs purely from pmbootstrap reusing the cached chroot across flavors. So the patch restores exactly what a clean upstream build would have produced. (The cached-chroot-reuse behavior itself is worth an upstream note someday — for now the patch covers us.)

## Phone-side current state (US998 bench)

- Device `LGUS9986e606d55`, LOS, currently in recovery, adb sideload-capable. Access: `ssh nym-nest-family` (adb may need `sudo` on that box — try plain first).
- SD card (`mmcblk0`, type=SD) already carries `pmOS_boot` (254 MB) + `pmOS_root` (196.6 GB) from the previous successful sideload, and **laf currently holds the stale-UUID boot.img** from that run.
- The new sideload re-partitions the card and re-flashes laf with the fixed boot.img — no manual cleanup needed. eMMC (sda*/sde*) and the LOS ROM are untouched.

## Literal next action (no code changes needed — this is a phone test)

1. Lance puts US998 in recovery → "Apply update from ADB".
2. From nym-nest-family:
   ```sh
   adb -s LGUS9986e606d55 sideload ~/joan-test-assets/lge-joan-prealpha-20260906-recovery-sd.zip
   ```
   (If the transport is flaky — this bench's USB re-enumerates — poll `adb devices` in a loop for `sideload` state, then fire the push; that watcher pattern worked earlier. The bench's USB quirks were ruled out as the cause of the earlier installer failures — that was the `set -e` bug, now patched.)
3. Lance boots laf: **power off → hold Volume Up → plug in USB** (no adb needed). Expected: pmOS boots to UI.
4. On success: optionally test the global internal zip next, then **t16 — present the full release (notes + assets + hashes) to Lance and get his explicit go before posting to GitHub Releases**. Do not publish unilaterally.
5. **H932 SD redo via LOS root shell is Lance-approved and queued** after the US998 test (replaces the card from the previous H932 test session).

## Pitfalls for the recipient

- **Do not rebuild from a reused chroot** without re-applying the label-based boot.img patch — cached chroots carry stale UUIDs. Fresh chroots are naturally fine. `/tmp/zipfix/` (patch script + extracted trees) is **ephemeral** — copy it somewhere durable (e.g. into the prealpha workspace) before it's needed again; do not rely on it surviving a reboot.
- `--android-recovery-zip` cannot combine with `--filesystem` (pmbootstrap v3 raises). Internal flavor needs `--split` (the combined image ships a broken PMBR/GPT). Leaked loop devices have killed `mkfs.ext4 /dev/installp2` — `scripts/pmb.sh shutdown` + `losetup -d` first. gzip inside the qemu-emulated chroot is single-core (~4.5 min); Lance accepted leaving it unshimmed for this release.
- Do not mislabel scope: this alpha does NOT have working cameras, mics, fingerprint, or sound output. Status authority = `ALPHA-STATUS.md`. Release notes must carry the works/doesn't-work split Lance specified.
- Every fork commit needs both trailers (hook enforces). Release zips: **no pushes without Lance's go.**

## Sources

- Release dir: `~/vibe-coding-projects/coding/lg-v30-pmos-prealpha/out/release/`
- Installer patch: `/tmp/zipfix/patch_installer.py` (+ trees z3..z6)
- pmbootstrap 3.11.1 source: `/usr/lib/python3.14/site-packages/pmb/`
- Installer upstream source: `/tmp/pmos-recovery-inst` (clone of postmarketos-android-recovery-installer)
