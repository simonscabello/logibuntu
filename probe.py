"""Print every HID++ device/feature reachable (diagnostics)."""
from logibuntu.hidpp import Device, HidppError, hidraw_nodes, speaks_hidpp

for path, pid, name, desc in hidraw_nodes():
    if not speaks_hidpp(desc):
        continue
    for index in ([0xFF] + list(range(1, 7))):
        try:
            d = Device(path, index)
            feats = d.list_features()
            print(f"{path} {pid:04x} {name} idx={index:#x} -> {d.name()!r}")
            print("   ", " ".join(f"{i}:{f:04x}" for i, f in feats))
            d.close()
        except (HidppError, OSError) as e:
            if index == 0xFF or "1.0" not in str(e):
                print(f"{path} {name} idx={index:#x}: {e}")
