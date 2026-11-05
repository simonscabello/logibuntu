"""Minimal HID++ 2.0 over Linux hidraw (no external deps)."""
import glob
import os
import select
import time

SHORT, LONG = 0x10, 0x11
SWID = 0x0A
LOGITECH = "0000046D"


class HidppError(Exception):
    pass


def hidraw_nodes():
    """Yield (path, product_id, name, report_descriptor) for Logitech hidraw nodes."""
    for sysdir in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        info = dict(l.split("=", 1) for l in open(f"{sysdir}/device/uevent").read().splitlines() if "=" in l)
        hid_id = info.get("HID_ID", "").split(":")
        if len(hid_id) != 3 or hid_id[1].upper() != LOGITECH:
            continue
        try:
            desc = open(f"{sysdir}/device/report_descriptor", "rb").read()
        except OSError:
            desc = b""
        yield "/dev/" + os.path.basename(sysdir), int(hid_id[2], 16), info.get("HID_NAME", ""), desc


def speaks_hidpp(desc):
    # Vendor usage page 0xFF00 with long report ID 0x11 marks the HID++ interface.
    return b"\x06\x00\xff" in desc and b"\x85\x11" in desc


class Device:
    def __init__(self, path, index=0xFF):
        self.path, self.index = path, index
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._features = {}
        self._swid = SWID

    def close(self):
        os.close(self.fd)

    def _drain(self):
        try:
            while os.read(self.fd, 64):
                pass
        except BlockingIOError:
            pass

    def request(self, feat_idx, func, params=b"", timeout=1.0):
        # Rotating SWID + draining stale reports keeps a late reply from matching a newer request.
        self._swid = self._swid % 15 + 1
        tag = (func << 4) | self._swid
        self._drain()
        os.write(self.fd, (bytes([LONG, self.index, feat_idx, tag]) + bytes(params)).ljust(20, b"\0"))
        deadline = time.monotonic() + timeout
        while True:
            # Deadline, not per-read timeout: mouse input reports keep the fd readable.
            left = deadline - time.monotonic()
            r = select.select([self.fd], [], [], left)[0] if left > 0 else []
            if not r:
                raise HidppError(f"timeout feat={feat_idx} func={func}")
            data = os.read(self.fd, 64)
            if len(data) < 6 or data[1] != self.index:
                continue
            if data[2] == 0xFF and data[3] == feat_idx and data[4] == tag:
                raise HidppError(f"HID++ error {data[5]} feat={feat_idx} func={func}")
            if data[2] == 0x8F and data[3] == feat_idx and data[4] == tag:
                raise HidppError(f"HID++1.0 error {data[5]} (device offline?)")
            if data[2] == feat_idx and data[3] == tag:
                return data[4:]

    def feature(self, feature_id):
        """IRoot.getFeature -> feature index, or None if unsupported."""
        if feature_id not in self._features:
            idx = self.request(0, 0, feature_id.to_bytes(2, "big"))[0]
            self._features[feature_id] = idx or None
        return self._features[feature_id]

    def call(self, feature_id, func, params=b""):
        idx = self.feature(feature_id)
        if idx is None:
            raise HidppError(f"feature {feature_id:#06x} not supported")
        return self.request(idx, func, params)

    def list_features(self):
        count = self.call(0x0001, 0)[0]
        out = []
        for i in range(1, count + 1):
            r = self.call(0x0001, 1, bytes([i]))
            out.append((i, int.from_bytes(r[:2], "big")))
        return out

    def name(self):
        n = self.call(0x0005, 0)[0]
        s = b""
        while len(s) < n:
            s += self.call(0x0005, 1, bytes([len(s)]))
        return s[:n].decode(errors="replace")
