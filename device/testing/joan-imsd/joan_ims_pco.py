#!/usr/bin/env python3
"""Decode 3GPP PCO / QMI WDS P-CSCF lists (TS 24.008 §10.5.6.3).

Container IDs:
  0x0001  P-CSCF IPv6 (16 bytes)
  0x000C  P-CSCF IPv4 (4 bytes)

  python3 joan_ims_pco.py --self-test
  python3 joan_ims_pco.py --hex 000110...
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from dataclasses import dataclass


PCO_PCSCF_V6 = 0x0001
PCO_PCSCF_V4 = 0x000C


@dataclass(frozen=True)
class Pcscf:
    version: int
    addr: str


def decode_pco(raw: bytes) -> list[Pcscf]:
    """Decode PCO contents *after* the 1-byte ext/config id if present.

    Accepts either:
      - raw container stream (id:2 len:1 data)
      - 3GPP PCO starting with 0x80 (ext bit + config protocol = PPP)
    """
    if raw and raw[0] == 0x80:
        raw = raw[1:]
    out: list[Pcscf] = []
    i = 0
    while i + 3 <= len(raw):
        cid = (raw[i] << 8) | raw[i + 1]
        ln = raw[i + 2]
        i += 3
        if i + ln > len(raw):
            raise ValueError("truncated PCO container")
        data = raw[i : i + ln]
        i += ln
        if cid == PCO_PCSCF_V6:
            if ln != 16:
                raise ValueError(f"P-CSCF v6 length {ln}")
            out.append(Pcscf(6, str(ipaddress.IPv6Address(data))))
        elif cid == PCO_PCSCF_V4:
            if ln != 4:
                raise ValueError(f"P-CSCF v4 length {ln}")
            out.append(Pcscf(4, str(ipaddress.IPv4Address(data))))
    return out


def self_test() -> int:
    v6 = ipaddress.IPv6Address("2001:db8::1").packed
    v4 = ipaddress.IPv4Address("66.94.3.103").packed
    blob = bytes([0x80, 0x00, 0x01, 16]) + v6 + bytes([0x00, 0x0C, 4]) + v4
    got = decode_pco(blob)
    if [p.addr for p in got] != ["2001:db8::1", "66.94.3.103"]:
        print("pco decode fail", got, file=sys.stderr)
        return 1
    print("PCO_SELF_TEST_OK", got[0].addr, got[1].addr)
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--hex", default="")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.hex:
        print("pass --self-test or --hex", file=sys.stderr)
        return 2
    raw = bytes.fromhex(args.hex.replace(" ", ""))
    for pc in decode_pco(raw):
        print(f"pcscf v{pc.version} {pc.addr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
