"""Logitech gaming devices on top of HID++ 2.0 (tested: G305 mouse, G533 headset)."""
from .hidpp import Device, HidppError, hidraw_nodes, speaks_hidpp

BATTERY, DPI, REPORT_RATE, ONBOARD = 0x1000, 0x2201, 0x8060, 0x8100
HS_BATTERY, SIDETONE, EQUALIZER = 0x1F20, 0x8300, 0x8310

ROM_PROFILE, USER_PROFILE, DIRECTORY, MACRO_SECTOR = 0x0101, 0x0001, 0x0000, 0x0002
LED = 0x8070
LED_OFF, LED_FIXED, LED_CYCLE, LED_BREATHING = 0x0000, 0x0001, 0x0003, 0x000A
LED_NAMES = {LED_OFF: "Desligado", LED_FIXED: "Cor fixa", LED_CYCLE: "Ciclo de cores", LED_BREATHING: "Respiração"}
MACRO = "Macro: digitar texto"

# Onboard-profile button actions: name -> 4 raw bytes (Logitech profile format 1-5).
ACTIONS = {
    "Botão esquerdo": b"\x80\x01\x00\x01",
    "Botão direito": b"\x80\x01\x00\x02",
    "Botão do meio": b"\x80\x01\x00\x04",
    "Voltar": b"\x80\x01\x00\x08",
    "Avançar": b"\x80\x01\x00\x10",
    "DPI: ciclar": b"\x90\x05\x00\x00",
    "DPI: próximo": b"\x90\x03\x00\x00",
    "DPI: anterior": b"\x90\x04\x00\x00",
    "DPI: shift (segurar)": b"\x90\x07\x00\x00",
    "Rolar para cima": b"\x90\x11\x00\x00",
    "Rolar para baixo": b"\x90\x10\x00\x00",
    "Play/Pause": b"\x80\x03\x00\xcd",
    "Próxima faixa": b"\x80\x03\x00\xb5",
    "Faixa anterior": b"\x80\x03\x00\xb6",
    "Volume +": b"\x80\x03\x00\xe9",
    "Volume −": b"\x80\x03\x00\xea",
    "Mudo": b"\x80\x03\x00\xe2",
    "Ctrl+C (copiar)": b"\x80\x02\x01\x06",
    "Ctrl+V (colar)": b"\x80\x02\x01\x19",
    "Ctrl+Z (desfazer)": b"\x80\x02\x01\x1d",
    "Enter": b"\x80\x02\x00\x28",
    "Esc": b"\x80\x02\x00\x29",
    "Desativado": b"\x80\x01\x00\x00",
}
BUTTON_NAMES = ["Esquerdo", "Direito", "Meio", "Lateral trás", "Lateral frente", "DPI (topo)"]


def crc16(data):
    """CRC-CCITT (0x1021, init 0xFFFF), used by onboard-profile sectors."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def is_macro(raw):
    return raw[0] >> 4 == 0 and raw != b"\0\0\0\0"


# shortcut: physical-key (US) usages for letters, digits, space and Enter only; symbols differ per layout.
_KEYS = {**{c: 0x04 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
         **{c: 0x1E + i for i, c in enumerate("1234567890")}, " ": 0x2C, "\n": 0x28}
_CHARS = {v: k for k, v in _KEYS.items()}
SHIFT = 0x02
DELAY = bytes([0x40, 0, 20])  # libratbag HIDPP20_MACRO_DELAY, ms BE16


def encode_macro(text):
    """Text -> onboard macro ops (key press 0x43 / release 0x44 with modifier, delay 0x40, end 0xFF)."""
    ops = b""
    for ch in text:
        key = _KEYS.get(ch.lower())
        if key is None:
            raise ValueError(f"caractere não suportado em macro: {ch!r}")
        mod = SHIFT if ch.isupper() else 0
        # Delay op between press/release: without it the receiver coalesces them and the key is lost.
        ops += bytes([0x43, mod, key]) + DELAY + bytes([0x44, mod, key]) + DELAY
    return ops + b"\xff"


def decode_macro(data):
    """Inverse of encode_macro; None if the macro uses ops this app does not write."""
    text, i = "", 0
    while i < len(data) and data[i] != 0xFF:
        op, mod, key = data[i:i + 3]
        if op == 0x43 and key in _CHARS:
            text += _CHARS[key].upper() if mod & SHIFT else _CHARS[key]
        elif op not in (0x44, 0x40):
            return None
        i += 3
    return text


def action_name(raw):
    if is_macro(raw):
        return MACRO
    return next((n for n, v in ACTIONS.items() if v == bytes(raw)), f"Personalizado ({bytes(raw).hex()})")


class Mouse:
    kind = "mouse"

    def __init__(self, dev, name):
        self.dev, self.name = dev, name
        info = dev.call(ONBOARD, 0)
        self.buttons = info[5]
        self.sector_size = int.from_bytes(info[7:9], "big")

    def battery(self):
        r = self.dev.call(BATTERY, 0)
        return r[0], r[2] in (1, 2, 3)  # status 1-3: charging/almost full/full

    def dpi_range(self):
        r = self.dev.call(DPI, 1, b"\0")[1:]
        vals = [int.from_bytes(r[i:i + 2], "big") for i in range(0, len(r) - 1, 2)]
        vals = vals[:vals.index(0)] if 0 in vals else vals
        step = next((v & 0x1FFF for v in vals if v >> 13 == 7), 50)  # 0xE000|step marks a range
        plain = [v for v in vals if v >> 13 != 7]
        return min(plain), max(plain), step

    def report_rates(self):
        mask = self.dev.call(REPORT_RATE, 0)[0]
        return [i + 1 for i in range(8) if mask >> i & 1]  # periods in ms

    # --- onboard memory ---
    def _read_sector(self, sector):
        out = b""
        for off in list(range(0, self.sector_size - 16, 16)) + [self.sector_size - 16]:
            out = out[:off] + self.dev.call(ONBOARD, 5, sector.to_bytes(2, "big") + off.to_bytes(2, "big"))[:16]
        return bytearray(out[:self.sector_size])

    def _write_sector(self, sector, data, strict_end=True):
        assert len(data) == self.sector_size
        data = bytes(data[:-2]) + crc16(data[:-2]).to_bytes(2, "big")
        self.dev.call(ONBOARD, 6, sector.to_bytes(2, "big") + b"\0\0" + len(data).to_bytes(2, "big"))
        for i in range(0, len(data), 16):
            self.dev.call(ONBOARD, 7, data[i:i + 16])
        try:
            self.dev.call(ONBOARD, 8)
        except HidppError:
            if strict_end:
                raise
        if self._read_sector(sector)[:-2] != data[:-2]:
            raise HidppError(f"verificação falhou no setor {sector:#06x}")

    def read_profile(self):
        """Current profile as an editable bytearray (user sector if active, else ROM default)."""
        current = int.from_bytes(self.dev.call(ONBOARD, 4)[:2], "big")
        try:
            p = self._read_sector(current)
            if current == ROM_PROFILE or crc16(p[:-2]) == int.from_bytes(p[-2:], "big"):  # ROM has no CRC
                return p
        except HidppError:
            pass
        return self._read_sector(ROM_PROFILE)

    def read_macros(self, profile):
        """{button index: text} for buttons whose macro lives in our macro sector."""
        out, sector = {}, None
        for i in range(self.buttons):
            raw = profile_button(profile, i)
            if is_macro(raw) and ((raw[0] & 0x0F) << 8 | raw[1]) == MACRO_SECTOR:
                sector = sector or self._read_sector(MACRO_SECTOR)
                text = decode_macro(sector[int.from_bytes(raw[2:4], "big"):])
                if text is not None:
                    out[i] = text
        return out

    def write_profile(self, profile, macros=None):
        """macros: {button index: text}; those buttons are pointed at the macro sector."""
        if macros:
            sector, offsets = b"", {}
            for i, text in macros.items():
                offsets[i] = len(sector)
                sector += encode_macro(text)
            if len(sector) > self.sector_size - 2:
                raise ValueError("macros longas demais para a memória do mouse")
            self._write_sector(MACRO_SECTOR, bytearray(sector.ljust(self.sector_size, b"\xff")))
            for i, off in offsets.items():
                profile[32 + 4 * i:36 + 4 * i] = MACRO_SECTOR.to_bytes(2, "big") + off.to_bytes(2, "big")
        self._write_sector(USER_PROFILE, profile)
        directory = bytearray(b"\xff" * self.sector_size)
        directory[:4] = USER_PROFILE.to_bytes(2, "big") + b"\x01\x00"
        self._write_sector(DIRECTORY, directory)
        self._activate(USER_PROFILE, profile[1])

    # --- lighting (0x8070 color LED effects, zone 0; the profile's LED slots are ignored by the G305) ---
    def led_effects(self):
        """[(index, effect id, default period)] for zone 0, in device order."""
        out = []
        for i in range(self.dev.call(LED, 1, b"\0")[3]):
            r = self.dev.call(LED, 2, bytes([0, i]))
            out.append((i, int.from_bytes(r[2:4], "big"), r[6:8]))
        return out

    def led_get(self):
        """(effect id, (r, g, b))."""
        r = self.dev.call(LED, 9, b"\0")
        effect = next((e for i, e, _ in self.led_effects() if i == r[1]), 0)
        return effect, tuple(r[2:5])

    def led_set(self, effect_id, rgb=(255, 255, 255)):
        idx, _e, period = next(x for x in self.led_effects() if x[1] == effect_id)
        r, g, b = rgb
        params = {
            LED_FIXED: [r, g, b, 0],
            LED_CYCLE: [0, 0, 0, 0, 0, *period, 100],
            LED_BREATHING: [r, g, b, *period, 0, 100],
        }.get(effect_id, [])
        # last byte 1 = persist across power cycles
        self.dev.call(LED, 3, bytes([0, idx, *bytes(params).ljust(10, b"\0"), 1]))

    def restore_defaults(self):
        # G305 rejects the write-end when the active profile disappears, yet applies it; readback verifies.
        self._write_sector(DIRECTORY, bytearray(b"\xff" * self.sector_size), strict_end=False)
        self._activate(ROM_PROFILE, self._read_sector(ROM_PROFILE)[1])

    def _activate(self, sector, dpi_index):
        self.dev.call(ONBOARD, 1, b"\x01")  # onboard mode: settings live in the mouse, no daemon needed
        self.dev.call(ONBOARD, 3, sector.to_bytes(2, "big"))
        self.dev.call(ONBOARD, 0xC, bytes([dpi_index]))  # profile default DPI only applies at power-on


# Profile field helpers (offsets from the Logitech onboard profile layout).
def profile_rate(p): return p[0]
def profile_dpis(p): return [int.from_bytes(p[3 + 2 * i:5 + 2 * i], "little") for i in range(5)]
def profile_default_dpi(p): return p[1]
def profile_button(p, i): return bytes(p[32 + 4 * i:36 + 4 * i])


def set_profile(p, rate, dpis, default_idx, buttons):
    dpis = [d for d in dpis if d] [:5]
    assert dpis and 0 <= default_idx < len(dpis)
    p[0], p[1], p[2] = rate, default_idx, 0
    p[3:13] = b"".join(d.to_bytes(2, "little") for d in dpis + [0] * (5 - len(dpis)))
    for i, raw in enumerate(buttons):
        p[32 + 4 * i:36 + 4 * i] = raw
    return p


class Headset:
    kind = "headset"

    def __init__(self, dev, name):
        self.dev, self.name = dev, name

    def battery(self):
        r = self.dev.call(HS_BATTERY, 0)
        mv, state = int.from_bytes(r[:2], "big"), r[2]
        return voltage_to_percent(mv), state == 3, mv

    def sidetone(self):
        return self.dev.call(SIDETONE, 0)[0]

    def set_sidetone(self, level):
        self.dev.call(SIDETONE, 1, bytes([max(0, min(100, level))]))

    def eq_info(self):
        bands, db_range = self.dev.call(EQUALIZER, 0)[:2]
        freqs = []
        while len(freqs) < bands:
            r = self.dev.call(EQUALIZER, 1, bytes([len(freqs)]))[1:]
            freqs += [int.from_bytes(r[i:i + 2], "big") for i in range(0, 14, 2)]
        return freqs[:bands], db_range


# shortcut: generic Li-ion curve, not Logitech's calibrated G533 table; refine if readings look off.
_CURVE = [(4150, 100), (4000, 85), (3900, 70), (3800, 55), (3700, 35), (3600, 15), (3500, 5), (3400, 0)]


def voltage_to_percent(mv):
    if mv >= _CURVE[0][0]:
        return 100
    for (v1, p1), (v2, p2) in zip(_CURVE, _CURVE[1:]):
        if mv >= v2:
            return round(p2 + (p1 - p2) * (mv - v2) / (v1 - v2))
    return 0


def discover():
    """Return Mouse/Headset objects for every reachable Logitech HID++ 2.0 device."""
    found, seen = [], set()
    for path, _pid, hid_name, desc in hidraw_nodes():
        if not speaks_hidpp(desc):
            continue
        for index in (range(1, 7) if "Receiver" in hid_name else [0xFF]):
            try:
                dev = Device(path, index)
            except OSError:
                break
            try:
                name = dev.name()
                if name in seen:
                    dev.close()
                    continue
                if dev.feature(ONBOARD) and dev.feature(DPI):
                    found.append(Mouse(dev, name))
                elif dev.feature(HS_BATTERY) or dev.feature(SIDETONE):
                    found.append(Headset(dev, name))
                else:
                    dev.close()
                    continue
                seen.add(name)
            except (HidppError, OSError):
                dev.close()
    return found
