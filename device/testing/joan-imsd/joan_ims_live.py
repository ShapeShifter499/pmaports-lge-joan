#!/usr/bin/env python3
"""REGISTER 200 then INVITE +19163599872 on the same userspace ESP SA.

Does not print IMPI/nonce/RES/CK/IK. Prints SIP status lines only.
"""
from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import joan_ims_esp as esp
import joan_ims_ipsec as ipsec
import joan_ims_register as reg

ISIM_AID = "A0000000871004FFFFFFFF8907030000"
ISIM_ENV_CANDIDATES = (
    "/etc/joan-imsd/isim.env",
    "/var/lib/joan-imsd/isim.env",
    "/tmp/isim-live.env",
)
DEST = os.environ.get("JOAN_IMS_DEST", "")


def sudo(args, **kw):
    if os.geteuid() == 0:
        return subprocess.run(args, capture_output=True, text=True, **kw)
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = os.environ.get("SUDO_ASKPASS", "/tmp/ap.sh")
    return subprocess.run(["sudo", "-A", *args], env=env, capture_output=True, text=True, **kw)


def tlv(t, val):
    return struct.pack("<BH", t, len(val)) + val


def qmi_req(msgid, body, txn):
    return struct.pack("<BHHH", 0, txn, msgid, len(body)) + body


def qmi_parse(data):
    typ, txn, msgid, ln = struct.unpack_from("<BHHH", data)
    body = data[7 : 7 + ln]
    tlvs = {}
    i = 0
    while i + 3 <= len(body):
        t, l = struct.unpack_from("<BH", body, i)
        i += 3
        tlvs[t] = body[i : i + l]
        i += l
    return msgid, tlvs


def qmi_res(tlvs):
    v = tlvs.get(2, b"")
    return struct.unpack_from("<HH", v) if len(v) >= 4 else None


def ipv6_list(blob):
    out = []
    if blob and blob[0] * 16 + 1 == len(blob):
        n, b = blob[0], blob[1:]
        for i in range(n):
            out.append(socket.inet_ntop(socket.AF_INET6, b[i * 16 : (i + 1) * 16]))
    return out


def wds_pcscf():
    s = socket.socket(42, socket.SOCK_DGRAM)
    s.settimeout(5)

    def recv_want(want):
        for _ in range(8):
            data = s.recvfrom(4096)[0]
            msgid, tlvs = qmi_parse(data)
            if msgid == want:
                return tlvs
        return None

    s.sendto(
        qmi_req(0x00A2, tlv(0x10, struct.pack("<II", 4, 1)) + tlv(0x11, struct.pack("<B", 2)), 1),
        (0, 57),
    )
    recv_want(0x00A2)
    s.sendto(qmi_req(0x002D, b"", 2), (0, 57))
    gc = recv_want(0x002D)
    if not gc or qmi_res(gc) != (0, 0):
        return [], None
    pcs = ipv6_list(gc.get(0x2E, b""))
    gw = None
    if len(gc.get(0x26, b"")) >= 16:
        gw = socket.inet_ntop(socket.AF_INET6, gc[0x26][:16])
    return pcs, gw


def sip_headers(text: str) -> dict[str, list[str]]:
    hdrs: dict[str, list[str]] = {}
    for line in text.splitlines()[1:]:
        if not line.strip():
            break
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        hdrs.setdefault(k.strip().lower(), []).append(v.strip())
    return hdrs


def hdr1(hdrs, k, default=""):
    return (hdrs.get(k) or [default])[0]


def qmicli(*args, timeout=12):
    return subprocess.run(
        ["qmicli", "-d", "qrtr://0", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def open_isim() -> int:
    r = qmicli(f"--uim-open-logical-channel=1,{ISIM_AID}")
    m = re.search(r"(\d+)\s*$", (r.stdout or "").strip())
    if r.returncode == 0 and m:
        return int(m.group(1))
    return 2


def get_imei() -> str:
    try:
        out = subprocess.check_output(["mmcli", "-m", "0"], text=True, timeout=8)
    except Exception:
        return ""
    for line in out.splitlines():
        if "imei:" in line.lower():
            return line.split(":", 1)[1].strip()
    return ""


def vss_set_ims_registered() -> str:
    s = socket.socket(42, socket.SOCK_DGRAM)
    s.settimeout(3)
    body = b""
    for i, val in enumerate([2, 1, 0, 0, 0, 0], start=1):
        body += tlv(i, struct.pack("<I", val))
    s.sendto(qmi_req(0x0707, body, 1), (0, 73))
    try:
        data = s.recvfrom(4096)[0]
        _, tlvs = qmi_parse(data)
        return f"vss707={qmi_res(tlvs)}"
    except Exception as e:
        return f"vss707_err={type(e).__name__}"


def decode_uicc_str(resp: bytes) -> str:
    data = resp[:-2] if len(resp) >= 2 and resp[-2] == 0x90 else resp
    if data[:1] == b"\x80" and len(data) > 2:
        n = data[1]
        data = data[2 : 2 + n]
    return data.decode("ascii", "replace").strip("\x00").strip()


def uicc_select(ch: int, fid_hex: str) -> bytes:
    apdu = bytes.fromhex("00A4000402") + bytes.fromhex(fid_hex)
    r = send_apdu(ch, apdu)
    if len(r) >= 2 and r[-2] == 0x61:
        r = send_apdu(ch, bytes.fromhex("00C00000") + bytes([r[-1]]))
    return r


def read_isim_card() -> dict[str, str]:
    """EF-IMPI/IMPU/DOMAIN from ISIM. No vendor blobs. Do not print values."""
    ch = open_isim()
    out: dict[str, str] = {}
    try:
        uicc_select(ch, "6F02")
        r = send_apdu(ch, bytes.fromhex("00B00000FF"))
        impi = decode_uicc_str(r)
        if "@" in impi:
            out["JOAN_IMPI"] = impi
            out["JOAN_REALM"] = impi.split("@", 1)[1]
        uicc_select(ch, "6F03")
        r = send_apdu(ch, bytes.fromhex("00B00000FF"))
        dom = decode_uicc_str(r)
        if "." in dom:
            out["JOAN_REALM"] = dom
        uicc_select(ch, "6F04")
        r = send_apdu(ch, bytes.fromhex("00B20104FF"))
        impu = decode_uicc_str(r)
        if "@" in impu or impu.startswith("sip:") or impu.startswith("tel:"):
            out["JOAN_IMPU"] = impu if ":" in impu else f"sip:{impu}"
    finally:
        qmicli(f"--uim-close-logical-channel=1,{ch}")
    return out


def get_msisdn() -> str:
    try:
        out = subprocess.check_output(["mmcli", "-m", "0"], text=True, timeout=8)
    except Exception:
        return ""
    for line in out.splitlines():
        if "own:" in line.lower():
            return "".join(c for c in line.split(":", 1)[1] if c.isdigit() or c == "+")
    return ""


def send_apdu(channel: int, apdu: bytes) -> bytes:
    hx = apdu.hex()
    r = qmicli(f"--uim-send-apdu=1,{channel},{hx}", timeout=15)
    blob = r.stdout or ""
    m = re.search(r"successfully completed:\s*([0-9A-Fa-f:]+)", blob)
    if not m:
        raise RuntimeError(f"APDU parse rc={r.returncode}")
    cleaned = m.group(1).replace(":", "")
    return bytes.fromhex(cleaned)


def parse_aka_success(resp: bytes) -> tuple[bytes, bytes, bytes]:
    if len(resp) < 2:
        raise RuntimeError("AKA short")
    sw = resp[-2:]
    data = resp[:-2] if sw in (b"\x90\x00", b"\x91\x00") else resp
    if data[:1] == b"\xdc":
        raise RuntimeError("AKA sync failure (AUTS)")
    if data[:1] != b"\xdb":
        raise RuntimeError(f"AKA tag {data[:1].hex()} sw={sw.hex()}")
    i = 1
    nres = data[i]; i += 1
    res = data[i : i + nres]; i += nres
    nck = data[i]; i += 1
    ck = data[i : i + nck]; i += nck
    nik = data[i]; i += 1
    ik = data[i : i + nik]
    if not (4 <= len(res) <= 16 and len(ck) == 16 and len(ik) == 16):
        raise RuntimeError(f"AKA lens res={len(res)} ck={len(ck)} ik={len(ik)}")
    return res, ck, ik


def unprotect(data: bytes, ck, ik) -> bytes:
    cands = [data]
    if len(data) > 40 and (data[0] >> 4) == 6:
        nh = data[6]
        off = 40
        while nh in (0, 43, 44, 51, 60, 135) and off + 8 <= len(data):
            nxt = data[off]
            if nh == 44:
                off += 8
                nh = nxt
                continue
            hlen = data[off + 1]
            off += (hlen + 1) * 8
            nh = nxt
        if nh == 50 and off < len(data):
            cands.append(data[off:])
        cands.append(data[40:])
    for cand in cands:
        if len(cand) < 40:
            continue
        try:
            return esp.esp_unprotect(cand, ck, ik)
        except ValueError:
            continue
    spi = None
    if len(data) >= 4:
        spi = int.from_bytes(data[:4], "big")
        if (data[0] >> 4) == 6 and len(data) >= 44:
            spi = int.from_bytes(data[40:44], "big")
    print("ESP_SKIP", len(data), "spi", spi)
    raise ValueError("esp")


class EspSa:
    def __init__(self, src, target, src_b, dst_b, sport, dport, spi, ck, ik):
        self.src = src
        self.target = target
        self.src_b = src_b
        self.dst_b = dst_b
        self.sport = sport
        self.dport = dport
        self.spi = spi
        self.ck = ck
        self.ik = ik
        self.seq = 0
        self.raw = socket.socket(socket.AF_INET6, socket.SOCK_RAW, 50)
        self.raw.settimeout(8)
        self.raw.bind((src, 0))

    def send(self, sip: bytes) -> int:
        self.seq += 1
        udp = esp.build_udp(self.src_b, self.dst_b, self.sport, self.dport, sip)
        pkt = esp.esp_protect(spi=self.spi, seq=self.seq, ck=self.ck, ik=self.ik, inner=udp)
        return self.raw.sendto(pkt, (self.target, 0))

    def recv(self, timeout=8) -> tuple[str, dict]:
        self.raw.settimeout(timeout)
        deadline = time.time() + timeout
        last_err = "timeout"
        while time.time() < deadline:
            remain = max(0.2, deadline - time.time())
            self.raw.settimeout(remain)
            try:
                data, addr = self.raw.recvfrom(4096)
            except socket.timeout:
                break
            try:
                inner = unprotect(data, self.ck, self.ik)
            except ValueError:
                last_err = "icv"
                print("ESP_SKIP", len(data))
                continue
            sip = inner[8:]
            text = sip.decode("utf-8", "replace")
            return text, sip_headers(text)
        raise socket.timeout


def sip_response(req: str, code: int, reason: str, *, to_tag: str, src: str, port_c: int, body: str = "", extra: list[str] | None = None) -> bytes:
    h = sip_headers(req)
    lines = [f"SIP/2.0 {code} {reason}"]
    for v in h.get("via", []):
        lines.append(f"Via: {v}")
    for r in h.get("record-route", []):
        lines.append(f"Record-Route: {r}")
    lines.append(f"From: {hdr1(h, 'from')}")
    to = hdr1(h, "to")
    if "tag=" not in to.lower():
        to = f"{to};tag={to_tag}"
    lines.append(f"To: {to}")
    lines.append(f"Call-ID: {hdr1(h, 'call-id')}")
    lines.append(f"CSeq: {hdr1(h, 'cseq')}")
    lines.append(f"Contact: <sip:ue@[{src}]:{port_c}>")
    if extra:
        lines.extend(extra)
    blen = len(body.encode()) if body else 0
    lines.append(f"Content-Length: {blen}")
    if body:
        return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def listen_incoming(sa: EspSa, *, src: str, port_c: int, iface: str = "qmapmux0.1") -> int:
    our_tag = uuid.uuid4().hex[:10]
    udp_socks = []
    for p in (5060, port_c, 16000):
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((src, p))
            s.settimeout(0.3)
            udp_socks.append(s)
            print("UDP_BIND", p)
        except OSError as e:
            print("UDP_BIND_FAIL", p, type(e).__name__)
    print("LISTENING")
    rx0 = 0
    try:
        rx0 = int(open(f"/sys/class/net/{iface}/statistics/rx_packets").read())
    except Exception:
        pass
    while True:
        got = None
        try:
            t, h = sa.recv(5)
            got = ("esp", t, h)
        except socket.timeout:
            pass
        except ValueError:
            print("LISTEN_ESP_SKIP")
        if got is None:
            for s in udp_socks:
                try:
                    data, addr = s.recvfrom(4096)
                    text = data.decode("utf-8", "replace")
                    print("UDP_RX", addr[1] if addr else "?", text.splitlines()[0][:80] if text else "empty")
                    got = ("udp", text, sip_headers(text))
                    break
                except socket.timeout:
                    continue
        if got is None:
            try:
                rx = int(open(f"/sys/class/net/{iface}/statistics/rx_packets").read())
            except Exception:
                rx = rx0
            if rx != rx0:
                print("IFACE_RX_DELTA", rx - rx0)
                rx0 = rx
            else:
                print("LISTEN_IDLE")
            continue
        _, t, h = got
        line0 = t.splitlines()[0] if t else ""
        print("RX", line0[:90], "LEN", len(t))
        if line0.startswith("INVITE "):
            sa.send(sip_response(t, 100, "Trying", to_tag=our_tag, src=src, port_c=port_c))
            sa.send(sip_response(t, 180, "Ringing", to_tag=our_tag, src=src, port_c=port_c))
            print("INCOMING_RING")
            sdp = (
                "v=0\r\n"
                f"o=- {int(time.time())} 1 IN IP6 {src}\r\n"
                "s=-\r\n"
                f"c=IN IP6 {src}\r\n"
                "t=0 0\r\n"
                "m=audio 40000 RTP/AVP 0 101\r\n"
                "a=rtpmap:0 PCMU/8000\r\n"
                "a=rtpmap:101 telephone-event/8000\r\n"
                "a=sendrecv\r\n"
            )
            extra = [
                "Content-Type: application/sdp",
                "Allow: INVITE, ACK, CANCEL, BYE, PRACK",
            ]
            sa.send(sip_response(t, 200, "OK", to_tag=our_tag, src=src, port_c=port_c, body=sdp, extra=extra))
            print("INCOMING_200")
            sdp_info = parse_sdp(t)
            stop = threading.Event()
            rtp_thr = None
            if sdp_info.get("ip") and sdp_info.get("port") and os.path.isfile("/tmp/aurel-vm.ulaw"):
                rtp_thr = threading.Thread(
                    target=send_pcmu,
                    args=(src, sdp_info["ip"], sdp_info["port"], stop),
                    daemon=True,
                )
                rtp_thr.start()
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    t2, _ = sa.recv(max(1, deadline - time.time()))
                except (socket.timeout, ValueError):
                    continue
                l2 = t2.splitlines()[0] if t2 else ""
                print("INCALL", l2[:90])
                if l2.startswith("BYE "):
                    sa.send(sip_response(t2, 200, "OK", to_tag=our_tag, src=src, port_c=port_c))
                    print("INCOMING_BYE")
                    break
                if l2.startswith("ACK "):
                    print("INCOMING_ACK")
            stop.set()
            if rtp_thr:
                rtp_thr.join(timeout=2)
            print("LISTENING")
        elif line0.startswith("BYE "):
            sa.send(sip_response(t, 200, "OK", to_tag=our_tag, src=src, port_c=port_c))
        elif line0.startswith("OPTIONS "):
            sa.send(sip_response(t, 200, "OK", to_tag=our_tag, src=src, port_c=port_c))
    return 0


def parse_sdp(text: str) -> dict:
    body = text.split("\r\n\r\n", 1)
    sdp = body[1] if len(body) > 1 else ""
    info = {"ip": None, "port": None, "pts": []}
    for line in sdp.splitlines():
        if line.startswith("c=IN IP6 "):
            info["ip"] = line.split()[-1]
        elif line.startswith("c=IN IP4 "):
            info["ip"] = line.split()[-1]
        elif line.startswith("m=audio "):
            parts = line.split()
            info["port"] = int(parts[1])
            info["pts"] = [int(x) for x in parts[3:] if x.isdigit()]
    return info


def send_pcmu(src: str, dest_ip: str, dest_port: int, stop, path="/tmp/aurel-vm.ulaw", iface="qmapmux0.1"):
    data = open(path, "rb").read()
    fam = socket.AF_INET6 if ":" in dest_ip else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode() + b"\x00")
    except OSError as e:
        print("RTP_BINDDEV", type(e).__name__)
    sock.bind((src, 40000))
    seq = 0
    ts = 0
    ssrc = 0xA11E0001
    sent = 0
    loops = 0
    while not stop.is_set():
        marker = 1
        for i in range(0, len(data), 160):
            if stop.is_set():
                break
            frame = data[i : i + 160]
            if len(frame) < 160:
                frame = frame + b"\xff" * (160 - len(frame))
            hdr = struct.pack("!BBHII", 0x80, marker, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            sock.sendto(hdr + frame, (dest_ip, dest_port))
            marker = 0
            seq += 1
            ts += 160
            sent += 1
            time.sleep(0.02)
        loops += 1
    sock.close()
    print("RTP_PCMU_FRAMES", sent, "LOOPS", loops)


def aor_from_impu(impu: str) -> str:
    if impu.startswith("tel:") or impu.startswith("sip:"):
        return impu
    return f"sip:{impu}"


def build_invite(*, aor, realm, src, port_c, pcscf_port_s, routes, ss, dest=None) -> bytes:
    dest = dest or DEST
    tag = uuid.uuid4().hex[:12]
    call_id = str(uuid.uuid4())
    branch = "z9hG4bK" + uuid.uuid4().hex[:12]
    host = f"[{src}]"
    sdp = (
        "v=0\r\n"
        f"o=- {int(time.time())} 1 IN IP6 {src}\r\n"
        "s=-\r\n"
        f"c=IN IP6 {src}\r\n"
        "t=0 0\r\n"
        "m=audio 40000 RTP/AVP 0 96 97 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:96 AMR-WB/16000/1\r\n"
        "a=fmtp:96 octet-align=0;mode-change-capability=2\r\n"
        "a=rtpmap:97 AMR/8000/1\r\n"
        "a=fmtp:97 octet-align=0\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
        "a=ptime:20\r\n"
        "a=maxptime:240\r\n"
        "a=sendrecv\r\n"
    )
    lines = [
        f"INVITE {dest} SIP/2.0",
        f"Via: SIP/2.0/UDP {host}:{port_c};branch={branch};rport",
        "Max-Forwards: 70",
    ]
    for r in routes:
        lines.append(f"Route: {r}")
    lines += [
        f"From: <{aor}>;tag={tag}",
        f"To: <{dest}>",
        f"Call-ID: {call_id}",
        "CSeq: 1 INVITE",
        f"Contact: <sip:{aor.split(':')[-1].split('@')[0]}@{host}:{port_c}>;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\";audio",
        f"P-Preferred-Identity: <{aor}>",
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD",
        "Allow: INVITE, ACK, CANCEL, BYE, UPDATE, PRACK, INFO, OPTIONS",
        "Supported: 100rel, replaces, timer",
        "Require: sec-agree",
        "Proxy-Require: sec-agree",
        f"Security-Verify: {ss}",
        "Accept-Contact: *;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"",
        "Content-Type: application/sdp",
        f"Content-Length: {len(sdp)}",
        "",
        sdp,
    ]
    return "\r\n".join(lines).encode(), tag, call_id


def build_prack(*, dest, aor, src, port_c, routes, from_tag, to_tag, call_id, ss, rseq, inv_cseq=1) -> bytes:
    host = f"[{src}]"
    branch = "z9hG4bK" + uuid.uuid4().hex[:12]
    lines = [
        f"PRACK {dest} SIP/2.0",
        f"Via: SIP/2.0/UDP {host}:{port_c};branch={branch};rport",
        "Max-Forwards: 70",
    ]
    for r in routes:
        lines.append(f"Route: {r}")
    lines += [
        f"From: <{aor}>;tag={from_tag}",
        f"To: <{dest}>;tag={to_tag}",
        f"Call-ID: {call_id}",
        "CSeq: 2 PRACK",
        f"RAck: {rseq} {inv_cseq} INVITE",
        "Require: sec-agree",
        "Proxy-Require: sec-agree",
        f"Security-Verify: {ss}",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def build_ack(*, dest, aor, src, port_c, routes, from_tag, to_tag, call_id, ss) -> bytes:
    host = f"[{src}]"
    branch = "z9hG4bK" + uuid.uuid4().hex[:12]
    lines = [
        f"ACK {dest} SIP/2.0",
        f"Via: SIP/2.0/UDP {host}:{port_c};branch={branch};rport",
        "Max-Forwards: 70",
    ]
    for r in routes:
        lines.append(f"Route: {r}")
    lines += [
        f"From: <{aor}>;tag={from_tag}",
        f"To: <{dest}>;tag={to_tag}",
        f"Call-ID: {call_id}",
        "CSeq: 1 ACK",
        "Require: sec-agree",
        "Proxy-Require: sec-agree",
        f"Security-Verify: {ss}",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def build_bye(*, dest, aor, src, port_c, routes, from_tag, to_tag, call_id, ss) -> bytes:
    host = f"[{src}]"
    branch = "z9hG4bK" + uuid.uuid4().hex[:12]
    lines = [
        f"BYE {dest} SIP/2.0",
        f"Via: SIP/2.0/UDP {host}:{port_c};branch={branch};rport",
        "Max-Forwards: 70",
    ]
    for r in routes:
        lines.append(f"Route: {r}")
    lines += [
        f"From: <{aor}>;tag={from_tag}",
        f"To: <{dest}>;tag={to_tag}",
        f"Call-ID: {call_id}",
        "CSeq: 2 BYE",
        "Require: sec-agree",
        "Proxy-Require: sec-agree",
        f"Security-Verify: {ss}",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def main() -> int:
    global DEST
    argv = sys.argv[1:]
    mode = "register"
    number = None
    if argv:
        if argv[0] in ("register", "dial"):
            mode = argv[0]
            if mode == "dial":
                if len(argv) < 2:
                    print("usage: joan-ims dial +15551212")
                    return 2
                number = argv[1]
        elif argv[0].startswith("+") or argv[0].startswith("sip:"):
            mode = "dial"
            number = argv[0]
    out = subprocess.check_output(["mmcli", "-b", "2"], text=True)
    iface = None
    for line in out.splitlines():
        if "interface:" in line:
            iface = line.split(":", 1)[1].strip()
    show = subprocess.check_output(["ip", "-6", "addr", "show", iface], text=True)
    src = None
    for line in show.splitlines():
        if "inet6 2607:" in line and "tentative" not in line and "dadfailed" not in line:
            src = line.split()[1].split("/")[0]
            break
    pcs, gw = wds_pcscf()
    if not (src and pcs and gw):
        print("MISSING", bool(src), bool(pcs), bool(gw))
        return 2
    target = pcs[0]
    sudo(["ip", "-6", "route", "replace", "fd00:976a::/32", "via", gw, "dev", iface, "src", src])
    env = {}
    isim_path = None
    for p in ISIM_ENV_CANDIDATES:
        if os.path.isfile(p):
            isim_path = p
            break
    if isim_path:
        with open(isim_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k] = v.strip().strip("\"'")
        print("ISIM_ENV_OK")
    else:
        try:
            env = read_isim_card()
            print("ISIM_CARD_OK", sorted(k for k in env))
            cache = "/var/lib/joan-imsd/isim.env"
            if env.get("JOAN_IMPI") and os.path.isdir("/var/lib/joan-imsd"):
                tmp = cache + ".tmp"
                with open(tmp, "w") as f:
                    for k, v in env.items():
                        f.write(f"{k}={v}\n")
                os.chmod(tmp, 0o600)
                os.replace(tmp, cache)
        except Exception as e:
            print("ISIM_CARD_FAIL", type(e).__name__)
            return 3
    if "JOAN_IMPI" not in env or "JOAN_REALM" not in env:
        print("NO_ISIM_IDENTITY")
        return 3
    impi = env["JOAN_IMPI"]
    realm = env["JOAN_REALM"]
    impu = env.get("JOAN_IMPU")
    imei = get_imei()
    print("IMEI_OK", bool(imei), "MSISDN_OK", bool(get_msisdn()))
    if number:
        if number.startswith("sip:"):
            DEST = number
        else:
            if number.isdigit() and not number.startswith("+"):
                number = "+" + number
            DEST = f"sip:{number}@{realm}"
    if mode == "dial" and not DEST:
        print("NO_DEST")
        return 2
    aor = aor_from_impu(impu or impi)
    spi_c, spi_s = 0x11110001, 0x11110002
    port_c, port_s = 15000, 16000

    # Drop leftover binding from prior 200 OK (Expires was 600000s).
    sock0 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    sock0.settimeout(5)
    sock0.bind((src, 5060))
    dreg = reg.build_register(
        impi=impi, realm=realm, local_ip=src, local_port=5060,
        pcscf=target, pcscf_port=5060, impu=impu, expires=0,
        spi_c=spi_c, spi_s=spi_s, port_c=port_c, port_s=port_s, imei=imei,
    )
    sock0.sendto(dreg, (target, 5060))
    try:
        dtext = sock0.recvfrom(4096)[0].decode("utf-8", "replace")
    except socket.timeout:
        print("DEREG_TIMEOUT")
        dtext = ""
    dline = dtext.splitlines()[0] if dtext else "none"
    print("DEREG1", dline)
    dh = sip_headers(dtext) if dtext else {}
    if "401" in dline:
        dnonce = reg.parse_www_authenticate(hdr1(dh, "www-authenticate")).get("nonce", "")
        dss = hdr1(dh, "security-server")
        ch = open_isim()
        rand, autn = reg.split_aka_nonce(dnonce)
        apdu = bytes.fromhex("0088008122") + b"\x10" + rand + b"\x10" + autn
        resp = send_apdu(ch, apdu)
        if len(resp) >= 2 and resp[-2] == 0x61:
            resp = send_apdu(ch, bytes.fromhex("00C00000") + bytes([resp[-1]]))
        resb, ck, ik = parse_aka_success(resp)
        qmicli(f"--uim-close-logical-channel=1,{ch}")
        dcall = dtag = None
        for line in dreg.decode().splitlines():
            if line.lower().startswith("call-id:"):
                dcall = line.split(":", 1)[1].strip()
            if line.lower().startswith("from:") and "tag=" in line:
                dtag = line.rsplit("tag=", 1)[1].strip()
        dreg2 = reg.build_register(
            impi=impi, realm=realm, local_ip=src, local_port=5060,
            pcscf=target, pcscf_port=5060, impu=impu, expires=0,
            call_id=dcall, from_tag=dtag, cseq=2, nonce=dnonce, res=resb,
            spi_c=spi_c, spi_s=spi_s, port_c=port_c, port_s=port_s,
            security_verify=dss or None, imei=imei,
        )
        sock0.sendto(dreg2, (target, 5060))
        try:
            print("DEREG2", sock0.recvfrom(4096)[0].decode("utf-8", "replace").splitlines()[0])
        except socket.timeout:
            print("DEREG2_TIMEOUT")
    sock0.close()
    time.sleep(1)

    msg1 = reg.build_register(
        impi=impi, realm=realm, local_ip=src, local_port=5060,
        pcscf=target, pcscf_port=5060, impu=impu,
        spi_c=spi_c, spi_s=spi_s, port_c=port_c, port_s=port_s, imei=imei,
    )
    call_id = from_tag = None
    for line in msg1.decode().splitlines():
        if line.lower().startswith("call-id:"):
            call_id = line.split(":", 1)[1].strip()
        if line.lower().startswith("from:") and "tag=" in line:
            from_tag = line.rsplit("tag=", 1)[1].strip()

    sock1 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    sock1.settimeout(5)
    sock1.bind((src, 5060))
    sock1.sendto(msg1, (target, 5060))
    try:
        data, addr = sock1.recvfrom(4096)
    except socket.timeout:
        print("FIRST_TIMEOUT")
        return 5
    text = data.decode("utf-8", "replace")
    print("FIRST", text.splitlines()[0])
    hdrs = sip_headers(text)
    if "401" not in text.splitlines()[0]:
        print("NOT_401")
        return 6
    nonce = reg.parse_www_authenticate(hdr1(hdrs, "www-authenticate")).get("nonce", "")
    ss = hdr1(hdrs, "security-server")
    print("HDR401", sorted(hdrs.keys()))
    if not ss:
        print("NO_SEC_SERVER")
        return 6
    rand, autn = reg.split_aka_nonce(nonce)
    ch = open_isim()
    apdu = bytes.fromhex("0088008122") + b"\x10" + rand + b"\x10" + autn
    resp = send_apdu(ch, apdu)
    if len(resp) >= 2 and resp[-2] == 0x61:
        resp = send_apdu(ch, bytes.fromhex("00C00000") + bytes([resp[-1]]))
    resb, ck, ik = parse_aka_success(resp)
    print("AKA_OK", len(resb), len(ck), len(ik))
    qmicli(f"--uim-close-logical-channel=1,{ch}")
    pcscf_sec = ipsec.parse_sec_agree(ss)

    msg2 = reg.build_register(
        impi=impi, realm=realm, local_ip=src, local_port=port_c,
        pcscf=target, pcscf_port=pcscf_sec.port_s, impu=impu,
        call_id=call_id, from_tag=from_tag, cseq=2, nonce=nonce, res=resb,
        spi_c=spi_c, spi_s=spi_s, port_c=port_c, port_s=port_s,
        ck=ck, ik=ik, security_verify=ss, imei=imei,
    )
    src_b = socket.inet_pton(socket.AF_INET6, src)
    dst_b = socket.inet_pton(socket.AF_INET6, target)
    sa = EspSa(src, target, src_b, dst_b, port_c, pcscf_sec.port_s, pcscf_sec.spi_s, ck, ik)
    sa.send(msg2)
    try:
        t2, h2 = sa.recv(12)
    except socket.timeout:
        print("REG2_TIMEOUT")
        return 7
    print("REG2", t2.splitlines()[0], "LEN", len(t2))
    if "200" not in t2.splitlines()[0]:
        print("REG2_NOT_200")
        return 8
    routes = []
    pcscf_lr = f"<sip:[{target}]:{pcscf_sec.port_s};lr>"
    routes.append(pcscf_lr)
    for sr in h2.get("service-route", []):
        if sr not in routes:
            routes.append(sr)
    print("ROUTE_N", len(routes), "HAS_SVC", bool(h2.get("service-route")))
    print("HDR200", sorted(h2.keys()))
    print("HAS_PAU", "p-associated-uri" in h2, "HAS_PATH", "path" in h2)
    print("VSS", vss_set_ims_registered())
    print("REGISTERED")
    if os.environ.get("JOAN_IMS_MODE", mode) == "register":
        return listen_incoming(sa, src=src, port_c=port_c, iface=iface)

    inv, inv_tag, inv_cid = build_invite(
        aor=aor, realm=realm, src=src, port_c=port_c,
        pcscf_port_s=pcscf_sec.port_s, routes=routes, ss=ss,
    )
    sa.send(inv)
    print("INVITE_SENT", len(inv))
    codes = []
    to_tag = ""
    pracked = False
    last_sdp = {}
    deadline = time.time() + 55
    while time.time() < deadline:
        try:
            t, h = sa.recv(max(1, deadline - time.time()))
        except socket.timeout:
            break
        line0 = t.splitlines()[0] if t else ""
        print("INV", line0, "LEN", len(t))
        codes.append(line0)
        to = hdr1(h, "to")
        if "tag=" in to:
            to_tag = to.rsplit("tag=", 1)[1].strip()
        for k in ("warning", "reason", "require", "rseq", "supported"):
            if k in h:
                v = hdr1(h, k)
                print("HDR", k, v[:120] if "nonce" not in v.lower() else "present")
        if "\r\n\r\nm=audio" in t.replace("\n", "\r\n") or "\nm=audio" in t:
            last_sdp = parse_sdp(t)
            print("SDP_PORT", last_sdp.get("port"), "PTS", last_sdp.get("pts"), "IP_SET", bool(last_sdp.get("ip")))
        req = hdr1(h, "require")
        rseq = hdr1(h, "rseq")
        if (not pracked) and to_tag and rseq and "100rel" in req.lower():
            prack = build_prack(
                dest=DEST, aor=aor, src=src, port_c=port_c, routes=routes,
                from_tag=inv_tag, to_tag=to_tag, call_id=inv_cid, ss=ss, rseq=rseq,
            )
            sa.send(prack)
            pracked = True
            print("PRACK_SENT")
        if line0.startswith("SIP/2.0 2") and "200" in line0:
            break
        if line0.startswith(("SIP/2.0 4", "SIP/2.0 5", "SIP/2.0 6")):
            break
    print("INV_CODES", len(codes))
    if any("SIP/2.0 200" in c for c in codes) and to_tag:
        ack = build_ack(dest=DEST, aor=aor, src=src, port_c=port_c, routes=routes,
                        from_tag=inv_tag, to_tag=to_tag, call_id=inv_cid, ss=ss)
        sa.send(ack)
        print("ACK_SENT")
        stop = threading.Event()
        rtp_thr = None
        if last_sdp.get("ip") and last_sdp.get("port") and 0 in (last_sdp.get("pts") or []):
            rtp_thr = threading.Thread(
                target=send_pcmu,
                args=(src, last_sdp["ip"], last_sdp["port"], stop),
                daemon=True,
            )
            rtp_thr.start()
            print("RTP_LOOP_START")
        else:
            print("RTP_SKIP", last_sdp)
        hangup = False
        end = time.time() + 180
        while time.time() < end and not hangup:
            try:
                t, h = sa.recv(max(1, min(8, end - time.time())))
            except socket.timeout:
                continue
            except ValueError:
                continue
            line0 = t.splitlines()[0] if t else ""
            print("INCALL", line0)
            if line0.startswith("BYE "):
                hangup = True
                # 200 OK for their BYE
                print("REMOTE_BYE")
        stop.set()
        if rtp_thr:
            rtp_thr.join(timeout=2)
        if not hangup:
            bye = build_bye(dest=DEST, aor=aor, src=src, port_c=port_c, routes=routes,
                            from_tag=inv_tag, to_tag=to_tag, call_id=inv_cid, ss=ss)
            sa.send(bye)
            print("BYE_SENT")
            try:
                t, _ = sa.recv(5)
                print("BYE_RSP", t.splitlines()[0])
            except socket.timeout:
                print("BYE_TIMEOUT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
