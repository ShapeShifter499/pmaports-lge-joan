# First boot: VoLTE on an LG V30 (joan) running postmarketOS

You flashed pmOS onto a V30. This is the user-facing path — not a lab script.

## 1. Install the device stack

On a finished image these should already be pulled in by `lge-joan-volte`.
If you built a minimal rootfs:

```sh
sudo apk add lge-joan-volte
# pulls: firmware (separate), modemmanager, rmtfs, 81voltd, calls, joan-imsd
```

Enable the services that own the modem and IMS PDN:

```sh
sudo rc-update add rmtfs default
sudo rc-update add modemmanager default
sudo rc-update add 81voltd default
sudo rc-update add joan-imsd default
sudo rc-service rmtfs start
sudo rc-service modemmanager start
sudo rc-service 81voltd start
```

## 2. First SIM / first radio

Unlock the SIM if asked. Wait until the modem is home on LTE:

```sh
mmcli -m 0
# operator name: T-Mobile (or your carrier)
# packet service state: attached
```

Internet PDN (already often auto-connected):

```sh
mmcli -m 0 --simple-connect=apn=fast.t-mobile.com,ip-type=ipv6
```

IMS PDN — ModemManager + 81voltd (QMI IMS data, QRTR 770):

```sh
mmcli -m 0 --simple-connect=apn=ims,ip-type=ipv6
```

## 3. Identity (once)

joan-imsd reads ISIM from:

- `/etc/joan-imsd/isim.env` (preferred, mode 0600)
- `/var/lib/joan-imsd/isim.env`

Do not put IMPI in a chat log. A helper can fill this from QMI UIM on
first boot later; today copy the live env once:

```sh
sudo install -m 0600 /tmp/isim-live.env /etc/joan-imsd/isim.env
```

## 4. Register and call

```sh
sudo rc-service joan-imsd start
# log: REGISTERED (SIP 200) — VoLTE signalling is up

joan-ims dial +19165550100
```

Phosh **Calls** is installed (`calls` package) so the phone looks like
every other pmOS device. Its ModemManager backend still talks **CS
voice** (QMI Voice). T-Mobile US has no CS. Until an MM IMS Voice
plugin exists, `joan-ims dial` is the VoLTE path. CS dial in Calls
will fail; that is expected.

## 5. Kernel IPsec (later image)

This UA can do userspace ESP. The portable path is kernel xfrm:

- `CONFIG_XFRM_USER=y`
- `CONFIG_INET6_ESP=y`

Rebuild/boot that kernel when packing a real image. RAM boots without
those options still call via userspace ESP.

## 6. Carriers

The UA is 3GPP IMS, not T-Mobile-only. Live proof is T-Mobile US
(`usr/share/joan-imsd/profiles/tmo-us.yaml`). Another carrier is a
new profile file, not a new daemon.

## 7. Models

Same package on US998 and H932. Do **not** cross-flash bootloaders
(`abl`/`xbl`/`tz`/`hyp`/`keymaster`/`laf`).
