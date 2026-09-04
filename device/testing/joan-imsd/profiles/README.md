# IMS carrier profiles

The SIP UA is 3GPP (REGISTER, AKA, sec-agree, IPsec, INVITE).
Carrier differences live here, not in the protocol code.

| File | Carrier | Proven on air |
|---|---|---|
| `tmo-us.yaml` | T-Mobile US 310260 | 2026-08-26 joan pmOS: REGISTER 200, INVITE 200, PCMU heard |

Add a new file for another PLMN. Do not fork the UA.
Unproven until that SIM answers 401/200 the same way.
