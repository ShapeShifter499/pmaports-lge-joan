# alsa-ucm-conf-lg-joan

ALSA Use Case Manager profile for the LG V30 (joan).

Signed-off-by: Lance <Gero3977@gmail.com>
Assisted-by: Claude-Code:claude-opus-5-5
Date: 2026-10-13

## Layout

The layout follows the sdm845 phones in `alsa-ucm-conf-sdm845` (OnePlus 6,
Poco F1, SHIFT6mq):

```
ucm2/LG/joan/joan.conf          entry point: use case list + BootSequence
ucm2/LG/joan/HiFi.conf          the HiFi verb and its devices
ucm2/conf.d/sdm845/LG-V30.conf  -> ../../LG/joan/joan.conf
```

alsa-lib's `ucm.conf` probes `conf.d/<card driver>/<card long name>.conf`.
On joan the driver is `sdm845` and the long name is `LG-V30`. The top-level
`ucm2/<CardId>/` paths that r7 to r9 used are only probed with `V2Name`
enabled, and it is off by default in alsa-lib 1.2.16.

## Devices

Each device has its own frontend PCM, as on the OnePlus 6. The verb wires
every frontend to its backend once, and a device only switches its own codec
path. Selecting one output never re-routes another.

| device | PCM | path | priority |
|---|---|---|---|
| Speaker | hw:,0 MM1 | TERT_MI2S_RX -> TFA9872 | 200 |
| Earpiece | hw:,2 MM3 | SLIMBUS_0_RX / AIF1_PB -> RX0 -> INT0 -> EAR PA | 50 |
| Headphones | hw:,4 MM5 | SLIMBUS_1_RX / AIF2_PB -> RX2/RX3 -> HPHL/R, ES9218P in Low Power Bypass | 100 |
| Headphones (Hi-Fi DAC) | hw:,6 MM7 | QUAT_MI2S_RX -> ES9218P | 90 |
| Bottom Microphone | hw:,1 MM2 | ADC1 -> DEC6 -> TX6 -> AIF1_CAP / SLIMBUS_0_TX | 200 |
| Top Microphone | hw:,3 MM4 | ADC3 -> DEC7 -> TX7 -> AIF2_CAP / SLIMBUS_1_TX | 150 |
| Headset Microphone | hw:,5 MM6 | ADC2 -> DEC0 -> TX0 -> AIF3_CAP / SLIMBUS_2_TX | 100 |

The two headphone devices conflict, because both drive the jack through the
ES9218P mode pins.

## Kernel requirements

This profile needs the joan DTS from `joan/wcd-micbias-mclk`:

- **`"MIC BIAS1..4", "MCLK"` in `audio-routing`.** Without these routes no
  microphone records anything (every sample is 0) unless a WCD playback path
  happens to be running. The codec MCLK, and with it ANA_BIAS, was only ever
  powered by the RX side. LG's msm8998 machine driver carries the same four
  routes.
- **Frontends MultiMedia1-7 and backends SLIMBUS_1_RX, SLIMBUS_1_TX and
  SLIMBUS_2_TX** (wcd9340 AIF2_PB, AIF2_CAP and AIF3_CAP), as in
  sdm845-oneplus-common.dtsi.

## Measured on hardware (US998, 2026-10-13)

The source of each tone was the device's own loudspeaker or earpiece. Captures
were checked with a Goertzel filter at 1 kHz against 950/1050 Hz.

- **Raw ALSA:** speaker to bottom mic 1131x; earpiece to bottom mic 848x; top mic
  (TX7) 475x; with nothing playing, the headset mic shows a live noise floor
  (no headset inserted).
- **Through PipeWire:** speaker to top mic 25424x; earpiece to top mic 2873x;
  speaker to bottom mic 130648x, with the global echo-cancel module removed
  (see `device-lg-joan` r31).
- **Not verified:** headphone outputs by ear (nothing was plugged into the jack).

## Known limits

- **No jack detection** (MBHC). Neither headphone device has a
  `JackControl`, so they are always listed and are picked by hand.
- **DEC8/TX8 records zeros** on mainline. The top mic uses DEC7/TX7, which is
  also what LG's dual-mic paths use.
- **No Voice Call verb yet.**
- **TX COPP Topology stays "None".** LG's SM ECNS topology makes the COPP
  open fail on this firmware.
