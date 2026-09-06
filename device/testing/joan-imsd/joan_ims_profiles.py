#!/usr/bin/env python3
"""Carrier profile layer for joan-imsd.

Two data sources, merged per operator (overlay wins):

1. ``/usr/share/joan-imsd/carrier-profiles.json`` — the 164-entry carrier
   table distilled from stock Ims6.apk ``assets/Configuration/`` (see
   tools/make-carrier-profiles.py in the joan-volte-lineage repo). Fields are
   the stock XML knobs: ipsec_algs, ipsec_spi_3gpp, conf_uri, pcscf_port,
   max_sessions, offer_res_code, cw_type, ...
2. ``profiles/*.yaml`` — pmOS-specific overlays (pcscf discovery, AKA
   algorithm, SDP payloads). tmo-us.yaml is the proven live profile.

Transport is NOT taken from the stock XML blindly: libims derives it from
the per-registration ``common_tcp_criterion_len`` knob (CMCC 1300, KR 4096,
T-Mobile 0 = disabled) — see docs/lg-ims-carrier-extract-2026-09-04.md.
The alpha7 blanket MCC-460 forced-TCP was disproven in the field.
"""

from __future__ import annotations

import json
import os
import sys

try:
    import yaml
except ImportError:  # minimal rootfs without py3-yaml
    yaml = None

PKG_SHARE = "/usr/share/joan-imsd"

# tcp_criterion_len from the V300L world XML extract (0 = disabled/udp).
TCP_CRITERION = {
    "CMCC.CN": 1300,
    "LGU.KR": 4096,
    "KT.KR": 4096,
    "SKT.KR": 4096,
    "TMO.US.NAO": 0,
    "ATT.US.NAO": 0,
    "VZW.US.VOWIFI": 0,
}

# MCC -> carrier profile key in carrier-profiles.json.
MCC_KEY = {
    "460": "CMCC.CN",
    "310": None,  # resolved by MNC below
    "311": None,
    "450": "LGU.KR",
    "208": None,
}

MNC_KEY = {
    "310": {"260": "TMO.US.NAO", "410": "ATT.US.NAO", "120": "SPR.US"},
    "311": {"480": "VZW.US.VOWIFI"},
    "208": {"10": "ORANGE.FR", "01": "ORANGE.FR"},
}


def profile_key(mcc: str, mnc: str) -> str | None:
    """Stock profile key for a PLMN, or None."""
    if mcc in MNC_KEY and mnc in MNC_KEY[mcc]:
        return MNC_KEY[mcc][mnc]
    return MCC_KEY.get(mcc)


def load_carrier_json(path: str | None = None) -> dict:
    path = path or os.path.join(PKG_SHARE, "carrier-profiles.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_overlay_dir(path: str | None = None) -> dict[str, dict]:
    """profiles/*.yaml keyed by plmn (e.g. "310260")."""
    path = path or os.path.join(PKG_SHARE, "profiles")
    out: dict[str, dict] = {}
    if yaml is None or not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        try:
            with open(os.path.join(path, fn), encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("plmn"):
            out[str(doc["plmn"])] = doc
    return out


def select(mcc: str, mnc: str, overlays: dict[str, dict] | None = None,
           carriers: dict | None = None) -> dict:
    """Merged profile for a PLMN: overlay (if any) over the stock table."""
    overlays = overlays or load_overlay_dir()
    carriers = carriers or load_carrier_json()
    key = profile_key(mcc, mnc)
    plmn = f"{mcc}{mnc}"
    base: dict = {}
    if key and key in carriers:
        base = dict(carriers[key])
    if plmn in overlays:
        base.update(overlays[plmn])
    elif mcc in overlays:
        base.update(overlays[mcc])
    base.setdefault("profile_key", key)
    base.setdefault("plmn", plmn)
    base.setdefault("tcp_criterion_len", TCP_CRITERION.get(key or "", 0))
    return base


def self_test() -> int:
    sel = select("460", "11", overlays={}, carriers={"CMCC.CN": {"ipsec": True}})
    if sel.get("tcp_criterion_len") != 1300 or sel.get("profile_key") != "CMCC.CN":
        print("CMCC select fail", sel, file=sys.stderr)
        return 1
    sel = select("310", "260", overlays={}, carriers={"TMO.US.NAO": {"ipsec": True}})
    if sel.get("tcp_criterion_len") != 0 or sel.get("profile_key") != "TMO.US.NAO":
        print("TMO select fail", sel, file=sys.stderr)
        return 1
    if profile_key("450", "06") != "LGU.KR":
        print("LGU key fail", file=sys.stderr)
        return 1
    print("PROFILES_SELF_TEST_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
