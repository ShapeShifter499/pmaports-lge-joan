#!/bin/sh
# SPDX-License-Identifier: MIT
# Fast checks that do not need pmbootstrap: the leftover owner-tarball / dual-zap
# layout must not come back.

set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
selector=$repo_dir/joan-firmware-variant
apkbuild=$repo_dir/APKBUILD
files=$repo_dir/30-lge-joan-gpu.files

fail() { echo "FAIL: $*" >&2; exit 1; }

sh -n "$selector"
sh -n "$repo_dir/firmware-lge-joan-h930.post-install"
sh -n "$repo_dir/firmware-lge-joan-h932.post-install"

grep -q 'owner-firmware-lge-joan' "$apkbuild" && \
	fail "APKBUILD still references owner-firmware-lge-joan"
grep -q 'firmware-lge-joan-blobs' "$apkbuild" || \
	fail "APKBUILD does not fetch firmware-lge-joan-blobs"
grep -q 'pkgname-h930:h930' "$apkbuild" || fail "missing h930 subpackage"
grep -q 'pkgname-h932:h932' "$apkbuild" || fail "missing h932 subpackage"

for stale in \
	/usr/lib/firmware/qcom/lge/joan/H930/a540_zap.mdt \
	/usr/lib/firmware/qcom/lge/joan/H932/a540_zap.mdt
do
	grep -Fqx "$stale" "$files" && fail "initramfs list still has dual-zap path $stale"
done

for path in \
	/usr/lib/firmware/qcom/a530_pfp.fw \
	/usr/lib/firmware/qcom/a530_pm4.fw \
	/usr/lib/firmware/qcom/a540_gpmu.fw2 \
	/usr/lib/firmware/qca/crbtfw21.tlv \
	/usr/lib/firmware/qca/crnv21.bin \
	/usr/lib/firmware/qcom/a540_zap.mdt \
	/usr/lib/firmware/qcom/a540_zap.b00 \
	/usr/lib/firmware/qcom/a540_zap.b01 \
	/usr/lib/firmware/qcom/a540_zap.b02
do
	grep -Fqx "$path" "$files" || fail "initramfs list missing $path"
done

got=$("$selector" --fw-name 'qcom/lge/joan/H932/a540_zap.mdt')
[ "$got" = h932 ] || fail "H932 DT path: got $got"
got=$("$selector" --fw-name 'qcom/lge/joan/H930/a540_zap.mdt')
[ "$got" = h930 ] || fail "H930 DT path: got $got"

echo PASS firmware-split layout
