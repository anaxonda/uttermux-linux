#!/usr/bin/env python3
"""Single-click Waybar control panel for UtterMux."""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
from pathlib import Path
import subprocess
import threading
import tomllib

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
except (ValueError, ImportError):
    Gtk4LayerShell = None


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "uttermux/config.toml"
CLI = Path(__file__).resolve().with_name("uttermux")
LANGUAGE_NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "fi": "Finnish", "fil": "Filipino",
    "fr": "French", "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch",
    "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sv": "Swedish", "ta": "Tamil", "tr": "Turkish", "uk": "Ukrainian",
    "vi": "Vietnamese", "zh": "Chinese",
}


def config():
    try:
        with CONFIG.open("rb") as stream: return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError): return {}


def voices():
    result = subprocess.run([str(CLI), "voices", "--json"], text=True, capture_output=True, check=True)
    records = json.loads(result.stdout)
    for record in records: record["state"] = "ready"
    return records


def language_label(code):
    base = code.split("-", 1)[0]
    return f"{LANGUAGE_NAMES.get(base, base)} ({code})"


def current_record(records):
    selected = config().get("default_voice", "")
    return next((record for record in records if record["id"] == selected), None)


def waybar_status():
    try:
        records = voices(); current = current_record(records)
        if not current: raise RuntimeError("default voice unavailable")
        tooltip = (f"{current['name']}\nProvider: {current['provider']}\n"
                   f"Model: {current['model']}\nNative accent: {current['native_language']}\nClick to configure")
        print(json.dumps({"text": "󰔊", "tooltip": tooltip, "class": current["provider"]}))
    except Exception as error:
        print(json.dumps({"text": "󰔊", "tooltip": f"UtterMux: {error}", "class": "error"}))


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="UtterMux")
        self.add_css_class("uttermux-window")
        self.set_default_size(520, 650)
        self.records = voices()
        self.installed_records = list(self.records)
        self.discovery_token = 0
        self.discovery_source = None
        self.current = current_record(self.records)
        self.filters = {"language": None, "provider": None, "model": None}

        if Gtk4LayerShell:
            Gtk4LayerShell.init_for_window(self)
            Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, True)
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
            Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.ON_DEMAND)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed); self.add_controller(keys)

        outer = Gtk.Overlay(hexpand=True, vexpand=True)
        outer.add_css_class("uttermux-overlay")
        click_away = Gtk.GestureClick(); click_away.connect("pressed", self.background_clicked)
        outer.add_controller(click_away); self.set_child(outer)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       halign=Gtk.Align.END, valign=Gtk.Align.START,
                       width_request=520, height_request=650)
        root.add_css_class("uttermux-panel")
        root.set_margin_top(42); root.set_margin_end(8)
        outer.set_child(root); self.panel_root = root
        css = Gtk.CssProvider(); css.load_from_string(
            ".uttermux-window, .uttermux-overlay { background: transparent; } "
            ".uttermux-panel { background-color: #20242b; color: #f3f4f5; "
            "border: 1px solid #454b55; border-radius: 10px; padding: 14px; }")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<b>UtterMux</b>")
        root.append(heading)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.update_summary(); root.append(self.summary)

        self.search = Gtk.SearchEntry(placeholder_text="Search language, provider, model, or voice")
        self.search.connect("search-changed", self.search_changed); root.append(self.search)
        status_row = Gtk.Box(spacing=8)
        self.result_status = Gtk.Label(xalign=0, hexpand=True)
        clear = Gtk.Button(label="Clear filters"); clear.connect("clicked", self.clear_filters)
        status_row.append(self.result_status); status_row.append(clear); root.append(status_row)

        filter_grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        root.append(filter_grid)
        self.dropdowns = {}
        specs = [("language", "Language"), ("provider", "Provider"), ("model", "Model")]
        self.dropdown_values, self.dropdown_handlers = {}, {}
        for column, (key, label) in enumerate(specs):
            title = Gtk.Label(label=label, xalign=0); filter_grid.attach(title, column, 0, 1, 1)
            dropdown = Gtk.DropDown()
            dropdown.set_hexpand(True)
            self.dropdown_handlers[key] = dropdown.connect("notify::selected", self.filter_changed, key)
            filter_grid.attach(dropdown, column, 1, 1, 1); self.dropdowns[key] = dropdown

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.voice_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.voice_box.add_css_class("boxed-list"); scroll.set_child(self.voice_box); root.append(scroll)

        actions = Gtk.Box(spacing=8, homogeneous=True)
        for label, command in (("Read selection", [str(CLI), "speak-selection"]),
                               ("Stop", ["spd-say", "--cancel"]),
                               ("Preview", None)):
            button = Gtk.Button(label=label)
            if command: button.connect("clicked", self.run_action, command)
            else: button.connect("clicked", self.preview)
            actions.append(button)
        root.append(actions); self.refresh_facets(); self.rebuild()
        GLib.idle_add(self.search.grab_focus)

    def background_clicked(self, _gesture, _presses, x, y):
        widget = self.get_child().pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget:
            if widget is self.panel_root:
                return
            widget = widget.get_parent()
        self.close()

    def key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == 0xff1b:
            self.close()
            return True
        return False

    def run_action(self, _button, command):
        command = list(command)
        if command[:2] == [str(CLI), "speak-selection"] and self.filters["language"]:
            command += ["--language", self.filters["language"]]
        subprocess.Popen(command)
        self.close()

    def update_summary(self):
        if self.current:
            self.summary.set_markup(f"<b>{html.escape(self.current['name'])}</b>\n"
                f"{html.escape(self.current['provider'])} · {html.escape(self.current['model'])} · native {html.escape(self.current['native_language'])}")
        else: self.summary.set_text("No active default voice")

    def filter_changed(self, dropdown, _property, key):
        values = self.dropdown_values.get(key, [None])
        selected = dropdown.get_selected()
        self.filters[key] = values[selected] if selected < len(values) else None
        self.rebuild()
        GLib.idle_add(self.refresh_facets, key)
        self.schedule_discovery()

    def search_changed(self, *_args):
        self.rebuild(); self.schedule_discovery()

    def clear_filters(self, *_args):
        self.filters = {"language": None, "provider": None, "model": None}
        self.search.set_text(""); self.records = list(self.installed_records)
        self.refresh_facets(); self.schedule_discovery()

    def inferred_language(self):
        if self.filters["language"]: return self.filters["language"]
        query = self.search.get_text().casefold()
        return next((code for code, name in LANGUAGE_NAMES.items() if name.casefold() in query), "")

    def schedule_discovery(self):
        if self.discovery_source: GLib.source_remove(self.discovery_source)
        self.discovery_source = GLib.timeout_add(450, self.start_discovery)

    def start_discovery(self):
        self.discovery_source = None
        language, query, provider = self.inferred_language(), self.search.get_text().strip(), self.filters["provider"]
        if provider == "edge" or (not language and len(query) < 2):
            self.records = list(self.installed_records); self.refresh_facets(); return GLib.SOURCE_REMOVE
        self.discovery_token += 1; token = self.discovery_token
        self.result_status.set_text("Searching provider catalogs…")

        def work():
            command = [str(CLI), "discover", "--limit", "30"]
            if provider: command += ["--provider", provider]
            if language: command += ["--language", language]
            if query and not (language and query.casefold() == LANGUAGE_NAMES.get(language.split("-", 1)[0], "").casefold()):
                command += ["--search", query]
            result = subprocess.run(command, text=True, capture_output=True)
            try: records = json.loads(result.stdout) if result.returncode == 0 else []
            except json.JSONDecodeError: records = []
            GLib.idle_add(self.apply_discovery, token, records, result.stderr.strip())

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def apply_discovery(self, token, records, error):
        if token != self.discovery_token: return GLib.SOURCE_REMOVE
        known = {record["id"] for record in self.installed_records}
        self.records = list(self.installed_records) + [record for record in records if record["id"] not in known]
        self.result_status.set_text("")
        self.refresh_facets()
        if error: self.result_status.set_text(f"Catalog search failed: {error}")
        return GLib.SOURCE_REMOVE

    def matches(self, record, omit=None):
        if omit != "language" and self.filters["language"] and not any(
                value == self.filters["language"] or value.split("-", 1)[0] == self.filters["language"].split("-", 1)[0]
                for value in record["languages"]): return False
        if omit != "provider" and self.filters["provider"] and record["provider"] != self.filters["provider"]: return False
        if omit != "model" and self.filters["model"] and record["model"] != self.filters["model"]: return False
        return True

    def refresh_facets(self, skip=None):
        for key, dropdown in self.dropdowns.items():
            if key == skip:
                continue
            candidates = [record for record in self.records if self.matches(record, omit=key)]
            if key == "language": values = sorted({language for record in candidates for language in record["languages"]})
            else: values = sorted({record[key] for record in candidates})
            current = self.filters[key]
            if current not in values: current = None; self.filters[key] = None
            labels = [f"All {key}s"] + ([language_label(value) for value in values] if key == "language" else values)
            self.dropdown_values[key] = [None] + values
            dropdown.handler_block(self.dropdown_handlers[key])
            dropdown.set_model(Gtk.StringList.new(labels)); dropdown.set_selected(self.dropdown_values[key].index(current))
            dropdown.handler_unblock(self.dropdown_handlers[key])
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def rebuild(self):
        while child := self.voice_box.get_first_child(): self.voice_box.remove(child)
        query = self.search.get_text().casefold().split()
        target = self.inferred_language()

        def result_rank(record):
            native = record["native_language"].casefold().replace("_", "-")
            requested = target.casefold().replace("_", "-")
            if requested and native == requested: language_rank = 0
            elif requested and native.split("-", 1)[0] == requested.split("-", 1)[0]: language_rank = 1
            elif requested and any(value.casefold().split("-", 1)[0] == requested.split("-", 1)[0]
                                   for value in record["languages"]): language_rank = 2
            else: language_rank = 3
            state_rank = 0 if record.get("state") == "ready" else 1
            return language_rank, state_rank, record["provider"], record["name"].casefold()

        shown = 0
        for record in sorted(self.records, key=result_rank):
            if not self.matches(record): continue
            haystack = " ".join((record["id"], record["name"], record["native_language"], record["provider"],
                record["model"], *record["languages"], *(language_label(x) for x in record["languages"]))).casefold()
            if not all(term in haystack for term in query): continue
            row = Gtk.Box(spacing=6)
            button = Gtk.Button(hexpand=True)
            label = Gtk.Label(xalign=0)
            marker = ("✓ " if self.current and record["id"] == self.current["id"] else
                      "＋ " if record.get("state") in {"add", "install"} else "")
            display_target = self.filters["language"] or self.inferred_language()
            language_detail = (f"speaks {language_label(display_target)} · " if display_target else
                               f"supports {len(record['languages'])} language{'s' if len(record['languages']) != 1 else ''} · ")
            label.set_markup(f"<b>{marker}{html.escape(record['name'])}</b>\n"
                f"<small>{html.escape(record['provider'])} · {html.escape(record['model'])} · "
                f"{html.escape(language_detail)}accent {html.escape(record['native_language'])}</small>")
            button.set_child(label)
            if record.get("state") == "add": button.connect("clicked", self.add_and_choose, record)
            elif record.get("state") == "install": button.connect("clicked", self.install_and_choose, record)
            else: button.connect("clicked", self.choose, record)
            preview = Gtk.Button(label="▶", tooltip_text=f"Preview {record['name']}")
            preview.connect("clicked", self.preview_record, record)
            row.append(button); row.append(preview); self.voice_box.append(row)
            shown += 1
        if not self.result_status.get_text().startswith(("Searching", "Catalog search failed")):
            active = [value for value in self.filters.values() if value]
            suffix = f" · filters: {', '.join(active)}" if active else ""
            self.result_status.set_text(f"{shown} results{suffix}")

    def choose(self, _button, record):
        try:
            command = [str(CLI), "default", record["id"]]
            if self.filters["language"]: command += ["--language", self.filters["language"]]
            subprocess.run(command, check=True)
            self.current = record; self.update_summary(); self.rebuild()
            subprocess.run(["pkill", "-RTMIN+9", "waybar"], check=False)
            self.close()
        except subprocess.CalledProcessError as error:
            dialog = Gtk.AlertDialog(message="Could not select voice", detail=str(error)); dialog.show(self)

    def add_and_choose(self, _button, record):
        language = self.filters["language"] or record["native_language"]
        try:
            added = subprocess.run([str(CLI), "add-voice", record["provider"], record["id"],
                "--owner", record.get("owner_id", ""), "--name", record["name"],
                "--language", record["native_language"]], text=True, capture_output=True)
            if added.returncode:
                raise RuntimeError(added.stderr.strip() or added.stdout.strip() or "provider rejected the voice")
            selected = subprocess.run([str(CLI), "default", record["id"], "--language", language],
                                      text=True, capture_output=True)
            if selected.returncode:
                raise RuntimeError(selected.stderr.strip() or selected.stdout.strip() or "voice was added but could not be selected")
            subprocess.run(["pkill", "-RTMIN+9", "waybar"], check=False)
            self.close()
        except (subprocess.CalledProcessError, RuntimeError) as error:
            dialog = Gtk.AlertDialog(message="Could not add voice", detail=str(error)); dialog.show(self)

    def install_and_choose(self, _button, record):
        language = self.filters["language"] or record["native_language"]
        try:
            subprocess.run([str(CLI), "model", "install", record["model_id"]], check=True)
            subprocess.run([str(CLI), "default", record["id"], "--language", language], check=True)
            subprocess.run(["pkill", "-RTMIN+9", "waybar"], check=False)
            self.close()
        except subprocess.CalledProcessError as error:
            dialog = Gtk.AlertDialog(message="Could not install voice", detail=str(error)); dialog.show(self)

    def preview(self, _button):
        if self.current:
            self.preview_record(_button, self.current)

    def preview_record(self, _button, record):
        subprocess.run(["spd-say", "--cancel"], check=False)
        if record.get("preview_url") and record.get("state") != "ready":
            command = [str(CLI), "preview-url", record["preview_url"]]
        elif record.get("state") == "install":
            dialog = Gtk.AlertDialog(message="Install this voice before previewing it")
            dialog.show(self); return
        else:
            language = self.filters["language"] or self.inferred_language()
            samples = {
                "fr": "Bonjour. Voici un aperçu de cette voix avec UtterMux.",
                "de": "Hallo. Dies ist eine Vorschau dieser Stimme mit UtterMux.",
                "es": "Hola. Esta es una muestra de esta voz con UtterMux.",
            }
            text = samples.get((language or "en").split("-", 1)[0],
                               "This is an UtterMux voice preview.")
            command = [str(CLI), "preview", record["id"], text]
            if language: command += ["--language", language]
        subprocess.Popen(command)


class Application(Gtk.Application):
    def __init__(self): super().__init__(application_id="io.uttermux.Panel", flags=Gio.ApplicationFlags.NON_UNIQUE)
    def do_activate(self): Panel(self).present()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--waybar", action="store_true"); args = parser.parse_args()
    if args.waybar: waybar_status(); return 0
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    lock = (runtime / "uttermux-panel.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        try: os.kill(int(lock.read().strip()), 15)
        except (ValueError, ProcessLookupError, PermissionError): pass
        return 0
    lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()
    return Application().run([])


if __name__ == "__main__": raise SystemExit(main())
