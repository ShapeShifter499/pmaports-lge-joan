#!/usr/bin/env python3
"""AES-128-CBC + HMAC-SHA1-96 ESP transport (RFC 4303) for IMS SIP.

Used because joan 7.2.0-rc2 has CONFIG_INET6_ESP=n and CONFIG_XFRM_USER=n.
Not a vendor blob. Self-test uses NIST AES-128 ECB vector.
"""
from __future__ import annotations

import hmac
import hashlib
import os
import struct

# FIPS-197 S-box
SBOX = bytes(
    [
        0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
        0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
        0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
        0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
        0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
        0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
        0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
        0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
        0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
        0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
        0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
        0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
        0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
        0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
        0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
        0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
    ]
)
INV_SBOX = bytes(SBOX.index(i) for i in range(256))
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _expand(key: bytes) -> list[list[int]]:
    w = list(key)
    i = 1
    while len(w) < 176:
        t = w[-4:]
        if len(w) % 16 == 0:
            t = [SBOX[t[1]] ^ RCON[i], SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]
            i += 1
        w.extend(a ^ b for a, b in zip(w[-16:-12], t))
    return [w[i : i + 16] for i in range(0, 176, 16)]


def _mix(s: list[int], inv: bool = False) -> None:
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = s[i : i + 4]
        if not inv:
            s[i] = _xtime(a0) ^ _xtime(a1) ^ a1 ^ a2 ^ a3
            s[i + 1] = a0 ^ _xtime(a1) ^ _xtime(a2) ^ a2 ^ a3
            s[i + 2] = a0 ^ a1 ^ _xtime(a2) ^ _xtime(a3) ^ a3
            s[i + 3] = _xtime(a0) ^ a0 ^ a1 ^ a2 ^ _xtime(a3)
        else:
            def m(x, n):
                r = 0
                y = x
                for bit in range(4):
                    if n & (1 << bit):
                        r ^= y
                    y = _xtime(y)
                return r
            s[i] = m(a0, 14) ^ m(a1, 11) ^ m(a2, 13) ^ m(a3, 9)
            s[i + 1] = m(a0, 9) ^ m(a1, 14) ^ m(a2, 11) ^ m(a3, 13)
            s[i + 2] = m(a0, 13) ^ m(a1, 9) ^ m(a2, 14) ^ m(a3, 11)
            s[i + 3] = m(a0, 11) ^ m(a1, 13) ^ m(a2, 9) ^ m(a3, 14)


def _shift(s: list[int], inv: bool = False) -> list[int]:
    n = s[:]
    if not inv:
        n[1], n[5], n[9], n[13] = s[5], s[9], s[13], s[1]
        n[2], n[6], n[10], n[14] = s[10], s[14], s[2], s[6]
        n[3], n[7], n[11], n[15] = s[15], s[3], s[7], s[11]
    else:
        n[1], n[5], n[9], n[13] = s[13], s[1], s[5], s[9]
        n[2], n[6], n[10], n[14] = s[10], s[14], s[2], s[6]
        n[3], n[7], n[11], n[15] = s[7], s[11], s[15], s[3]
    return n


def _aes_block(block: bytes, rounds: list[list[int]], decrypt: bool) -> bytes:
    if not decrypt:
        s = [block[i] ^ rounds[0][i] for i in range(16)]
        for r in rounds[1:-1]:
            s = [SBOX[x] for x in s]
            s = _shift(s)
            _mix(s)
            s = [s[i] ^ r[i] for i in range(16)]
        s = [SBOX[x] for x in s]
        s = _shift(s)
        s = [s[i] ^ rounds[-1][i] for i in range(16)]
    else:
        s = [block[i] ^ rounds[-1][i] for i in range(16)]
        for r in reversed(rounds[1:-1]):
            s = _shift(s, inv=True)
            s = [INV_SBOX[x] for x in s]
            s = [s[i] ^ r[i] for i in range(16)]
            _mix(s, inv=True)
        s = _shift(s, inv=True)
        s = [INV_SBOX[x] for x in s]
        s = [s[i] ^ rounds[0][i] for i in range(16)]
    return bytes(s)


def aes128_cbc_encrypt(key: bytes, iv: bytes, pt: bytes) -> bytes:
    rnd = _expand(key)
    out = b""
    prev = iv
    for i in range(0, len(pt), 16):
        blk = bytes(a ^ b for a, b in zip(pt[i : i + 16], prev))
        enc = _aes_block(blk, rnd, False)
        out += enc
        prev = enc
    return out


def aes128_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    rnd = _expand(key)
    out = b""
    prev = iv
    for i in range(0, len(ct), 16):
        dec = _aes_block(ct[i : i + 16], rnd, True)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = ct[i : i + 16]
    return out


def sha1_96(key: bytes, data: bytes) -> bytes:
    if len(key) < 20:
        key = key + b"\x00" * (20 - len(key))
    return hmac.new(key, data, hashlib.sha1).digest()[:12]


def udp_checksum(src: bytes, dst: bytes, udp: bytes) -> int:
    ph = src + dst + struct.pack("!I", len(udp)) + b"\x00\x00\x00\x11"
    data = ph + udp
    if len(data) % 2:
        data += b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def build_udp(src_ip: bytes, dst_ip: bytes, sport: int, dport: int, payload: bytes) -> bytes:
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    csum = udp_checksum(src_ip, dst_ip, udp)
    if csum == 0:
        csum = 0xFFFF
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), csum) + payload


def esp_protect(*, spi: int, seq: int, ck: bytes, ik: bytes, inner: bytes) -> bytes:
    pad_need = 16 - ((len(inner) + 2) % 16)
    if pad_need == 16:
        pad_need = 0
    pad = bytes(range(1, pad_need + 1))
    inner_p = inner + pad + bytes([pad_need, 17])  # next hdr UDP
    iv = os.urandom(16)
    ct = aes128_cbc_encrypt(ck[:16], iv, inner_p)
    hdr = struct.pack("!II", spi, seq) + iv + ct
    return hdr + sha1_96(ik, hdr)


def esp_unprotect(pkt: bytes, ck: bytes, ik: bytes, check_icv: bool = True) -> bytes:
    if len(pkt) < 8 + 16 + 16 + 12:
        raise ValueError("short esp")
    body, icv = pkt[:-12], pkt[-12:]
    if check_icv and sha1_96(ik, body) != icv:
        raise ValueError("esp icv")
    iv, ct = body[8:24], body[24:]
    pt = aes128_cbc_decrypt(ck[:16], iv, ct)
    pad_len, nh = pt[-2], pt[-1]
    if nh != 17:
        raise ValueError(f"esp nh {nh}")
    return pt[: -(2 + pad_len)]


def self_test() -> int:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    ct = _aes_block(pt, _expand(key), False)
    expect = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    if ct != expect:
        print("AES_ECB_FAIL", ct.hex())
        return 1
    iv = bytes(16)
    enc = aes128_cbc_encrypt(key, iv, pt)
    if aes128_cbc_decrypt(key, iv, enc) != pt:
        print("AES_CBC_FAIL")
        return 1
    inner = b"\x00" * 8 + b"hello-esp-pad!!"  # not real udp; length for pad
    # use real udp-shaped 8+16
    inner = bytes(24)
    pkt = esp_protect(spi=1, seq=1, ck=key, ik=key + b"\x00" * 4, inner=inner)
    out = esp_unprotect(pkt, key, key + b"\x00" * 4)
    if out != inner:
        print("ESP_ROUNDTRIP_FAIL")
        return 1
    print("ESP_SELF_TEST_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
