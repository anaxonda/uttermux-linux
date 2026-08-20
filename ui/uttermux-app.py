#!/usr/bin/env python3
"""Native GTK4 management application for the UtterMux broker."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

CLI = Path(__file__).resolve().with_name("uttermux")
if not CLI.exists(): CLI = Path(__file__).resolve().parents[1] / "cli/uttermux"
if not CLI.exists(): CLI = Path("/usr/bin/uttermux")
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "uttermux/ui.json"


def command(*arguments: str) -> list[str]:
    return [str(CLI), *arguments]


def run_json(*arguments: str) -> dict | list:
    result = subprocess.run(command(*arguments), text=True, capture_output=True)
    if result.returncode: raise RuntimeError(result.stderr.strip() or "UtterMux command failed")
    return json.loads(result.stdout)


def saved_state() -> dict:
    try: return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}


def save_state(document: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


class VoicePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window, self.document, self.records = window, {}, []
        self.set_margin_top(16); self.set_margin_bottom(16); self.set_margin_start(16); self.set_margin_end(16)
        self.active = Gtk.Label(xalign=0, wrap=True); self.active.add_css_class("title-3"); self.append(self.active)
        self.filters = {}
        grid = Gtk.Grid(column_spacing=8, row_spacing=6); self.append(grid)
        state = saved_state().get("filters", {})
        for column, (key, label, placeholder) in enumerate((
            ("voice", "Voice", "Search voice name"), ("language", "Language", "e.g. French or fr"),
            ("service", "Service / runtime", "e.g. Local or Edge"), ("model", "Model", "e.g. Pocket"))):
            grid.attach(Gtk.Label(label=label, xalign=0), column, 0, 1, 1)
            entry = Gtk.SearchEntry(placeholder_text=placeholder, hexpand=True)
            entry.set_text(state.get(key, "")); entry.connect("search-changed", self.filter_changed)
            grid.attach(entry, column, 1, 1, 1); self.filters[key] = entry
        row = Gtk.Box(spacing=8); self.append(row)
        self.location = Gtk.DropDown(model=Gtk.StringList.new(["All locations", "Offline", "Online"]))
        self.readiness = Gtk.DropDown(model=Gtk.StringList.new(["All voices", "Ready", "Downloadable"]))
        self.performance = Gtk.DropDown(model=Gtk.StringList.new(["Any performance", "Fast", "Balanced", "Heavy", "Cloud"]))
        self.sorting = Gtk.DropDown(model=Gtk.StringList.new(["Recommended", "Name", "Smallest download", "Lowest RAM"]))
        for widget in (self.location, self.readiness, self.performance, self.sorting):
            widget.connect("notify::selected", self.filter_changed); row.append(widget)
        clear = Gtk.Button(label="Clear filters"); clear.connect("clicked", self.clear); row.append(clear)
        self.status = Gtk.Label(xalign=0); self.append(self.status)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE); self.listbox.add_css_class("boxed-list")
        scroll.set_child(self.listbox); self.append(scroll)
        actions = Gtk.Box(spacing=8)
        read = Gtk.Button(label="Read selection"); read.connect("clicked", lambda *_: subprocess.Popen(command("speak-selection")))
        stop = Gtk.Button(label="Stop speech"); stop.connect("clicked", lambda *_: subprocess.run(["spd-say", "--cancel"], check=False))
        refresh = Gtk.Button(label="Refresh"); refresh.connect("clicked", lambda *_: self.load())
        actions.append(read); actions.append(stop); actions.append(refresh); self.append(actions)
        self.load()

    def load(self):
        self.status.set_text("Loading voice catalog…")
        def work():
            try: document, status = run_json("catalog"), run_json("status")
            except Exception as error: GLib.idle_add(self.loaded, None, None, str(error)); return
            GLib.idle_add(self.loaded, document, status, "")
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, document, status, error):
        if error: self.status.set_text(error); return GLib.SOURCE_REMOVE
        self.document = document; models = {item["id"]: item for item in document.get("models", [])}
        self.records = []
        for voice in document.get("voices", []):
            model = models.get(voice["modelId"], {})
            self.records.append(voice | {"model": model, "profile": False})
        existing = {item["id"] for item in self.records}
        for profile in document.get("profiles", []):
            if profile.get("voiceId") not in existing:
                self.records.append({"id": profile["voiceId"], "name": profile["name"],
                    "languages": [profile["language"]], "modelId": profile["modelVersion"],
                    "model": models.get(profile["modelVersion"], {}), "ready": profile.get("available", False),
                    "preview": "generated", "profile": True})
        self.default_id = status.get("configuredDefault", "")
        current = next((item for item in self.records if item["id"] == self.default_id), None)
        self.active.set_markup("<b>Active voice:</b> " + html.escape(current["name"] if current else self.default_id or "None"))
        self.rebuild(); return GLib.SOURCE_REMOVE

    def clear(self, *_args):
        for entry in self.filters.values(): entry.set_text("")
        for dropdown in (self.location, self.readiness, self.performance, self.sorting): dropdown.set_selected(0)
        self.rebuild()

    def filter_changed(self, *_args):
        save_state({"filters": {key: entry.get_text() for key, entry in self.filters.items()}})
        self.rebuild()

    def rebuild(self):
        while child := self.listbox.get_first_child(): self.listbox.remove(child)
        terms = {key: entry.get_text().casefold().split() for key, entry in self.filters.items()}
        result = []
        for record in self.records:
            model = record.get("model", {}); provider = model.get("providerId", "local")
            fields = {"voice": record.get("name", ""), "language": " ".join(record.get("languages", [])),
                      "service": provider + " " + model.get("engine", ""),
                      "model": record.get("modelId", "") + " " + model.get("library", "")}
            if any(not all(term in fields[key].casefold() for term in wanted) for key, wanted in terms.items()): continue
            online = model.get("location") == "cloud"
            if self.location.get_selected() == 1 and online: continue
            if self.location.get_selected() == 2 and not online: continue
            ready = bool(record.get("ready"))
            if self.readiness.get_selected() == 1 and not ready: continue
            if self.readiness.get_selected() == 2 and ready: continue
            perf = model.get("performanceClass", "unknown").casefold()
            if self.performance.get_selected() and perf != ("fast", "balanced", "heavy", "cloud")[self.performance.get_selected()-1]: continue
            result.append(record)
        sort = self.sorting.get_selected()
        if sort == 1: result.sort(key=lambda item: item["name"].casefold())
        elif sort == 2: result.sort(key=lambda item: item.get("model", {}).get("downloadSizeMb", 10**9))
        elif sort == 3: result.sort(key=lambda item: item.get("model", {}).get("estimatedRamMb", 10**9))
        else: result.sort(key=lambda item: (item["id"] != self.default_id, not item.get("ready", False), item["name"].casefold()))
        for record in result:
            model = record.get("model", {}); row = Gtk.Box(spacing=8)
            details = [model.get("library", record.get("modelId", "")), ", ".join(record.get("languages", []))]
            if model.get("downloadSizeMb"): details.append(f"{model['downloadSizeMb']} MB download")
            if model.get("estimatedRamMb"): details.append(f"~{model['estimatedRamMb']} MB RAM")
            if model.get("quantization"): details.append(model["quantization"])
            label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
            marker = "✓ " if record["id"] == self.default_id else ""
            label.set_markup(f"<b>{html.escape(marker + record['name'])}</b>\n<small>{html.escape(' · '.join(filter(None, details)))}</small>")
            row.append(label)
            if record.get("ready"):
                choose = Gtk.Button(label="Use"); choose.connect("clicked", self.choose, record); row.append(choose)
                preview = Gtk.Button(label="Preview"); preview.connect("clicked", self.preview, record); row.append(preview)
            elif model.get("location") == "on-device":
                install = Gtk.Button(label="Download"); install.connect("clicked", self.install, record); row.append(install)
            self.listbox.append(row)
        self.status.set_text(f"{len(result)} voice{'s' if len(result) != 1 else ''}")

    def choose(self, _button, record): self.window.run_task(command("default", record["id"]), "Voice selected", self.load)
    def preview(self, _button, record): subprocess.Popen(command("preview", record["id"], "This is an UtterMux voice preview."))
    def install(self, _button, record): self.window.run_task(command("model", "install", record["modelId"]), "Model installed", self.load)


class CreatePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window, self.audio, self.record_process = window, "", None
        self.set_margin_top(24); self.set_margin_start(24); self.set_margin_end(24)
        title = Gtk.Label(label="Create a voice", xalign=0); title.add_css_class("title-2"); self.append(title)
        self.append(Gtk.Label(label="Create engine-specific voices from recordings you have the right to use.", xalign=0, wrap=True))
        form = Gtk.Grid(column_spacing=12, row_spacing=10); self.append(form)
        self.engine = Gtk.DropDown(model=Gtk.StringList.new(["Pocket (local)", "ZipVoice (local)", "ElevenLabs Instant Clone"]))
        self.name = Gtk.Entry(placeholder_text="Voice name"); self.language = Gtk.Entry(text="en-US")
        self.transcript = Gtk.Entry(placeholder_text="Exact transcript (required by ZipVoice)")
        self.file_label = Gtk.Label(label="No recording selected", xalign=0, hexpand=True, ellipsize=3)
        pick = Gtk.Button(label="Choose recording…"); pick.connect("clicked", self.pick_file)
        self.record = Gtk.Button(label="Record microphone…"); self.record.connect("clicked", self.toggle_record)
        for row, (label, widget) in enumerate((("Engine", self.engine), ("Name", self.name), ("Language", self.language), ("Transcript", self.transcript))):
            form.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1); form.attach(widget, 1, row, 2, 1)
        form.attach(Gtk.Label(label="Recording", xalign=0), 0, 4, 1, 1); form.attach(self.file_label, 1, 4, 1, 1); form.attach(pick, 2, 4, 1, 1)
        form.attach(self.record, 1, 5, 2, 1)
        self.consent = Gtk.CheckButton(label="I have the right and consent to clone this voice"); self.append(self.consent)
        create = Gtk.Button(label="Create voice"); create.add_css_class("suggested-action"); create.connect("clicked", self.create); self.append(create)
        self.status = Gtk.Label(xalign=0, wrap=True); self.append(self.status)
        profile_heading = Gtk.Box(spacing=8); heading = Gtk.Label(label="My local voices", xalign=0, hexpand=True); heading.add_css_class("title-3"); profile_heading.append(heading)
        import_button = Gtk.Button(label="Import bundle…"); import_button.connect("clicked", self.import_profile); profile_heading.append(import_button); self.append(profile_heading)
        self.profile_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE); self.profile_box.add_css_class("boxed-list")
        profile_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER); profile_scroll.set_child(self.profile_box); self.append(profile_scroll)
        self.refresh_profiles()

    def pick_file(self, *_args):
        chooser = Gtk.FileChooserNative(title="Choose voice reference", transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN, accept_label="Choose", cancel_label="Cancel")
        chooser.connect("response", self.file_response); chooser.show()

    def file_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            self.audio = chooser.get_file().get_path(); self.file_label.set_text(self.audio)
        chooser.destroy()

    def toggle_record(self, *_args):
        if self.record_process and self.record_process.poll() is None:
            self.record_process.terminate(); self.record_process.wait(timeout=3); self.record_process = None
            self.record.set_label("Record microphone…"); self.file_label.set_text(self.audio); self.status.set_text("Recording ready. Preview it after creating the voice.")
            return
        if not GLib.find_program_in_path("pw-record"):
            self.status.set_text("pw-record is unavailable; install PipeWire or choose an existing recording."); return
        fd, path = tempfile.mkstemp(prefix="uttermux-reference-", suffix=".wav",
                                    dir=os.environ.get("XDG_RUNTIME_DIR", "/tmp")); os.close(fd)
        self.audio = path
        self.record_process = subprocess.Popen(["pw-record", "--rate=24000", "--channels=1", "--format=s16", path])
        self.record.set_label("Stop recording"); self.status.set_text("Recording from the default microphone…")

    def create(self, *_args):
        if not self.consent.get_active(): self.status.set_text("Confirm that you have permission to clone this voice."); return
        if not self.audio or not self.name.get_text().strip(): self.status.set_text("Choose a recording and enter a name."); return
        selected = self.engine.get_selected()
        if selected == 2:
            cmd = command("elevenlabs-clone", "--name", self.name.get_text(), "--language", self.language.get_text(),
                          "--audio", self.audio, "--confirm-rights")
        else:
            engine = "pocket" if selected == 0 else "zipvoice"
            cmd = command("profile-create", engine, "--name", self.name.get_text(), "--language", self.language.get_text(), "--audio", self.audio)
            if self.transcript.get_text(): cmd += ["--transcript", self.transcript.get_text()]
        self.status.set_text("Creating voice…"); self.window.run_task(cmd, "Voice created", self.created)

    def created(self):
        self.status.set_text("Voice created. It is now available under Voices."); self.refresh_profiles(); self.window.voices.load()

    def refresh_profiles(self):
        try: records = run_json("profiles")
        except Exception: records = []
        while child := self.profile_box.get_first_child(): self.profile_box.remove(child)
        for profile in records:
            row = Gtk.Box(spacing=8); label = Gtk.Label(xalign=0, hexpand=True)
            label.set_markup(f"<b>{html.escape(profile['name'])}</b>\n<small>{html.escape(profile['engine'])} · {html.escape(profile['language'])}</small>"); row.append(label)
            preview = Gtk.Button(label="Preview"); preview.connect("clicked", lambda _b, p=profile: subprocess.Popen(command("preview", p["voiceId"]))); row.append(preview)
            export = Gtk.Button(label="Export…"); export.connect("clicked", self.export_profile, profile); row.append(export)
            delete = Gtk.Button(label="Delete"); delete.add_css_class("destructive-action"); delete.connect("clicked", self.delete_profile, profile); row.append(delete)
            self.profile_box.append(row)
        if not records: self.profile_box.append(Gtk.Label(label="No cloned local voices yet.", xalign=0))

    def export_profile(self, _button, profile):
        chooser = Gtk.FileChooserNative(title="Export voice", transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE, accept_label="Export", cancel_label="Cancel")
        chooser.set_current_name(f"{profile['name']}.uttermux-voice")
        def response(dialog, value):
            if value == Gtk.ResponseType.ACCEPT:
                self.window.run_task(command("profile-export", profile["id"], dialog.get_file().get_path()), "Voice exported")
            dialog.destroy()
        chooser.connect("response", response); chooser.show()

    def import_profile(self, *_args):
        chooser = Gtk.FileChooserNative(title="Import UtterMux voice", transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN, accept_label="Import", cancel_label="Cancel")
        def response(dialog, value):
            if value == Gtk.ResponseType.ACCEPT:
                self.window.run_task(command("profile-import", dialog.get_file().get_path()), "Voice imported", self.created)
            dialog.destroy()
        chooser.connect("response", response); chooser.show()

    def delete_profile(self, _button, profile):
        dialog = Gtk.Dialog(title=f"Delete {profile['name']}?", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Delete", Gtk.ResponseType.ACCEPT)
        dialog.get_content_area().append(Gtk.Label(label="The managed reference recording and profile will be permanently removed.", wrap=True,
                                                    margin_top=16, margin_bottom=16, margin_start=16, margin_end=16))
        def response(_dialog, value):
            if value == Gtk.ResponseType.ACCEPT:
                self.window.run_task(command("profile-delete", profile["id"]), "Voice deleted", self.created)
            dialog.destroy()
        dialog.connect("response", response); dialog.present()


class SettingsPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window; self.set_margin_top(20); self.set_margin_start(20); self.set_margin_end(20)
        title = Gtk.Label(label="Settings", xalign=0); title.add_css_class("title-2"); self.append(title)
        self.append(Gtk.Label(label="Online services", xalign=0, css_classes=["heading"]))
        for provider, title, detail in (("edge", "Microsoft Edge", "Free network voices; no API key."),
            ("elevenlabs", "ElevenLabs", "Subscription voices and Instant Voice Cloning."),
            ("grok", "xAI / Grok", "Multilingual cloud synthesis using your xAI key.")):
            row = Gtk.Box(spacing=8); text = Gtk.Label(xalign=0, hexpand=True, wrap=True)
            text.set_markup(f"<b>{html.escape(title)}</b>\n<small>{html.escape(detail)}</small>"); row.append(text)
            enable = Gtk.Button(label="Enable"); enable.connect("clicked", lambda _b, p=provider: self.window.run_task(command("provider", "enable", p), f"{p} enabled")); row.append(enable)
            disable = Gtk.Button(label="Disable"); disable.connect("clicked", lambda _b, p=provider: self.window.run_task(command("provider", "disable", p), f"{p} disabled")); row.append(disable)
            if provider in {"elevenlabs", "grok"}:
                key = Gtk.Button(label="Set API key…"); key.connect("clicked", self.set_key, provider, title); row.append(key)
            self.append(row)
        advanced = Gtk.Expander(label="Advanced playback and diagnostics"); box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(label="Model cache controls how many local engines stay warm. Audio cache reuses completed cloud utterances. Defaults favor a 16 GB desktop.", xalign=0, wrap=True))
        doctor = Gtk.Button(label="Run diagnostics"); doctor.connect("clicked", lambda *_: self.window.run_task(command("doctor"), "Diagnostics completed")); box.append(doctor)
        advanced.set_child(box); self.append(advanced)
        system = Gtk.Button(label="Open system text-to-speech settings"); system.connect("clicked", self.open_system); self.append(system)

    def open_system(self, *_args):
        for cmd in (["systemsettings", "kcm_tts"], ["gnome-control-center", "accessibility"]):
            if GLib.find_program_in_path(cmd[0]): subprocess.Popen(cmd); return

    def set_key(self, _button, provider, title):
        dialog = Gtk.Dialog(title=f"{title} API key", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Paste API key")
        entry.set_margin_top(16); entry.set_margin_bottom(16); entry.set_margin_start(16); entry.set_margin_end(16)
        dialog.get_content_area().append(entry)
        def response(_dialog, value):
            if value == Gtk.ResponseType.ACCEPT and entry.get_text().strip():
                secret = entry.get_text().strip()
                def work():
                    result = subprocess.run(command("credential-set", provider), input=secret + "\n", text=True, capture_output=True)
                    GLib.idle_add(lambda: Gtk.AlertDialog(message=("Credential saved" if result.returncode == 0 else "Could not save credential"), detail=result.stderr.strip()).show(self.window))
                threading.Thread(target=work, daemon=True).start()
            dialog.destroy()
        dialog.connect("response", response); dialog.present()


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="UtterMux", default_width=920, default_height=720)
        header = Gtk.HeaderBar(); header.set_title_widget(Gtk.Label(label="UtterMux", css_classes=["title"])); self.set_titlebar(header)
        notebook = Gtk.Notebook()
        self.voices = VoicePage(self); notebook.append_page(self.voices, Gtk.Label(label="Voices"))
        notebook.append_page(CreatePage(self), Gtk.Label(label="Create"))
        notebook.append_page(SettingsPage(self), Gtk.Label(label="Settings")); self.set_child(notebook)

    def run_task(self, argv, success, callback=None):
        def work():
            result = subprocess.run(argv, text=True, capture_output=True)
            GLib.idle_add(done, result)
        def done(result):
            if result.returncode:
                Gtk.AlertDialog(message="UtterMux operation failed", detail=result.stderr.strip() or result.stdout.strip()).show(self)
            else:
                if callback: callback()
                else: Gtk.AlertDialog(message=success).show(self)
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()


class Application(Gtk.Application):
    def __init__(self): super().__init__(application_id="io.uttermux.App", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    def do_activate(self):
        window = self.get_active_window()
        if not window: window = Window(self)
        window.present()


if __name__ == "__main__": raise SystemExit(Application().run([]))
