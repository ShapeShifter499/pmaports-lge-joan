#!/usr/bin/env python3
"""Portable 3GPP IMS IPsec (ESP transport) helper for joan.

Not a vendor blob. Layout from libims.lge.so logs:
  IPSEC-SA-INFO(spi|s-ip|d-ip|sec-proto|algo-auth|algo-enc|ik|ck)
  IPSEC-SP-INFO(spi|s-ip|d-ip|s-port|d-port|dir|proto|mode|action)
and TS 33.203 / RFC 3329: CK=enc, IK=auth, ESP transport, 4 SAs.

Prints `ip xfrm` commands. Does not apply them unless --apply (off).

  python3 joan_ims_ipsec.py --self-test
  python3 joan_ims_ipsec.py --dry-run \\
      --ue 2607:fc20::2 --pcscf 2001:db8::1 \\
      --ck-hex ... --ik-hex ... \\
      --security-server 'ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; ...'
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

# TS 33.203: IK/CK from AKA are 128-bit. hmac-sha1 wants 160-bit keys;
# pad IK with zeros (common IMS UE practice).
SHA1_PAD = b"\x00" * 4


@dataclass(frozen=True)
class SecAgree:
    alg: str
    ealg: str
    spi_c: int
    spi_s: int
    port_c: int
    port_s: int


def parse_sec_agree(header: str) -> SecAgree:
    """Parse one ipsec-3gpp Security-Client/Server mechanism."""
    body = header.strip()
    body = re.sub(r"^\s*Security-(Client|Server):\s*", "", body, flags=re.I)
    # take first ipsec-3gpp mechanism if a list
    first = body.split(",")[0]
    kv: dict[str, str] = {}
    for m in re.finditer(r"([a-zA-Z0-9_-]+)=([^;]+)", first):
        kv[m.group(1).lower()] = m.group(2).strip()
    need = ("spi-c", "spi-s", "port-c", "port-s")
    missing = [k for k in need if k not in kv]
    if missing:
        raise ValueError(f"sec-agree missing {missing} in {first!r}")
    alg = kv.get("alg", "hmac-sha-1-96")
    ealg = kv.get("ealg", "aes-cbc")
    return SecAgree(
        alg=alg,
        ealg=ealg,
        spi_c=int(kv["spi-c"], 0),
        spi_s=int(kv["spi-s"], 0),
        port_c=int(kv["port-c"]),
        port_s=int(kv["port-s"]),
    )


def bracket(ip: str) -> str:
    return ip  # xfrm wants unbracketed IPv6


def xfrm_auth(alg: str, ik: bytes) -> tuple[str, bytes]:
    a = alg.lower()
    if a in ("hmac-sha-1-96", "hmac-sha1-96", "sha1"):
        key = ik if len(ik) >= 20 else ik + SHA1_PAD[: 20 - len(ik)]
        return "sha1", key
    if a in ("hmac-md5-96", "hmac-md5", "md5"):
        return "md5", ik[:16]
    raise ValueError(f"unsupported auth alg {alg}")


def xfrm_enc(ealg: str, ck: bytes) -> tuple[str, bytes]:
    e = ealg.lower()
    if e in ("aes-cbc", "aes"):
        return "aes", ck[:16]
    if e in ("3des-cbc", "3des"):
        key = ck if len(ck) >= 24 else (ck * 2)[:24]
        return "des3_ede", key
    raise ValueError(f"unsupported enc alg {ealg}")


@dataclass(frozen=True)
class Sa:
    src: str
    dst: str
    spi: int
    auth: str
    auth_key: bytes
    enc: str
    enc_key: bytes
    sport: int
    dport: int
    direction: str  # in|out (policy dir)


def four_sas(
    *,
    ue: str,
    pcscf: str,
    ue_sec: SecAgree,
    pcscf_sec: SecAgree,
    ck: bytes,
    ik: bytes,
) -> list[Sa]:
    """RFC 3329 two-port model.

    UE port-c / spi-c  <->  P-CSCF port-s / spi-s
    UE port-s / spi-s  <->  P-CSCF port-c / spi-c
    """
    auth, akey = xfrm_auth(pcscf_sec.alg, ik)
    enc, ekey = xfrm_enc(pcscf_sec.ealg, ck)
    return [
        Sa(ue, pcscf, pcscf_sec.spi_s, auth, akey, enc, ekey, ue_sec.port_c, pcscf_sec.port_s, "out"),
        Sa(pcscf, ue, ue_sec.spi_c, auth, akey, enc, ekey, pcscf_sec.port_s, ue_sec.port_c, "in"),
        Sa(ue, pcscf, pcscf_sec.spi_c, auth, akey, enc, ekey, ue_sec.port_s, pcscf_sec.port_c, "out"),
        Sa(pcscf, ue, ue_sec.spi_s, auth, akey, enc, ekey, pcscf_sec.port_c, ue_sec.port_s, "in"),
    ]


def xfrm_commands(sas: list[Sa]) -> list[str]:
    cmds: list[str] = []
    for sa in sas:
        cmds.append(
            "ip xfrm state add "
            f"src {bracket(sa.src)} dst {bracket(sa.dst)} "
            f"proto esp spi {sa.spi} mode transport "
            f"auth {sa.auth} {sa.auth_key.hex()} "
            f"enc {sa.enc} {sa.enc_key.hex()}"
        )
        cmds.append(
            "ip xfrm policy add "
            f"src {bracket(sa.src)} dst {bracket(sa.dst)} "
            f"proto udp sport {sa.sport} dport {sa.dport} "
            f"dir {sa.direction} tmpl proto esp mode transport"
        )
    return cmds


def self_test() -> int:
    ue = "2001:db8::2"
    pcscf = "2001:db8::1"
    ue_sec = parse_sec_agree(
        "ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=100; spi-s=200; port-c=15000; port-s=16000"
    )
    pcscf_sec = parse_sec_agree(
        "ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=300; spi-s=400; port-c=25000; port-s=26000"
    )
    ck = bytes.fromhex("00112233445566778899aabbccddeeff")
    ik = bytes.fromhex("ffeeddccbbaa99887766554433221100")
    sas = four_sas(ue=ue, pcscf=pcscf, ue_sec=ue_sec, pcscf_sec=pcscf_sec, ck=ck, ik=ik)
    if len(sas) != 4:
        print("expected 4 SAs", file=sys.stderr)
        return 1
    if sas[0].spi != 400 or sas[1].spi != 100:
        print("spi pairing wrong", sas, file=sys.stderr)
        return 1
    if len(sas[0].auth_key) != 20 or len(sas[0].enc_key) != 16:
        print("key lengths", len(sas[0].auth_key), len(sas[0].enc_key), file=sys.stderr)
        return 1
    cmds = xfrm_commands(sas)
    if len(cmds) != 8:
        print("expected 8 xfrm cmds", file=sys.stderr)
        return 1
    joined = "\n".join(cmds)
    for needle in ("proto esp", "mode transport", "auth sha1", "enc aes", "dir out", "dir in"):
        if needle not in joined:
            print("missing", needle, file=sys.stderr)
            return 1
    print("IPSEC_SELF_TEST_OK", len(cmds))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true", help="run ip xfrm (default: print only)")
    p.add_argument("--ue", default="2001:db8::2")
    p.add_argument("--pcscf", default="2001:db8::1")
    p.add_argument("--ck-hex", default="")
    p.add_argument("--ik-hex", default="")
    p.add_argument(
        "--security-client",
        default="ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=100; spi-s=200; port-c=15000; port-s=16000",
    )
    p.add_argument(
        "--security-server",
        default="ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=300; spi-s=400; port-c=25000; port-s=26000",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.dry_run and not args.apply:
        print("pass --dry-run, --apply, or --self-test", file=sys.stderr)
        return 2
    if not args.ck_hex or not args.ik_hex:
        print("--ck-hex and --ik-hex required", file=sys.stderr)
        return 2
    sas = four_sas(
        ue=args.ue,
        pcscf=args.pcscf,
        ue_sec=parse_sec_agree(args.security_client),
        pcscf_sec=parse_sec_agree(args.security_server),
        ck=bytes.fromhex(args.ck_hex),
        ik=bytes.fromhex(args.ik_hex),
    )
    cmds = xfrm_commands(sas)
    for c in cmds:
        print(c)
    if args.apply:
        print("# --apply refused in this helper; run the printed ip xfrm on pmOS", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
