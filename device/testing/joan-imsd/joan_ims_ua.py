#!/usr/bin/env python3
"""Offline IMS UA sequencer for joan (no vendor blobs).

Wires:
  joan_ims_register.py  first/second REGISTER
  joan_ims_ipsec.py     4 ESP transport SAs
  joan_ims_pco.py       P-CSCF from PCO

Does not send packets. --self-test feeds a synthetic 401.

  python3 joan_ims_ua.py --self-test
"""

from __future__ import annotations

import base64
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from joan_ims_ipsec import four_sas, parse_sec_agree, xfrm_commands
from joan_ims_pco import decode_pco
from joan_ims_register import (
    build_register,
    parse_www_authenticate,
    required_headers_ok,
    split_aka_nonce,
)

IMPI = "user@msg.pc.t-mobile.com"
REALM = "msg.pc.t-mobile.com"
UE = "2001:db8::2"
PCSCF = "2001:db8::1"


def parse_401(msg: str) -> tuple[dict[str, str], str]:
    www = ""
    sec = ""
    for line in msg.splitlines():
        if line.lower().startswith("www-authenticate:"):
            www = line.split(":", 1)[1].strip()
        if line.lower().startswith("security-server:"):
            sec = line.split(":", 1)[1].strip()
    if not www:
        raise ValueError("401 missing WWW-Authenticate")
    if not sec:
        raise ValueError("401 missing Security-Server")
    return parse_www_authenticate(www), sec


def handle_401(
    *,
    first_pkt: bytes,
    msg_401: str,
    res: bytes,
    ck: bytes,
    ik: bytes,
    ue_sec_header: str,
) -> dict:
    auth, sec_server = parse_401(msg_401)
    nonce = auth["nonce"]
    rand, autn = split_aka_nonce(nonce)
    ue_sec = parse_sec_agree(ue_sec_header)
    pcscf_sec = parse_sec_agree(sec_server)
    sas = four_sas(ue=UE, pcscf=PCSCF, ue_sec=ue_sec, pcscf_sec=pcscf_sec, ck=ck, ik=ik)
    cmds = xfrm_commands(sas)
    call_id = re.search(r"Call-ID: (.+)", first_pkt.decode()).group(1).strip()
    from_tag = re.search(r"From:.*tag=([^\s;>]+)", first_pkt.decode()).group(1)
    second = build_register(
        impi=IMPI,
        realm=REALM,
        local_ip=UE,
        local_port=5060,
        pcscf=PCSCF,
        pcscf_port=5060,
        call_id=call_id,
        from_tag=from_tag,
        cseq=2,
        nonce=nonce,
        res=res,
        ck=ck,
        ik=ik,
        spi_c=ue_sec.spi_c,
        spi_s=ue_sec.spi_s,
        port_c=ue_sec.port_c,
        port_s=ue_sec.port_s,
    )
    return {
        "rand": rand,
        "autn": autn,
        "xfrm": cmds,
        "second": second,
        "algorithm": auth.get("algorithm", "AKAv1-MD5"),
    }


def self_test() -> int:
    first = build_register(
        impi=IMPI,
        realm=REALM,
        local_ip=UE,
        local_port=5060,
        pcscf=PCSCF,
        pcscf_port=5060,
    )
    miss = required_headers_ok(first, expect_aka_response=False)
    if miss:
        print("first REGISTER missing", miss, file=sys.stderr)
        return 1
    m = re.search(r"Security-Client: (.+)", first.decode())
    ue_sec_header = m.group(1).strip()
    rand = bytes(range(16))
    autn = bytes(range(16, 32))
    nonce = base64.b64encode(rand + autn).decode()
    msg_401 = (
        "SIP/2.0 401 Unauthorized\r\n"
        f'WWW-Authenticate: Digest realm="{REALM}", nonce="{nonce}", '
        'algorithm=AKAv1-MD5, qop="auth"\r\n'
        "Security-Server: ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; "
        "spi-c=300; spi-s=400; port-c=25000; port-s=26000\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    res = bytes.fromhex("00112233445566778899aabbccddeeff")
    ck = bytes.fromhex("00112233445566778899aabbccddeeff")
    ik = bytes.fromhex("ffeeddccbbaa99887766554433221100")
    out = handle_401(
        first_pkt=first,
        msg_401=msg_401,
        res=res,
        ck=ck,
        ik=ik,
        ue_sec_header=ue_sec_header,
    )
    if out["rand"] != rand or out["autn"] != autn:
        print("RAND/AUTN split fail", file=sys.stderr)
        return 1
    if len(out["xfrm"]) != 8:
        print("xfrm count", len(out["xfrm"]), file=sys.stderr)
        return 1
    miss = required_headers_ok(out["second"], expect_aka_response=True)
    if miss:
        print("second REGISTER missing", miss, file=sys.stderr)
        return 1
    pco = decode_pco(
        bytes([0x80, 0x00, 0x01, 16]) + bytes.fromhex("20010db8000000000000000000000001")
    )
    if pco[0].addr != "2001:db8::1":
        print("pco fail", pco, file=sys.stderr)
        return 1
    print("UA_SELF_TEST_OK", len(out["xfrm"]), pco[0].addr)
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" not in argv:
        print("pass --self-test", file=sys.stderr)
        return 2
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
