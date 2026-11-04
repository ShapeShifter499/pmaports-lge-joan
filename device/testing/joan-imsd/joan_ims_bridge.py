"""Local SIP bridge between GNOME Calls and the IMS session.

GNOME Calls registers here as an ordinary SIP account on 127.0.0.1, so it
handles the dialer, ringing, microphone and earpiece unchanged. This module
pairs each local dialog with one on the IMS side and relays PCMU RTP between
them untouched. It knows nothing about AKA or IPsec: it only needs the small
"ims" object below, so another IMS core can replace it later.

One call at a time, which is also all GNOME Calls allows on an account.
"""
import os
import pwd
import re
import select
import socket
import subprocess
import threading
import time
import uuid

LOCAL_IP = "127.0.0.1"
SIP_PORT = 5070
RTP_PORT_LOCAL = 40002
RTP_PORT_IMS = 40000
CARD = "alsa_card.platform-sound"


# Compact header names, which sofia-sip uses.
COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact",
           "l": "content-length", "c": "content-type", "s": "subject", "k": "supported"}


def parse_msg(text):
    """Split SIP text into (first line, lower-cased headers, body)."""
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    hdrs = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            key = COMPACT.get(key.strip().lower(), key.strip().lower())
            hdrs.setdefault(key, []).append(value.strip())
    return lines[0], hdrs, body


def hdr(hdrs, name, default=""):
    return (hdrs.get(name) or [default])[0]


def sdp_media(body):
    """(ip, port, payload types) from the first audio line of an SDP body."""
    ip, port, pts = None, None, []
    for line in body.splitlines():
        if line.startswith("c=IN IP"):
            ip = line.split()[-1]
        elif line.startswith("m=audio"):
            fields = line.split()
            port = int(fields[1])
            pts = [int(p) for p in fields[3:] if p.isdigit()]
    return ip, port, pts


def local_sdp():
    return (
        "v=0\r\n"
        f"o=- {int(time.time())} 1 IN IP4 {LOCAL_IP}\r\n"
        "s=-\r\n"
        f"c=IN IP4 {LOCAL_IP}\r\n"
        "t=0 0\r\n"
        f"m=audio {RTP_PORT_LOCAL} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendrecv\r\n"
    )


def session_user():
    """(name, uid) of the logged-in user, skipping the greeter's session."""
    for entry in sorted(os.listdir("/run/user")):
        if entry.isdigit() and int(entry) >= 1000:
            return pwd.getpwuid(int(entry)).pw_name, int(entry)
    return None, None


def set_call_audio(active):
    """Ask callaudiod for call mode, as Calls does for cellular calls only.

    libcall-ui treats a SIP call as VoIP and leaves the audio in media mode on
    the speaker, but on this bridge it is a real phone call.
    """
    name, uid = session_user()
    if not name:
        return
    subprocess.Popen(
        ["sudo", "-u", name, "env", f"XDG_RUNTIME_DIR=/run/user/{uid}",
         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
         "callaudiocli", "-m", "1" if active else "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("CALL_AUDIO", "on" if active else "off")


class SpeakerFollower(threading.Thread):
    """Apply the in-call speaker button.

    PipeWire will not put two devices that share a PCM in one profile, so the
    earpiece and speaker are separate Voice Call profiles, while callaudiod
    only switches ports inside a sink. It still records the button in
    SpeakerState, so follow that for the length of the call.
    """

    PROFILES = {0: "Voice Call (Earpiece, Mic1)", 1: "Voice Call (Mic1, Speaker)"}

    def __init__(self):
        super().__init__(daemon=True)
        self.name_, self.uid = session_user()
        self.proc = None

    def user_cmd(self, *cmd):
        return ["sudo", "-u", self.name_, "env", f"XDG_RUNTIME_DIR=/run/user/{self.uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{self.uid}/bus", *cmd]

    def run(self):
        if not self.name_:
            return
        self.proc = subprocess.Popen(
            self.user_cmd("gdbus", "monitor", "--session",
                          "--dest", "org.mobian_project.CallAudio"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in self.proc.stdout:
            found = re.search(r"'SpeakerState': <(?:uint32 )?(\d+)>", line)
            if found and int(found.group(1)) in self.PROFILES:
                profile = self.PROFILES[int(found.group(1))]
                subprocess.run(self.user_cmd("pactl", "set-card-profile", CARD, profile),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("SPEAKER_PROFILE", profile)

    def stop(self):
        if self.proc:
            self.proc.terminate()


def number_from_uri(uri):
    """The user part of a sip: or tel: URI, without parameters."""
    uri = uri.strip().strip("<>")
    uri = uri.split(":", 1)[-1]
    return uri.split("@", 1)[0].split(";", 1)[0]


class RtpRelay(threading.Thread):
    """Copy RTP both ways between Calls and the IMS bearer, unchanged."""

    def __init__(self, ims_src, iface):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.calls_peer = None
        self.ims_peer = None
        self.local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.local.bind((LOCAL_IP, RTP_PORT_LOCAL))
        self.remote = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.remote.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.remote.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                               iface.encode() + b"\x00")
        self.remote.bind((ims_src, RTP_PORT_IMS))
        self.up = 0
        self.down = 0

    def run(self):
        socks = [self.local, self.remote]
        while not self.stop_event.is_set():
            ready, _, _ = select.select(socks, [], [], 0.2)
            for sock in ready:
                data, addr = sock.recvfrom(2048)
                if sock is self.local:
                    # calls_peer stays the port from Calls' SDP: it sends from
                    # a different socket than the one it receives on.
                    if self.ims_peer:
                        self.remote.sendto(data, self.ims_peer)
                        self.up += 1
                elif self.calls_peer:
                    self.local.sendto(data, self.calls_peer)
                    self.down += 1
        self.local.close()
        self.remote.close()
        print("RTP_RELAY_DONE", "up", self.up, "down", self.down)

    def stop(self):
        self.stop_event.set()


class Bridge:
    """ims needs: send(bytes), recv(timeout) -> (text, headers), and the
    build_*/response callables, aor, realm, src, port_c, routes, ss, iface."""

    def __init__(self, ims):
        self.ims = ims
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((LOCAL_IP, SIP_PORT))
        self.sock.setblocking(False)
        self.calls_addr = None
        self.calls_user = "volte"
        self.tag = uuid.uuid4().hex[:10]
        self.probe_branch = "z9hG4bK" + uuid.uuid4().hex[:12]
        self.relay = None
        self.call = None
        self.call_audio = False
        self.speaker = None

    # ---- local (GNOME Calls) side -------------------------------------
    def local_response(self, hdrs, code, reason, addr, body="", to_tag=None):
        to = hdr(hdrs, "to")
        if "tag=" not in to.lower():
            to = f"{to};tag={to_tag or self.tag}"
        lines = [f"SIP/2.0 {code} {reason}"]
        lines += [f"Via: {v}" for v in hdrs.get("via", [])]
        lines += [
            f"From: {hdr(hdrs, 'from')}",
            f"To: {to}",
            f"Call-ID: {hdr(hdrs, 'call-id')}",
            f"CSeq: {hdr(hdrs, 'cseq')}",
        ]
        if hdr(hdrs, "cseq").endswith("REGISTER"):
            # A registrar echoes the client's bindings; sofia-sip validates them.
            lines += [f"Contact: {c}" for c in hdrs.get("contact", [])]
            lines.append("Expires: 180")
        else:
            lines.append(f"Contact: <sip:{self.calls_user}@{LOCAL_IP}:{SIP_PORT}>")
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        self.sock.sendto(("\r\n".join(lines) + "\r\n\r\n" + body).encode(), addr)

    def local_request(self, method, call, body=""):
        """A request from us to Calls, in the incoming call's local dialog."""
        # ACK and CANCEL belong to the INVITE transaction's CSeq; CANCEL also
        # has to reuse its branch.
        if method not in ("ACK", "CANCEL"):
            call["cseq"] += 1
        branch = "z9hG4bK" + uuid.uuid4().hex[:12]
        if method == "INVITE":
            call["local_branch"] = branch
        elif method == "CANCEL":
            branch = call["local_branch"]
        to = f"<sip:{self.calls_user}@{self.calls_addr[0]}>"
        if call.get("local_to_tag"):
            to += f";tag={call['local_to_tag']}"
        lines = [
            f"{method} {call['local_ruri']} SIP/2.0",
            f"Via: SIP/2.0/UDP {LOCAL_IP}:{SIP_PORT};branch={branch}",
            "Max-Forwards: 70",
            f"From: <sip:{call['caller']}@{LOCAL_IP}>;tag={self.tag}",
            f"To: {to}",
            f"Call-ID: {call['local_id']}",
            f"CSeq: {call['cseq']} {method}",
            f"Contact: <sip:{call['caller']}@{LOCAL_IP}:{SIP_PORT}>",
        ]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        self.sock.sendto(("\r\n".join(lines) + "\r\n\r\n" + body).encode(),
                         self.calls_addr)

    def on_local(self, text, addr):
        first, hdrs, body = parse_msg(text)
        if not first.strip():
            return  # sofia-sip keepalive
        method = first.split()[0]
        if first.startswith("SIP/2.0"):
            if hdr(hdrs, "cseq").endswith("OPTIONS") and self.probe_branch in hdr(hdrs, "via"):
                self.return_probe(text, addr)
            else:
                self.on_local_response(first, hdrs, body)
        elif method == "REGISTER":
            self.calls_addr = addr
            self.calls_user = number_from_uri(hdr(hdrs, "from").split(">")[0].split("<")[-1])
            self.local_response(hdrs, 200, "OK", addr)
            print("LOCAL_REGISTERED")
        elif method == "OPTIONS" and "REGISTRATION PROBE" in hdr(hdrs, "subject"):
            self.forward_probe(text, addr)
        elif method == "OPTIONS":
            self.local_response(hdrs, 200, "OK", addr)
        elif method == "INVITE":
            self.on_local_invite(first, hdrs, body, addr)
        elif method == "ACK":
            print("LOCAL_ACK")
        elif method == "BYE":
            self.local_response(hdrs, 200, "OK", addr)
            self.hang_up_ims()
        elif method == "CANCEL":
            self.local_response(hdrs, 200, "OK", addr)
            self.cancel_outgoing(addr)
        else:
            self.local_response(hdrs, 200, "OK", addr)

    def forward_probe(self, text, addr):
        """sofia-sip checks its registration by sending itself an OPTIONS
        through the registrar, so route it back like a proxy would."""
        lines = text.split("\r\n")
        lines.insert(1, f"Via: SIP/2.0/UDP {LOCAL_IP}:{SIP_PORT};branch={self.probe_branch}")
        self.sock.sendto("\r\n".join(lines).encode(), addr)

    def return_probe(self, text, addr):
        """Drop our Via from the probe's response and send it on."""
        lines = text.split("\r\n")
        for i, line in enumerate(lines):
            if self.probe_branch in line:
                del lines[i]
                break
        self.sock.sendto("\r\n".join(lines).encode(), addr)

    def on_local_invite(self, first, hdrs, body, addr):
        if self.call:
            self.local_response(hdrs, 486, "Busy Here", addr)
            return
        number = number_from_uri(first.split()[1])
        ip, port, pts = sdp_media(body)
        if 0 not in pts:
            self.local_response(hdrs, 488, "Not Acceptable Here", addr)
            print("LOCAL_NO_PCMU", pts)
            return
        self.local_response(hdrs, 100, "Trying", addr)
        if number.isdigit() and not number.startswith("+"):
            number = "+" + number
        dest = f"sip:{number}@{self.ims.realm}"
        inv, tag, cid = self.ims.build_invite(dest=dest)
        self.call = {
            "dir": "out", "state": "calling", "local_hdrs": hdrs, "local_addr": addr,
            "calls_rtp": (ip, port), "dest": dest, "invite": inv, "tag": tag, "cid": cid,
            "to_tag": "", "rseqs": set(),
        }
        self.ims.send(inv)
        print("OUT_INVITE_SENT")
        # Before Calls opens its audio: the switch replaces the sinks.
        self.call_audio = True
        set_call_audio(True)
        self.speaker = SpeakerFollower()
        self.speaker.start()

    def on_local_response(self, first, hdrs, body):
        call = self.call
        if not call or call["dir"] != "in":
            return
        code = int(first.split()[1])
        to = hdr(hdrs, "to")
        if "tag=" in to:
            call["local_to_tag"] = to.rsplit("tag=", 1)[1].split(";")[0].strip()
        if hdr(hdrs, "contact"):
            call["local_ruri"] = hdr(hdrs, "contact").split("<")[-1].split(">")[0].split(";")[0]
        if code == 180:
            print("LOCAL_RINGING")
        elif code == 200 and "INVITE" in hdr(hdrs, "cseq") and call["state"] == "ringing":
            self.local_request("ACK", call)
            ip, port, _ = sdp_media(body)
            self.start_relay((ip, port), call["ims_rtp"])
            self.ims.send(self.ims.response(call["invite"], 200, "OK", call["our_tag"],
                                            body=self.ims.sdp()))
            call["state"] = "up"
            print("IN_ANSWERED")
        elif code >= 300:
            self.local_request("ACK", call)
            self.ims.send(self.ims.response(call["invite"], code, first.split(" ", 2)[-1],
                                            call["our_tag"]))
            print("IN_REJECTED", code)
            self.end_call()

    # ---- IMS side -----------------------------------------------------
    def on_ims(self, text, hdrs):
        first = text.splitlines()[0] if text else ""
        if first.startswith("SIP/2.0"):
            self.on_ims_response(first, text, hdrs)
            return
        method = first.split()[0] if first else ""
        call = self.call
        if method == "INVITE":
            self.on_ims_invite(text, hdrs)
        elif method == "BYE":
            self.ims.send(self.ims.response(text, 200, "OK", self.tag))
            print("IMS_BYE")
            self.hang_up_local()
        elif method == "CANCEL" and call and call["dir"] == "in":
            self.ims.send(self.ims.response(text, 200, "OK", self.tag))
            self.ims.send(self.ims.response(call["invite"], 487, "Request Terminated",
                                            call["our_tag"]))
            self.local_request("CANCEL", call)
            print("IMS_CANCEL")
            self.end_call()
        elif method == "ACK":
            print("IMS_ACK")
        elif method:
            self.ims.send(self.ims.response(text, 200, "OK", self.tag))

    def on_ims_response(self, first, text, hdrs):
        call = self.call
        if not call or call["dir"] != "out":
            return
        code = int(first.split()[1])
        method = hdr(hdrs, "cseq").split()[-1] if hdr(hdrs, "cseq") else ""
        if method != "INVITE":
            print("IMS_RSP", method, code)
            return
        print("OUT_RSP", code)
        to = hdr(hdrs, "to")
        if "tag=" in to:
            call["to_tag"] = to.rsplit("tag=", 1)[1].split(";")[0].strip()
        if call["state"] == "cancelling" and 100 < code < 200:
            self.ims.send(self.ims.build_cancel(call["invite"]))
            call["state"] = "cancelled"
            return
        rseq = hdr(hdrs, "rseq")
        if rseq and rseq not in call["rseqs"] and "100rel" in hdr(hdrs, "require").lower():
            call["rseqs"].add(rseq)
            self.ims.send(self.ims.build_prack(dest=call["dest"], from_tag=call["tag"],
                                               to_tag=call["to_tag"], call_id=call["cid"],
                                               rseq=rseq))
        if code in (180, 183):
            # Not forwarded: Calls 50 crashes in calls_media_playback_stop_calling()
            # when a call it marked as alerting is answered.
            print("OUT_RINGING")
        elif code == 200:
            self.ims.send(self.ims.build_ack(dest=call["dest"], from_tag=call["tag"],
                                             to_tag=call["to_tag"], call_id=call["cid"]))
            if call["state"] == "cancelled":
                # Answered as the CANCEL crossed it: Calls has already gone.
                self.ims.send(self.ims.build_bye(dest=call["dest"], from_tag=call["tag"],
                                                 to_tag=call["to_tag"], call_id=call["cid"]))
                self.end_call()
                return
            body = text.partition("\r\n\r\n")[2]
            ip, port, pts = sdp_media(body)
            if 0 not in pts:
                print("OUT_NOT_PCMU", pts)
                self.ims.send(self.ims.build_bye(dest=call["dest"], from_tag=call["tag"],
                                                 to_tag=call["to_tag"], call_id=call["cid"]))
                self.local_response(call["local_hdrs"], 488, "Not Acceptable Here",
                                    call["local_addr"])
                self.end_call()
                return
            self.start_relay(call["calls_rtp"], (ip, port))
            self.local_response(call["local_hdrs"], 200, "OK", call["local_addr"],
                                body=local_sdp())
            call["state"] = "up"
            print("OUT_ANSWERED")
        elif code >= 300:
            self.ims.send(self.ims.build_ack(dest=call["dest"], from_tag=call["tag"],
                                             to_tag=call["to_tag"], call_id=call["cid"]))
            if call["state"] != "cancelled":
                self.local_response(call["local_hdrs"], code, first.split(" ", 2)[-1],
                                    call["local_addr"])
            self.end_call()

    def on_ims_invite(self, text, hdrs):
        tag = uuid.uuid4().hex[:10]
        if self.call or not self.calls_addr:
            reason = "Busy Here" if self.call else "Temporarily Unavailable"
            self.ims.send(self.ims.response(text, 486 if self.call else 480, reason, tag))
            print("IN_DECLINED", reason)
            return
        body = text.partition("\r\n\r\n")[2]
        ip, port, pts = sdp_media(body)
        if 0 not in pts:
            self.ims.send(self.ims.response(text, 488, "Not Acceptable Here", tag))
            print("IN_NOT_PCMU", pts)
            return
        self.ims.send(self.ims.response(text, 100, "Trying", tag))
        caller = number_from_uri(hdr(hdrs, "p-asserted-identity") or hdr(hdrs, "from"))
        self.call = {
            "dir": "in", "state": "ringing", "invite": text, "our_tag": tag,
            "ims_rtp": (ip, port), "caller": caller, "cseq": 0,
            "local_id": str(uuid.uuid4()), "local_to_tag": "",
            "local_ruri": f"sip:{self.calls_user}@{self.calls_addr[0]}:{self.calls_addr[1]}",
            "contact": hdr(hdrs, "contact").split("<")[-1].split(">")[0],
            "routes": hdrs.get("record-route", []),
            "remote_tag": hdr(hdrs, "from").rsplit("tag=", 1)[-1].split(";")[0].strip(),
            "cid": hdr(hdrs, "call-id"),
        }
        self.local_request("INVITE", self.call, body=local_sdp())
        self.ims.send(self.ims.response(text, 180, "Ringing", tag))
        print("IN_RINGING")

    # ---- shared ------------------------------------------------------
    def start_relay(self, calls_rtp, ims_rtp):
        self.relay = RtpRelay(self.ims.src, self.ims.iface)
        self.relay.calls_peer = (LOCAL_IP, calls_rtp[1])
        self.relay.ims_peer = (ims_rtp[0], ims_rtp[1])
        self.relay.start()
        print("RTP_RELAY_UP")

    def end_call(self):
        if self.relay:
            self.relay.stop()
            self.relay.join(timeout=1)  # frees the RTP ports for the next call
            self.relay = None
        if self.call_audio:
            self.call_audio = False
            set_call_audio(False)
        if self.speaker:
            self.speaker.stop()
            self.speaker = None
        self.call = None
        print("CALL_ENDED")

    def hang_up_ims(self):
        call = self.call
        if not call:
            return
        if call["dir"] == "out":
            self.ims.send(self.ims.build_bye(dest=call["dest"], from_tag=call["tag"],
                                             to_tag=call["to_tag"], call_id=call["cid"]))
        else:
            self.ims.send(self.ims.build_bye(dest=call["contact"], from_tag=call["our_tag"],
                                             to_tag=call["remote_tag"], call_id=call["cid"],
                                             routes=call["routes"]))
        self.end_call()

    def hang_up_local(self):
        call = self.call
        if not call:
            return
        if call["dir"] == "in":
            self.local_request("BYE", call)
        else:
            hdrs = call["local_hdrs"]
            branch = "z9hG4bK" + uuid.uuid4().hex[:12]
            frm = hdr(hdrs, "from")
            contact = hdr(hdrs, "contact").split("<")[-1].split(">")[0]
            bye = "\r\n".join([
                f"BYE {contact} SIP/2.0",
                f"Via: SIP/2.0/UDP {LOCAL_IP}:{SIP_PORT};branch={branch}",
                "Max-Forwards: 70",
                f"From: {hdr(hdrs, 'to')};tag={self.tag}",
                f"To: {frm}",
                f"Call-ID: {hdr(hdrs, 'call-id')}",
                "CSeq: 2 BYE",
                "Content-Length: 0",
                "", "",
            ])
            self.sock.sendto(bye.encode(), call["local_addr"])
        self.end_call()

    def cancel_outgoing(self, addr):
        call = self.call
        if not call or call["dir"] != "out":
            return
        self.local_response(call["local_hdrs"], 487, "Request Terminated", addr)
        self.ims.send(self.ims.build_cancel(call["invite"]))
        call["state"] = "cancelled"
        print("OUT_CANCEL")

    def guarded(self, handler, *args):
        """One bad message must not take the registration down with it."""
        try:
            handler(*args)
        except Exception as e:
            print("BRIDGE_ERROR", handler.__name__, type(e).__name__, e)
            self.end_call()

    def run(self):
        print("BRIDGE_LISTENING", SIP_PORT)
        while True:
            try:
                while True:
                    data, addr = self.sock.recvfrom(8192)
                    self.guarded(self.on_local, data.decode("utf-8", "replace"), addr)
            except BlockingIOError:
                pass
            try:
                text, hdrs = self.ims.recv(0.2)
            except socket.timeout:
                continue
            except ValueError:
                print("BRIDGE_ESP_SKIP")
                continue
            self.guarded(self.on_ims, text, hdrs)
