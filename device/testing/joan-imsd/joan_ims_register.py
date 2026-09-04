#!/usr/bin/env python3
"""Portable IMS SIP REGISTER builder for LG joan / T-Mobile.

Not a vendor blob. Protocol: stock Ims6/libims.lge.so strings +
3GPP TS 24.229 / TS 33.203 / RFC 3261 / RFC 3310 / RFC 3329.

Flow (aos_reg_0):
  1. unprotected REGISTER (empty AKA digest, Security-Client)
  2. 401 + Security-Server + nonce=base64(RAND||AUTN)
  3. ISIM AKA -> RES, CK, IK  (QMI UIM on device; --res-hex here)
  4. IPsec ESP transport from CK/IK (not in this file yet)
  5. second REGISTER with RFC 3310 Digest

Do not use the XML lab IMPI. Live identity is ISIM (msg.pc.t-mobile.com).

  python3 joan_ims_register.py --dry-run
  python3 joan_ims_register.py --dry-run --cseq 2 --nonce <b64> --res-hex <32 hex>
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
import uuid

DEFAULT_REALM = "msg.pc.t-mobile.com"
DEFAULT_PCSCF_PORT = 5060
PORT_INTERVAL = 1000


def branch() -> str:
    return "z9hG4bK" + secrets.token_hex(8)


def tag() -> str:
    return secrets.token_hex(6)


def spi() -> int:
    return secrets.randbelow(0x7FFFFFFF - 256) + 256


def pair_ports(base: int | None = None) -> tuple[int, int]:
    if base is None:
        base = 10000 + secrets.randbelow(20000)
    return base, base + PORT_INTERVAL


def security_client(spi_c: int, spi_s: int, port_c: int, port_s: int) -> str:
    """RFC 3329 / TS 33.203 Security-Client.

    TMO aos_reg_0_ipsec_algs=0x00010003; libims offers hmac-md5-96,
    hmac-sha-1-96, aes-cbc, 3des-cbc. Bitmask not fully decoded.
    """
    common = f"spi-c={spi_c}; spi-s={spi_s}; port-c={port_c}; port-s={port_s}"
    return (
        f"ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; prot=esp; mod=trans; {common}, "
        f"ipsec-3gpp; alg=hmac-md5-96; ealg=aes-cbc; prot=esp; mod=trans; {common}"
    )


def bracket_ip(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def aka_digest_response(
    *,
    username: str,
    realm: str,
    method: str,
    uri: str,
    nonce: str,
    res: bytes,
    algorithm: str = "AKAv1-MD5",
    qop: str | None = "auth",
    nc: str = "00000001",
    cnonce: str | None = None,
    ck: bytes | None = None,
    ik: bytes | None = None,
) -> tuple[str, str | None]:
    """RFC 3310 HTTP Digest AKA.

    AKAv1-MD5 password = RES
    AKAv2-MD5 password = RES || IK || CK
    """
    algo = algorithm.upper()
    if algo == "AKAV2-MD5":
        if ck is None or ik is None:
            raise ValueError("AKAv2-MD5 needs CK and IK")
        password = res + ik + ck
    else:
        password = res
    ha1 = md5_hex(username.encode() + b":" + realm.encode() + b":" + password)
    ha2 = md5_hex(f"{method}:{uri}".encode())
    if qop:
        cnonce = cnonce or secrets.token_hex(8)
        resp = md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode())
        return resp, cnonce
    return md5_hex(f"{ha1}:{nonce}:{ha2}".encode()), None


def parse_www_authenticate(header: str) -> dict[str, str]:
    """Parse WWW-Authenticate: Digest k=v, ..."""
    body = re.sub(r"^\s*Digest\s+", "", header.strip(), flags=re.I)
    out: dict[str, str] = {}
    for m in re.finditer(r'([a-zA-Z0-9_-]+)=("([^"]*)"|([^,]+))', body):
        key = m.group(1).lower()
        val = m.group(3) if m.group(3) is not None else m.group(4).strip()
        out[key] = val
    return out


def split_aka_nonce(nonce_b64: str) -> tuple[bytes, bytes]:
    """TS 24.229 / RFC 3310: nonce is base64(RAND[16] || AUTN[16])."""
    import base64

    raw = base64.b64decode(nonce_b64)
    if len(raw) < 32:
        raise ValueError(f"AKA nonce too short: {len(raw)} bytes")
    return raw[:16], raw[16:32]


def authorization_header(
    *,
    impi: str,
    realm: str,
    request_uri: str,
    nonce: str = "",
    response: str = "",
    algorithm: str = "AKAv1-MD5",
    qop: str | None = None,
    nc: str = "00000001",
    cnonce: str | None = None,
    integrity_protected: str | None = None,
) -> str:
    parts = [
        f'username="{impi}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{request_uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if qop:
        parts.append(f"qop={qop}")
        parts.append(f"nc={nc}")
        parts.append(f'cnonce="{cnonce or ""}"')
    if integrity_protected:
        parts.append(f"integrity-protected={integrity_protected}")
    return "Digest " + ", ".join(parts)


def build_register(
    *,
    impi: str,
    realm: str,
    local_ip: str,
    local_port: int,
    pcscf: str,
    pcscf_port: int,
    call_id: str | None = None,
    cseq: int = 1,
    from_tag: str | None = None,
    nonce: str = "",
    res: bytes | None = None,
    algorithm: str = "AKAv1-MD5",
    qop: str | None = None,
    ck: bytes | None = None,
    ik: bytes | None = None,
    spi_c: int | None = None,
    spi_s: int | None = None,
    port_c: int | None = None,
    port_s: int | None = None,
    impu: str | None = None,
    security_verify: str | None = None,
    expires: int = 600000,
    imei: str | None = None,
) -> bytes:
    if "@" not in impi:
        raise ValueError("IMPI must be user@realm")
    public = impu or impi
    if public.startswith("tel:"):
        aor = public
    elif public.startswith("sip:"):
        aor = public
    else:
        aor = f"sip:{public}"
    request_uri = f"sip:{realm}"
    via_host = bracket_ip(local_ip)
    contact_host = bracket_ip(local_ip)
    call_id = call_id or str(uuid.uuid4())
    from_tag = from_tag or tag()
    spi_c = spi() if spi_c is None else spi_c
    spi_s = spi() if spi_s is None else spi_s
    if port_c is None or port_s is None:
        port_c, port_s = pair_ports()
    sec = security_client(spi_c, spi_s, port_c, port_s)
    digits = "".join(c for c in (imei or "") if c.isdigit())
    if len(digits) >= 14:
        inst = f"{digits[:8]}-{digits[8:14]}-{digits[14] if len(digits) > 14 else '0'}"
    else:
        inst = "00000000-000000-0"
    contact_params = (
        f'+sip.instance="<urn:gsma:imei:{inst}>"'
        ';+g.3gpp.icsi-ref="urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"'
        ";+g.3gpp.smsip;audio"
    )
    cnonce = None
    response = ""
    if res is not None and nonce:
        qop = qop or "auth"
        response, cnonce = aka_digest_response(
            username=impi,
            realm=realm,
            method="REGISTER",
            uri=request_uri,
            nonce=nonce,
            res=res,
            algorithm=algorithm,
            qop=qop,
            ck=ck,
            ik=ik,
        )
    elif qop is None and not nonce:
        qop = None
    auth = authorization_header(
        impi=impi,
        realm=realm,
        request_uri=request_uri,
        nonce=nonce,
        response=response,
        algorithm=algorithm,
        qop=qop if res is not None else None,
        cnonce=cnonce,
        integrity_protected="yes" if res is not None else None,
    )
    contact_user = public.split("@")[0].removeprefix("sip:").removeprefix("tel:")
    lines = [
        f"REGISTER {request_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {via_host}:{local_port};branch={branch()};rport",
        "Max-Forwards: 70",
        f"From: <{aor}>;tag={from_tag}",
        f"To: <{aor}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <sip:{contact_user}@{contact_host}:{local_port}>;{contact_params}",
        f"Expires: {expires}",
        "Allow: INVITE, ACK, CANCEL, BYE, UPDATE, REFER, NOTIFY, MESSAGE, OPTIONS, PRACK",
        "Supported: path, sec-agree",
        "Require: sec-agree",
        "Proxy-Require: sec-agree",
        f"Security-Client: {sec}",
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD",
        f"P-Preferred-Identity: <{aor}>",
    ]
    if security_verify:
        lines.append(f"Security-Verify: {security_verify}")
    lines += [
        "Authorization: " + auth,
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def required_headers_ok(pkt: bytes, *, expect_aka_response: bool) -> list[str]:
    text = pkt.decode("utf-8")
    missing = []
    for h in (
        "REGISTER sip:",
        "Security-Client:",
        "Require: sec-agree",
        "Authorization:",
        "AKAv1-MD5",
        "Contact:",
        "Expires:",
    ):
        if h not in text:
            missing.append(h)
    if expect_aka_response:
        if "qop=auth" not in text or 'response=""' in text:
            missing.append("aka-response")
    return missing


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--impi", default=os.environ.get("JOAN_IMPI", f"user@{DEFAULT_REALM}"))
    p.add_argument("--realm", default=os.environ.get("JOAN_REALM", DEFAULT_REALM))
    p.add_argument("--local-ip", default="2001:db8::2")
    p.add_argument("--local-port", type=int, default=5060)
    p.add_argument("--pcscf", default="2001:db8::1")
    p.add_argument("--pcscf-port", type=int, default=DEFAULT_PCSCF_PORT)
    p.add_argument("--cseq", type=int, default=1)
    p.add_argument("--nonce", default="", help="401 nonce (base64 RAND||AUTN)")
    p.add_argument("--res-hex", default="", help="ISIM RES hex from QMI UIM AKA")
    p.add_argument("--ck-hex", default="")
    p.add_argument("--ik-hex", default="")
    p.add_argument("--algorithm", default="AKAv1-MD5")
    p.add_argument("--www-authenticate", default="", help="raw WWW-Authenticate value")
    return p.parse_args(argv)


def self_test() -> int:
    """Digest math only — synthetic RES, not a live Ki."""
    res = bytes.fromhex("00112233445566778899aabbccddeeff")
    username = "user@msg.pc.t-mobile.com"
    realm = "msg.pc.t-mobile.com"
    uri = "sip:msg.pc.t-mobile.com"
    nonce = "dGVzdG5vbmNlMTIzNA=="
    resp, cnonce = aka_digest_response(
        username=username,
        realm=realm,
        method="REGISTER",
        uri=uri,
        nonce=nonce,
        res=res,
        qop="auth",
        cnonce="cnonce01",
        nc="00000001",
    )
    password = res
    ha1 = md5_hex(username.encode() + b":" + realm.encode() + b":" + password)
    ha2 = md5_hex(b"REGISTER:sip:msg.pc.t-mobile.com")
    expect = md5_hex(f"{ha1}:{nonce}:00000001:cnonce01:auth:{ha2}".encode())
    if resp != expect:
        print("aka digest mismatch", resp, expect, file=sys.stderr)
        return 1
    if cnonce != "cnonce01":
        print("cnonce mismatch", file=sys.stderr)
        return 1
    parsed = parse_www_authenticate(
        'Digest realm="msg.pc.t-mobile.com", nonce="abc", algorithm=AKAv1-MD5, qop="auth"'
    )
    if parsed.get("algorithm") != "AKAv1-MD5" or parsed.get("qop") != "auth":
        print("www-authenticate parse fail", parsed, file=sys.stderr)
        return 1
    pkt1 = build_register(
        impi=username,
        realm=realm,
        local_ip="2001:db8::2",
        local_port=5060,
        pcscf="2001:db8::1",
        pcscf_port=5060,
    )
    miss = required_headers_ok(pkt1, expect_aka_response=False)
    if miss:
        print("first REGISTER missing", miss, file=sys.stderr)
        return 1
    pkt2 = build_register(
        impi=username,
        realm=realm,
        local_ip="2001:db8::2",
        local_port=5060,
        pcscf="2001:db8::1",
        pcscf_port=5060,
        cseq=2,
        nonce=nonce,
        res=res,
    )
    miss = required_headers_ok(pkt2, expect_aka_response=True)
    if miss:
        print("second REGISTER missing", miss, file=sys.stderr)
        return 1
    print("SELF_TEST_OK", resp)
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.dry_run and not args.send:
        print("pass --dry-run, --send, or --self-test", file=sys.stderr)
        return 2
    nonce = args.nonce
    algorithm = args.algorithm
    if args.www_authenticate:
        parsed = parse_www_authenticate(args.www_authenticate)
        nonce = nonce or parsed.get("nonce", "")
        algorithm = parsed.get("algorithm", algorithm)
    res = bytes.fromhex(args.res_hex) if args.res_hex else None
    ck = bytes.fromhex(args.ck_hex) if args.ck_hex else None
    ik = bytes.fromhex(args.ik_hex) if args.ik_hex else None
    pkt = build_register(
        impi=args.impi,
        realm=args.realm,
        local_ip=args.local_ip,
        local_port=args.local_port,
        pcscf=args.pcscf,
        pcscf_port=args.pcscf_port,
        cseq=args.cseq,
        nonce=nonce,
        res=res,
        algorithm=algorithm,
        ck=ck,
        ik=ik,
    )
    miss = required_headers_ok(pkt, expect_aka_response=res is not None)
    if miss:
        print("missing headers:", miss, file=sys.stderr)
        return 1
    sys.stdout.buffer.write(pkt)
    if not args.send:
        return 0
    import socket

    family = socket.AF_INET6 if ":" in args.pcscf else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(5)
    sock.sendto(pkt, (args.pcscf, args.pcscf_port))
    try:
        data, src = sock.recvfrom(65535)
    except TimeoutError:
        print("\n# no UDP reply in 5s", file=sys.stderr)
        return 3
    sys.stderr.buffer.write(b"\n# reply from " + str(src).encode() + b"\n")
    sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
