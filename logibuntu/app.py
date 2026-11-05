"""GTK4/libadwaita UI for Logitech G mice and headsets."""
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import devices as dv  # noqa: E402
from . import softeq  # noqa: E402
from .hidpp import HidppError  # noqa: E402


def battery_text(percent, charging, extra=""):
    return f"{percent}%{' · carregando' if charging else ''}{extra}"


class MousePage(Adw.PreferencesPage):
    def __init__(self, mouse, toast):
        super().__init__(title=mouse.name, icon_name="input-mouse-symbolic")
        self.m, self.toast = mouse, toast
        lo, hi, step = mouse.dpi_range()
        self.dpi_min = lo
        self.rates = mouse.report_rates()
        p = mouse.read_profile()

        status = Adw.PreferencesGroup(title="Status")
        self.battery = Adw.ActionRow(title="Bateria")
        status.add(self.battery)
        self.add(status)

        sens = Adw.PreferencesGroup(title="Sensibilidade", description=f"DPI de {lo} a {hi}, passo {step}. 0 = nível desligado.")
        self.dpi_rows = []
        for i, val in enumerate(dv.profile_dpis(p)):
            row = Adw.SpinRow.new_with_range(0, hi, step)
            row.set_title(f"Nível {i + 1}")
            row.set_value(val)
            sens.add(row)
            self.dpi_rows.append(row)
        self.default_dpi = Adw.ComboRow(title="Nível inicial", model=Gtk.StringList.new([f"Nível {i + 1}" for i in range(5)]))
        self.default_dpi.set_selected(dv.profile_default_dpi(p))
        sens.add(self.default_dpi)
        self.rate = Adw.ComboRow(title="Taxa de polling", model=Gtk.StringList.new([f"{1000 // ms} Hz" for ms in self.rates]))
        if dv.profile_rate(p) in self.rates:
            self.rate.set_selected(self.rates.index(dv.profile_rate(p)))
        sens.add(self.rate)
        self.add(sens)

        if mouse.dev.feature(dv.LED):
            self.add(self.lighting_group())

        btns = Adw.PreferencesGroup(title="Botões", description="Macro: letras, números, espaço e Enter (\\n).")
        self.button_rows, self.macro_rows = [], []
        macros = mouse.read_macros(p)
        for i in range(mouse.buttons):
            raw = dv.profile_button(p, i)
            names = list(dv.ACTIONS) + [dv.MACRO]
            if dv.action_name(raw) not in names:
                names.append(dv.action_name(raw))  # keep unknown mappings selectable as-is
            title = dv.BUTTON_NAMES[i] if i < len(dv.BUTTON_NAMES) else f"Botão {i + 1}"
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(names))
            row.set_selected(names.index(dv.action_name(raw)))
            row.raw = {n: dv.ACTIONS.get(n, raw) for n in names}
            macro = Adw.EntryRow(title=f"Texto da macro · {title}", text=macros.get(i, "").replace("\n", "\\n"))
            macro.set_visible(dv.action_name(raw) == dv.MACRO)
            row.connect("notify::selected", lambda r, _p, e=macro: e.set_visible(self.selected(r) == dv.MACRO))
            btns.add(row)
            btns.add(macro)
            self.button_rows.append(row)
            self.macro_rows.append(macro)
        self.add(btns)

        actions = Adw.PreferencesGroup()
        box = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        reset = Gtk.Button(label="Restaurar padrão de fábrica")
        reset.add_css_class("destructive-action")
        reset.connect("clicked", self.on_reset)
        apply = Gtk.Button(label="Salvar no mouse")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", self.on_apply)
        box.append(reset)
        box.append(apply)
        actions.add(box)
        self.add(actions)
        self.profile = p
        self.refresh()

    @staticmethod
    def selected(row):
        return row.get_model().get_string(row.get_selected())

    def lighting_group(self):
        group = Adw.PreferencesGroup(title="Iluminação", description="Aplicada na hora e mantida ao desligar.")
        self.effects = [e for _i, e, _p in self.m.led_effects() if e in dv.LED_NAMES]
        effect, rgb = self.m.led_get()
        self.effect = Adw.ComboRow(title="Efeito", model=Gtk.StringList.new([dv.LED_NAMES[e] for e in self.effects]))
        if effect in self.effects:
            self.effect.set_selected(self.effects.index(effect))
        color = Gdk.RGBA()
        color.red, color.green, color.blue, color.alpha = (*(c / 255 for c in rgb), 1)
        self.color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=color, valign=Gtk.Align.CENTER)
        color_row = Adw.ActionRow(title="Cor")
        color_row.add_suffix(self.color)
        group.add(self.effect)
        group.add(color_row)
        self.effect.connect("notify::selected", self.on_led)
        self.color.connect("notify::rgba", self.on_led)
        return group

    def on_led(self, *_args):
        c = self.color.get_rgba()
        try:
            self.m.led_set(self.effects[self.effect.get_selected()], tuple(round(x * 255) for x in (c.red, c.green, c.blue)))
        except (HidppError, OSError) as e:
            self.toast(f"Falha na iluminação: {e}")

    def refresh(self):
        try:
            self.battery.set_subtitle(battery_text(*self.m.battery()))
        except (HidppError, OSError):
            self.battery.set_subtitle("indisponível (mouse dormindo?)")
        return True

    def on_apply(self, _btn):
        dpis = [v and max(self.dpi_min, v) for v in (int(r.get_value()) for r in self.dpi_rows)]
        default = self.default_dpi.get_selected()
        if not dpis[default]:
            return self.toast("O nível inicial não pode estar em 0.")
        # Levels set to 0 are dropped; remap the default index onto the compacted list.
        default -= sum(1 for d in dpis[:default] if not d)
        buttons = [r.raw[self.selected(r)] for r in self.button_rows]
        macros = {i: e.get_text().replace("\\n", "\n") for i, (r, e) in enumerate(zip(self.button_rows, self.macro_rows))
                  if self.selected(r) == dv.MACRO}
        if any(not t for t in macros.values()):
            return self.toast("Escreva o texto da macro.")
        try:
            profile = dv.set_profile(self.profile, self.rates[self.rate.get_selected()], dpis, default, buttons)
            self.m.write_profile(profile, macros)
            self.toast("Perfil salvo na memória do mouse.")
        except (HidppError, OSError, ValueError) as e:
            self.toast(f"Falha ao salvar: {e}")

    def on_reset(self, _btn):
        try:
            self.m.restore_defaults()
            self.toast("Padrão de fábrica restaurado. Reabra o app para ver os valores.")
        except (HidppError, OSError) as e:
            self.toast(f"Falha ao restaurar: {e}")


class HeadsetPage(Adw.PreferencesPage):
    def __init__(self, headset, toast):
        super().__init__(title=headset.name, icon_name="audio-headset-symbolic")
        self.h, self.toast = headset, toast

        status = Adw.PreferencesGroup(title="Status")
        self.battery = Adw.ActionRow(title="Bateria")
        status.add(self.battery)
        self.add(status)

        audio = Adw.PreferencesGroup(title="Áudio")
        if headset.dev.feature(dv.SIDETONE):
            side = Adw.SpinRow.new_with_range(0, 100, 5)
            side.set_title("Sidetone (retorno do microfone)")
            side.set_value(headset.sidetone())
            side.connect("notify::value", lambda r, _p: self.safe(headset.set_sidetone, int(r.get_value())))
            audio.add(side)
        self.add(audio)

        if headset.dev.feature(dv.EQUALIZER):
            # Band layout comes from the headset, but audio is equalized by PipeWire (the G533 ignores its own EQ).
            freqs, rng = headset.eq_info()
            self.eq_args = (headset.name.split()[0], freqs)
            gains = softeq.saved_gains() or [0] * len(freqs)
            eq = Adw.PreferencesGroup(title="Equalizador", description=f"±{rng} dB, aplicado pelo PipeWire.")
            self.eq_switch = Adw.SwitchRow(title="Ativar equalizador",
                                           subtitle="Cria a saída de som “(Equalizador)” e a deixa como padrão.",
                                           active=softeq.enabled())
            self.eq_switch.connect("notify::active", self.on_eq_switch)
            eq.add(self.eq_switch)
            self._eq_pending = None
            bands = Gtk.Box(spacing=6, homogeneous=True, margin_top=12, margin_bottom=12)
            self.scales = []
            for f, g in zip(freqs, gains):
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                s = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -rng, rng, 1)
                s.set_inverted(True)
                s.set_value(g)
                s.set_size_request(-1, 180)
                s.set_draw_value(True)
                s.connect("value-changed", self.on_eq)
                col.append(s)
                col.append(Gtk.Label(label=f"{f // 1000}k" if f >= 1000 else str(f)))
                bands.append(col)
                self.scales.append(s)
            eq.add(bands)
            flat = Gtk.Button(label="Zerar equalizador", halign=Gtk.Align.END)
            flat.connect("clicked", lambda _b: [s.set_value(0) for s in self.scales])
            eq.add(flat)
            self.add(eq)
        self.refresh()

    def safe(self, fn, *args):
        try:
            fn(*args)
        except (HidppError, OSError) as e:
            self.toast(f"Falha: {e}")

    def eq_call(self, fn):
        label, freqs = self.eq_args
        target = softeq.target_sink(label)
        if target is None:
            raise RuntimeError(f"saída de som do {label} não encontrada")
        return fn(target, label, freqs, [int(s.get_value()) for s in self.scales])

    def on_eq_switch(self, row, _p):
        try:
            self.eq_call(softeq.enable) if row.get_active() else softeq.disable()
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            self.toast(f"Falha no equalizador: {e}")

    def on_eq(self, _scale):
        # Debounce slider drags: each update spawns pw-cli.
        if self._eq_pending:
            GLib.source_remove(self._eq_pending)
        self._eq_pending = GLib.timeout_add(150, self.flush_eq)

    def flush_eq(self):
        self._eq_pending = None
        if self.eq_switch.get_active():
            try:
                self.eq_call(softeq.set_gains)
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                self.toast(f"Falha no equalizador: {e}")
        return False

    def refresh(self):
        try:
            pct, charging, mv = self.h.battery()
            self.battery.set_subtitle(battery_text(pct, charging, f" · {mv} mV"))
        except (HidppError, OSError):
            self.battery.set_subtitle("indisponível (headset desligado?)")
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.logibuntu")

    def do_activate(self):
        Gtk.Window.set_default_icon_name("io.github.logibuntu")
        win = Adw.ApplicationWindow(application=self, title="Logibuntu", default_width=560, default_height=760)
        overlay = Adw.ToastOverlay()
        toast = lambda msg: overlay.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg)))  # noqa: E731
        stack = Adw.ViewStack()
        pages, offline = [], {}
        icons = {"mouse": "input-mouse-symbolic", "headset": "audio-headset-symbolic"}

        def build(d):
            """Add the device page; while it does not answer (headset off, mouse asleep), show a placeholder."""
            try:
                page = (MousePage if d.kind == "mouse" else HeadsetPage)(d, toast)
            except (HidppError, OSError):
                if d.name not in offline:
                    offline[d.name] = Adw.StatusPage(title=f"{d.name} não responde", icon_name=icons[d.kind],
                                                     description="Ligue o dispositivo; o app tenta de novo sozinho.")
                    stack.add_titled_with_icon(offline[d.name], d.name, d.name.split()[0], icons[d.kind])
                return False
            if d.name in offline:
                stack.remove(offline.pop(d.name))
            stack.add_titled_with_icon(page, d.name, d.name.split()[0], icons[d.kind])
            pages.append(page)
            return True

        found = dv.discover()
        for d in found:
            build(d)
        if not found:
            stack.add_child(Adw.StatusPage(
                title="Nenhum dispositivo Logitech encontrado", icon_name="dialog-warning-symbolic",
                description="Conecte o receptor/headset e confira se a regra udev 70-logibuntu.rules está instalada."))
        retry = [d for d in found if d.name in offline]
        GLib.timeout_add_seconds(15, lambda: [retry.remove(d) for d in list(retry) if build(d)] is not None and bool(retry))
        GLib.timeout_add_seconds(60, lambda: [p.refresh() for p in pages] is not None)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)))
        view.set_content(stack)
        overlay.set_child(view)
        win.set_content(overlay)
        win.present()


def main():
    return App().run(sys.argv)
