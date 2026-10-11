# joan-adsp-watchdog

Mitigation for the LG V30 (joan) "1 in 8 boots has no sound card" defect
(observed on linux-lg-joan r53..r57, kernel 7.2.0-rc2).

## The defect

On an affected boot the ADSP remoteproc reaches "running" and its APR
services come up, but its QRTR services never appear on the ADSP node
(no SLIMbus control service 769). Without 769, qcom,slim-ngd-ctrl cannot
enumerate the WCD9340 codec, so the msm-snd-sdm845 machine driver fails
with "SLIM Playback: codec dai not found" and no ALSA card registers.
Nothing in the 7.2-rc2 qrtr stack retries the handshake, so the state is
permanent for the boot.

Candidate root causes (both fixed upstream after 7.2-rc2, absent from
linux-lg-joan at r57):

- `786439ad5876` rpmsg: glink: smem: order FIFO read after availability
  check — a speculative FIFO read can consume stale bytes and drop the
  packet (qrtr logs "invalid ipcrouter packet").
- `544d85de4dc2` net: qrtr: Send HELLO message on endpoint register —
  the host name service never (re)sends HELLO per endpoint, so a lost
  handshake never recovers.

The real fix is backporting those; this package is the no-rebuild
mitigation while that is pending.

## What this package does

- `joan-adsp-watchdog.service` (oneshot, after multi-user): waits up to
  25 s for the sdm845 card. If the card is there, it exits — a healthy
  ADSP is never touched. If not, it finds the ADSP remoteproc by NAME
  (never number; numbering swaps between boots), logs state and the
  node-5 QRTR service count, then runs up to 3 bounded `echo stop` +
  `echo start` cycles, waiting up to 20 s for the card after each.
  Multi-cycle because the 2026-10-07 live test showed each ADSP restart
  re-rolls the QRTR service announcement (5/6 and 0/6 services observed
  on two cycles; 769 lost both times).
- `joan-adsp-watchdog-recheck.timer`: runs the same oneshot once more at
  t+3 min (bounded second attempt; no-ops when the card exists).
- `joan-adsp-boot-probe.service` (oneshot, early, parallel): at t+5/15/30
  logs remoteproc states, the full `qrtr-lookup` table, /proc/asound/cards,
  /dev/snd, the qrtr tracepoint ring and filtered dmesg under the tag
  `joan-adsp-boot-probe`. Purely diagnostics: it enables the qrtr kernel
  tracepoints so the next natural bad boot records whether the ADSP ever
  sent HELLO / NEW_SERVER.

Journal tags: `joan-adsp-watchdog`, `joan-adsp-boot-probe`.

## Safety properties

- Never restarts a healthy ADSP (restart only happens when the sound card
  is missing after the wait).
- Bounded: 3 restart cycles per run, two runs per boot (boot + t+3 min
  recheck), no loops.
- Refuses to stop a remoteproc when the "stop" write is rejected, and
  never writes to a remoteproc that is not named "adsp".

## Testing

Dry-run (observe only, no writes):
`JOAN_ADSP_WATCHDOG_DRYRUN=1 JOAN_ADSP_WATCHDOG_WAIT=3 /usr/libexec/joan-adsp-watchdog`

Single-sample probe:
`JOAN_ADSP_BOOT_PROBE_SAMPLES=5 /usr/libexec/joan-adsp-boot-probe`
