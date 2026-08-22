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

CLI = Path(os.environ.get("UTTERMUX_CLI", "")) if os.environ.get("UTTERMUX_CLI") else Path(__file__).resolve().with_name("uttermux")
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
    current = saved_state()
    current.update(document)
    STATE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


LANGUAGE_NAMES = {
    "ar": "Arabic", "de": "German", "en": "English", "es": "Spanish",
    "fr": "French", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ru": "Russian", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese",
    "zh": "Chinese",
}

RECOMMENDATION_LABELS = {
    "recommended": "Recommended here", "likely-usable": "Likely usable",
    "may-be-slow": "May be slow", "memory-pressure": "Memory pressure",
    "insufficient-memory": "Insufficient memory", "available": "Online",
    "unknown": "Not benchmarked",
}
RECOMMENDATION_ORDER = {name: index for index, name in enumerate((
    "recommended", "likely-usable", "may-be-slow", "memory-pressure",
    "insufficient-memory", "unknown", "available"))}


def language_label(tag: str) -> str:
    base = tag.split("-", 1)[0].casefold()
    return f"{LANGUAGE_NAMES.get(base, tag)} ({tag})"


def size_label(size: int) -> str:
    if size >= 1024 ** 3: return f"{size / 1024 ** 3:.1f} GiB installed"
    return f"{size / 1024 ** 2:.1f} MiB installed"


class VoicePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window, self.document, self.records = window, {}, []
        self.loading_filters = False
        self.set_margin_top(16); self.set_margin_bottom(16); self.set_margin_start(16); self.set_margin_end(16)
        self.active = Gtk.Label(xalign=0, wrap=True); self.active.add_css_class("title-3"); self.append(self.active)
        self.filter_state = saved_state().get("filters", {})
        self.search = Gtk.SearchEntry(placeholder_text="Search voices", hexpand=True)
        self.search.set_text(self.filter_state.get("query", self.filter_state.get("voice", "")))
        self.search.connect("search-changed", self.filter_changed); self.append(self.search)
        choice_grid = Gtk.Grid(column_spacing=8, row_spacing=4); self.append(choice_grid)
        self.exact_filters = {}
        self.exact_values = {}
        for column, (key, label, initial) in enumerate((("language", "Language", "All languages"),
                ("provider", "Provider", "All providers"), ("model", "Model", "All models"))):
            choice_grid.attach(Gtk.Label(label=label, xalign=0), column, 0, 1, 1)
            dropdown = Gtk.DropDown(model=Gtk.StringList.new([initial]), hexpand=True, enable_search=True)
            dropdown.connect("notify::selected", self.filter_changed)
            choice_grid.attach(dropdown, column, 1, 1, 1); self.exact_filters[key] = dropdown
        row = Gtk.Box(spacing=8); self.append(row)
        self.location = Gtk.DropDown(model=Gtk.StringList.new(["All locations", "Offline", "Online"]))
        self.readiness = Gtk.DropDown(model=Gtk.StringList.new(["All voices", "Ready", "Downloadable"]))
        self.favorites = Gtk.DropDown(model=Gtk.StringList.new(["All voices", "Favorites"]))
        self.performance = Gtk.DropDown(model=Gtk.StringList.new(["Any performance", "Fast", "Balanced", "Heavy", "Cloud"]))
        self.sorting = Gtk.DropDown(model=Gtk.StringList.new(["Recommended", "Name", "Smallest download", "Lowest RAM"]))
        for widget in (self.favorites, self.location, self.readiness, self.performance, self.sorting):
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
        self.document = document
        self.provider_names = {item["id"]: item.get("name", item["id"]) for item in document.get("providers", [])}
        models = {item["id"]: item for item in document.get("models", [])}
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
        self.populate_exact_filters()
        current = next((item for item in self.records if item["id"] == self.default_id), None)
        self.active.set_markup("<b>Active voice:</b> " + html.escape(current["name"] if current else self.default_id or "None"))
        self.rebuild(); return GLib.SOURCE_REMOVE

    def clear(self, *_args):
        self.search.set_text("")
        for dropdown in self.exact_filters.values(): dropdown.set_selected(0)
        for dropdown in (self.favorites, self.location, self.readiness, self.performance, self.sorting): dropdown.set_selected(0)
        self.rebuild()

    def filter_changed(self, *_args):
        if self.loading_filters: return
        filters = {"query": self.search.get_text()}
        filters.update({f"exact_{key}": self.dropdown_value(key) for key in self.exact_filters})
        filters.update({"favorites_index": self.favorites.get_selected(), "location_index": self.location.get_selected(), "readiness_index": self.readiness.get_selected(),
                        "performance_index": self.performance.get_selected(), "sorting_index": self.sorting.get_selected()})
        self.filter_state = filters
        save_state({"filters": filters})
        self.rebuild()

    def populate_exact_filters(self):
        self.loading_filters = True
        models = {item["id"]: item for item in self.document.get("models", [])}
        provider_ids = sorted({models.get(item.get("modelId"), {}).get("providerId", "local") for item in self.records},
                              key=lambda value: self.provider_names.get(value, value).casefold())
        language_tags = sorted({language for item in self.records for language in item.get("languages", [])})
        model_names = sorted({models.get(item.get("modelId"), {}).get("library", "") for item in self.records
                              if models.get(item.get("modelId"), {}).get("library")}, key=str.casefold)
        values = {"provider": provider_ids, "language": language_tags, "model": model_names}
        displays = {"provider": [self.provider_names.get(value, value) for value in provider_ids],
                    "language": [language_label(value) for value in language_tags], "model": model_names}
        labels = {"provider": "All providers", "language": "All languages", "model": "All models"}
        for key, dropdown in self.exact_filters.items():
            wanted = self.filter_state.get(f"exact_{key}", "")
            self.exact_values[key] = ["", *values[key]]
            dropdown.set_model(Gtk.StringList.new([labels[key], *displays[key]]))
            dropdown.set_selected(self.exact_values[key].index(wanted) if wanted in self.exact_values[key] else 0)
        for key, widget in (("favorites_index", self.favorites), ("location_index", self.location), ("readiness_index", self.readiness),
                            ("performance_index", self.performance), ("sorting_index", self.sorting)):
            widget.set_selected(int(self.filter_state.get(key, 0)))
        self.loading_filters = False

    def dropdown_value(self, key):
        values = self.exact_values.get(key, [""])
        selected = self.exact_filters[key].get_selected()
        return values[selected] if selected < len(values) else ""

    def rebuild(self):
        while child := self.listbox.get_first_child(): self.listbox.remove(child)
        active_model_id = next((item.get("modelId") for item in self.records if item["id"] == self.default_id), "")
        terms = self.search.get_text().casefold().split()
        result = []
        for record in self.records:
            model = record.get("model", {}); provider = model.get("providerId", "local")
            provider_label = self.provider_names.get(provider, provider)
            languages = record.get("languages", [])
            searchable = " ".join((record.get("name", ""), record.get("id", ""), provider, provider_label,
                model.get("engine", ""), record.get("modelId", ""), model.get("library", ""),
                " ".join(languages), " ".join(language_label(value) for value in languages))).casefold()
            if not all(term in searchable for term in terms): continue
            exact = {key: self.dropdown_value(key) for key in self.exact_filters}
            if exact["provider"] and provider != exact["provider"]: continue
            if exact["language"] and exact["language"] not in languages: continue
            if exact["model"] and model.get("library", "") != exact["model"]: continue
            online = model.get("location") == "cloud"
            if self.location.get_selected() == 1 and online: continue
            if self.location.get_selected() == 2 and not online: continue
            ready = bool(record.get("ready"))
            if self.favorites.get_selected() == 1 and not record.get("favorite", False): continue
            if self.readiness.get_selected() == 1 and not ready: continue
            if self.readiness.get_selected() == 2 and ready: continue
            perf = model.get("performanceClass", "unknown").casefold()
            if self.performance.get_selected() and perf != ("fast", "balanced", "heavy", "cloud")[self.performance.get_selected()-1]: continue
            result.append(record)
        sort = self.sorting.get_selected()
        if sort == 1: result.sort(key=lambda item: item["name"].casefold())
        elif sort == 2: result.sort(key=lambda item: item.get("model", {}).get("downloadSizeMb", 10**9))
        elif sort == 3: result.sort(key=lambda item: item.get("model", {}).get("estimatedRamMb", 10**9))
        else: result.sort(key=lambda item: (item["id"] != self.default_id,
            RECOMMENDATION_ORDER.get(item.get("model", {}).get("recommendation", "unknown"), 99),
            not item.get("ready", False), item["name"].casefold()))
        for record in result:
            model = record.get("model", {}); row = Gtk.Box(spacing=8)
            provider = model.get("providerId", "local")
            details = [self.provider_names.get(provider, provider), model.get("library", record.get("modelId", "")),
                       ", ".join(record.get("languages", [])), model.get("performanceClass", "")]
            if model.get("downloadSizeMb"): details.append(f"{model['downloadSizeMb']} MB download")
            if model.get("installedSizeBytes"): details.append(size_label(model["installedSizeBytes"]))
            if model.get("estimatedRamMb"): details.append(f"~{model['estimatedRamMb']} MB RAM")
            if model.get("quantization"): details.append(model["quantization"])
            recommendation = model.get("recommendation", "unknown")
            details.append(RECOMMENDATION_LABELS.get(recommendation, recommendation))
            label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
            marker = "✓ " if record["id"] == self.default_id else ""
            label.set_markup(f"<b>{html.escape(marker + record['name'])}</b>\n<small>{html.escape(' · '.join(filter(None, details)))}</small>")
            label.set_tooltip_text(model.get("recommendationReason", ""))
            row.append(label)
            favorite = Gtk.Button(label="★" if record.get("favorite") else "☆")
            favorite.set_tooltip_text("Remove from favorites" if record.get("favorite") else "Add to favorites")
            favorite.connect("clicked", self.toggle_favorite, record); row.append(favorite)
            if record.get("ready"):
                choose = Gtk.Button(label="Active" if record["id"] == self.default_id else "Use")
                choose.set_sensitive(record["id"] != self.default_id)
                choose.connect("clicked", self.choose, record); row.append(choose)
                spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER); row.append(spinner)
                preview = Gtk.Button(label="Preview"); preview.connect("clicked", self.preview, record, spinner); row.append(preview)
                if model.get("providerId") == "local":
                    test = Gtk.Button(label="Test model"); test.connect("clicked", lambda *_args: self.window.show_test_page())
                    row.append(test)
                if model.get("removable"):
                    delete = Gtk.Button(label="Delete model")
                    delete.add_css_class("destructive-action")
                    delete.set_sensitive(record["modelId"] != active_model_id)
                    if record["modelId"] == active_model_id:
                        delete.set_tooltip_text("Select a voice from another model before deleting this model")
                    delete.connect("clicked", self.confirm_delete, record); row.append(delete)
            elif model.get("location") == "on-device":
                install = Gtk.Button(label="Download"); install.connect("clicked", self.install, record); row.append(install)
            self.listbox.append(row)
        if result:
            self.status.set_text(f"{len(result)} voice{'s' if len(result) != 1 else ''}")
        else:
            active = []
            if self.search.get_text().strip(): active.append(f'search “{self.search.get_text().strip()}”')
            for key in ("language", "provider", "model"):
                value = self.dropdown_value(key)
                if value: active.append(f"{key} {value}")
            labels = ((self.location, ("", "Offline", "Online")),
                      (self.readiness, ("", "Ready", "Downloadable")),
                      (self.performance, ("", "Fast", "Balanced", "Heavy", "Cloud")))
            for widget, values in labels:
                if widget.get_selected(): active.append(values[widget.get_selected()])
            detail = f" Active filters: {', '.join(active)}." if active else ""
            self.status.set_text(f"No matching voices.{detail} Use Clear filters to show the full catalog.")

    def toggle_favorite(self, _button, record):
        action = "remove" if record.get("favorite") else "add"
        self.window.run_task(command("favorite", action, record["id"]),
                             "Favorite updated", self.load)

    def choose(self, _button, record): self.window.run_task(command("default", record["id"]), "Voice selected", self.load)
    def preview(self, button, record, spinner):
        button.set_sensitive(False); button.set_label("Loading / playing…")
        spinner.set_visible(True); spinner.start()
        self.status.set_text(f"Loading {record['name']} preview…")
        def work():
            result = subprocess.run(command("preview", record["id"], "This is an UtterMux voice preview."),
                                    text=True, capture_output=True)
            GLib.idle_add(done, result)
        def done(result):
            spinner.stop(); spinner.set_visible(False)
            button.set_sensitive(True); button.set_label("Preview")
            if result.returncode:
                self.window.alert("Preview failed", result.stderr.strip() or result.stdout.strip())
                self.status.set_text(f"Preview failed for {record['name']}")
            else: self.status.set_text(f"Preview completed for {record['name']}")
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()
    def install(self, _button, record): self.window.run_task(command("model", "install", record["modelId"]), "Model installed", self.load)

    def confirm_delete(self, _button, record):
        model = record["model"]
        size = size_label(model.get("installedSizeBytes", 0)).removesuffix(" installed")
        dialog = Gtk.AlertDialog(message=f"Delete {model.get('library', record['name'])}?", detail=(
            f"This removes {size} of downloaded model data. All voices supplied by this model will become unavailable. "
            "Runtime components and your settings are preserved."),
            buttons=["Cancel", "Delete"], cancel_button=0, default_button=0)
        def chosen(_dialog, task):
            try: choice = _dialog.choose_finish(task)
            except GLib.Error: choice = 0
            if choice == 1:
                self.window.run_task(command("model", "remove", record["modelId"]), "Model deleted", self.load)
        dialog.choose(self.window, None, chosen)


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
        self.window, self.loading = window, True
        self.set_margin_top(20); self.set_margin_bottom(20); self.set_margin_start(20); self.set_margin_end(20)
        title = Gtk.Label(label="Settings", xalign=0); title.add_css_class("title-2"); self.append(title)
        self.append(Gtk.Label(label="Online providers", xalign=0, css_classes=["heading"]))
        providers_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE); providers_box.add_css_class("boxed-list")
        self.provider_switches = {}; self.provider_fields = {}
        for provider, title, detail in (("edge", "Microsoft Edge", "Free network voices; no API key."),
            ("elevenlabs", "ElevenLabs", "Subscription voices and Instant Voice Cloning."),
            ("grok", "xAI / Grok", "Multilingual cloud synthesis using your xAI key."),
            ("openai", "OpenAI-compatible", "OpenAI speech API or a compatible endpoint."),
            ("azure", "Azure Speech", "Azure neural voices using a Speech resource."),
            ("qwen-api", "Qwen / DashScope", "Hosted Qwen multilingual voices."),
            ("google", "Google Cloud TTS", "Direct restricted key or UtterMux-compatible proxy."),
            ("aws", "Amazon Polly", "Direct, Cognito, or proxy authentication."),
            ("deepgram", "Deepgram", "Aura 2 hosted voices."),
            ("cartesia", "Cartesia", "Sonic hosted and account voices."),
            ("playht", "PlayHT", "Hosted Play voice catalog."),
            ("resemble", "Resemble AI", "Configured Resemble voice UUIDs."),
            ("custom", "Custom streamed PCM", "Constrained HTTPS JSON-to-PCM endpoint.")):
            row = Gtk.Box(spacing=8); text = Gtk.Label(xalign=0, hexpand=True, wrap=True)
            text.set_markup(f"<b>{html.escape(title)}</b>\n<small>{html.escape(detail)}</small>"); row.append(text)
            if provider in {"elevenlabs", "grok"}:
                key = Gtk.Button(label="API key…"); key.connect("clicked", self.set_key, provider, title); row.append(key)
            elif provider != "edge":
                configure = Gtk.Button(label="Configure…"); configure.connect("clicked", self.configure_provider, provider, title); row.append(configure)
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER); toggle.connect("state-set", self.toggle_provider, provider)
            self.provider_switches[provider] = toggle; row.append(toggle); providers_box.append(row)
        self.append(providers_box)

        advanced = Gtk.Expander(label="Global defaults, playback, and language routing")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=10, margin_bottom=8, margin_start=8, margin_end=8)
        box.append(Gtk.Label(label="These are global defaults for Firefox, Zotero, selection reading, and KOReader. Per-artifact values on Test & tune take precedence.", xalign=0, wrap=True))
        self.auto_language = Gtk.Switch(); self.auto_language.connect("state-set", self.set_boolean, "auto-detect-language")
        box.append(self.setting_row("Detect language automatically", "Routes longer text to a compatible configured voice.", self.auto_language))
        self.preload_voice = Gtk.Switch(); self.preload_voice.connect("state-set", self.set_boolean, "preload-default-voice")
        box.append(self.setting_row("Preload active local voice", "Uses more memory after login, but removes the first-use model loading delay.", self.preload_voice))
        self.playback_speed = Gtk.SpinButton.new_with_range(.5, 2, .05); self.playback_speed.set_digits(2)
        box.append(self.setting_row("Speech speed", "Global multiplier for every provider and client. Application rate controls are multiplied by this value; 1.00 is unchanged.", self.playback_speed))
        box.append(Gtk.Separator())
        performance_heading = Gtk.Label(label="Local inference", xalign=0); performance_heading.add_css_class("heading")
        box.append(performance_heading)
        self.local_threads = Gtk.SpinButton.new_with_range(0, 16, 1)
        box.append(self.setting_row("ONNX CPU threads", "0 = Automatic (up to 4). A positive value is a device-specific override.", self.local_threads))
        self.pocket_threads = Gtk.SpinButton.new_with_range(0, 16, 1)
        box.append(self.setting_row("Pocket CPU threads", "0 = Automatic (up to 2). Pocket often slows down with excess parallelism.", self.pocket_threads))
        self.silence_scale = Gtk.SpinButton.new_with_range(0, 2, .05); self.silence_scale.set_digits(2)
        box.append(self.setting_row("Generated pause scale", "Scales pauses created inside a local-model utterance. It cannot remove pauses inserted by the reading application.", self.silence_scale))
        self.pocket_steps = Gtk.SpinButton.new_with_range(1, 8, 1)
        box.append(self.setting_row("Pocket quality steps", "More steps may improve quality but increase latency. Recommended: 3.", self.pocket_steps))
        self.pocket_chunk = Gtk.SpinButton.new_with_range(1, 16, 1)
        box.append(self.setting_row("Pocket generation chunk", "Larger chunks may improve continuity at the cost of responsiveness. Recommended: 4.", self.pocket_chunk))
        self.zipvoice_steps = Gtk.SpinButton.new_with_range(1, 8, 1)
        box.append(self.setting_row("ZipVoice quality steps", "More flow-matching steps trade speed for quality. Recommended: 4.", self.zipvoice_steps))
        self.moss_threads = Gtk.SpinButton.new_with_range(0, 8, 1)
        box.append(self.setting_row("MOSS pipeline threads", "0 = Automatic (up to 2 per concurrent stage). A positive value overrides it.", self.moss_threads))
        self.moss_batch = Gtk.SpinButton.new_with_range(1, 16, 1)
        box.append(self.setting_row("MOSS decode batch", "Smaller batches start sooner; larger batches may slightly improve throughput. Recommended: 4.", self.moss_batch))
        self.external_idle = Gtk.SpinButton.new_with_range(0, 3600, 30)
        box.append(self.setting_row("Heavy runtime idle timeout", "Seconds before idle Qwen or MOSS processes release RAM. Zero keeps them loaded until the broker exits.", self.external_idle))
        self.model_cache = Gtk.SpinButton.new_with_range(0, 8, 1)
        box.append(self.setting_row("Warm local models", "0 = Automatic: one below 8 GiB RAM, otherwise two. A positive value overrides it.", self.model_cache))
        self.audio_cache = Gtk.SpinButton.new_with_range(0, 1024, 16)
        box.append(self.setting_row("Cloud audio cache (MB)", "Zero disables reuse; cached utterances avoid repeat API calls.", self.audio_cache))
        box.append(Gtk.Separator())
        language_heading = Gtk.Label(label="Language routing", xalign=0); language_heading.add_css_class("heading")
        box.append(language_heading)
        self.language_characters = Gtk.SpinButton.new_with_range(10, 500, 5)
        box.append(self.setting_row("Detection minimum characters", "Shorter text uses the configured default language instead of an unreliable guess. Recommended: 40.", self.language_characters))
        self.language_confidence = Gtk.SpinButton.new_with_range(.5, 1, .05); self.language_confidence.set_digits(2)
        box.append(self.setting_row("Detection confidence", "Higher values reduce incorrect automatic language changes. Recommended: 0.80.", self.language_confidence))
        self.cross_language = Gtk.Switch(); self.cross_language.connect("state-set", self.set_boolean, "cross-language-fallback")
        box.append(self.setting_row("Allow cross-language fallback", "If no compatible voice works, permit the global fallback voice rather than failing silently.", self.cross_language))
        apply_advanced = Gtk.Button(label="Apply advanced settings", halign=Gtk.Align.END)
        apply_advanced.connect("clicked", self.apply_advanced); box.append(apply_advanced)
        advanced.set_child(box); self.append(advanced)

        tools = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE); tools.add_css_class("boxed-list")
        doctor = Gtk.Button(label="Run diagnostics"); doctor.connect("clicked", self.run_diagnostics)
        tools.append(self.action_row("Diagnostics", "Check broker, Speech Dispatcher, models, ffmpeg, and selection tools.", doctor))
        models = Gtk.Button(label="Open folder"); models.connect("clicked", self.open_models)
        tools.append(self.action_row("Downloaded models", "Open the user model storage directory.", models))
        self.system_command = self.find_system_settings()
        if self.system_command:
            system = Gtk.Button(label="Open settings"); system.connect("clicked", self.open_system)
            tools.append(self.action_row("System text-to-speech", "Open the desktop environment's TTS settings panel.", system))
        self.append(tools)
        self.load()

    @staticmethod
    def setting_row(title, detail, control):
        row = Gtk.Box(spacing=8); label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        label.set_markup(f"<b>{html.escape(title)}</b>\n<small>{html.escape(detail)}</small>"); row.append(label); row.append(control)
        return row

    action_row = setting_row

    def load(self):
        def work():
            try: result = (run_json("catalog"), run_json("settings-schema"))
            except Exception as error: GLib.idle_add(self.window.alert, "Could not load settings", str(error)); return
            GLib.idle_add(self.loaded, *result)
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, catalog, schema):
        self.loading = True
        self.provider_fields = {item["id"]: item.get("fields", []) for item in schema.get("providers", [])}
        enabled = {item["id"]: item.get("enabled", False) for item in catalog.get("providers", [])}
        for provider, widget in self.provider_switches.items(): widget.set_active(bool(enabled.get(provider)))
        playback = schema.get("playback", {})
        self.auto_language.set_active(bool(playback.get("autoDetectLanguage", {}).get("value", True)))
        self.preload_voice.set_active(bool(playback.get("preloadDefaultVoice", {}).get("value", False)))
        self.playback_speed.set_value(playback.get("playbackSpeed", {}).get("value", 1.0))
        self.local_threads.set_value(playback.get("localThreads", {}).get("value", 0))
        self.pocket_threads.set_value(playback.get("pocketThreads", {}).get("value", 0))
        self.silence_scale.set_value(playback.get("localSilenceScale", {}).get("value", .2))
        self.pocket_steps.set_value(playback.get("pocketNumSteps", {}).get("value", 3))
        self.pocket_chunk.set_value(playback.get("pocketChunkSize", {}).get("value", 4))
        self.zipvoice_steps.set_value(playback.get("zipvoiceNumSteps", {}).get("value", 4))
        self.moss_threads.set_value(playback.get("mossThreads", {}).get("value", 0))
        self.moss_batch.set_value(playback.get("mossBatchFrames", {}).get("value", 4))
        self.external_idle.set_value(playback.get("externalIdleSeconds", {}).get("value", 120))
        self.model_cache.set_value(playback.get("maxLoadedModels", {}).get("value", 0))
        self.audio_cache.set_value(playback.get("audioCacheMb", {}).get("value", 64))
        self.language_characters.set_value(playback.get("languageMinimumCharacters", {}).get("value", 40))
        self.language_confidence.set_value(playback.get("languageMinimumConfidence", {}).get("value", .8))
        self.cross_language.set_active(bool(playback.get("crossLanguageFallback", {}).get("value", True)))
        self.loading = False; return GLib.SOURCE_REMOVE

    def toggle_provider(self, widget, state, provider):
        if self.loading: return False
        widget.set_sensitive(False)
        self.window.run_task(command("provider", "enable" if state else "disable", provider),
                             f"{provider} {'enabled' if state else 'disabled'}",
                             lambda: (widget.set_sensitive(True), self.window.voices.load()))
        return False

    def set_boolean(self, _widget, state, name):
        if not self.loading: self.window.run_task(command("setting", name, str(bool(state)).lower()), "Setting saved")
        return False

    def set_number(self, widget, name):
        if not self.loading: self.window.run_task(command("setting", name, str(widget.get_value_as_int())), "Setting saved")

    def apply_advanced(self, *_args):
        def work():
            values = (("playback-speed", self.playback_speed.get_value()),
                      ("local-threads", self.local_threads.get_value_as_int()),
                      ("pocket-threads", self.pocket_threads.get_value_as_int()),
                      ("local-silence-scale", self.silence_scale.get_value()),
                      ("pocket-num-steps", self.pocket_steps.get_value_as_int()),
                      ("pocket-chunk-size", self.pocket_chunk.get_value_as_int()),
                      ("zipvoice-num-steps", self.zipvoice_steps.get_value_as_int()),
                      ("moss-threads", self.moss_threads.get_value_as_int()),
                      ("moss-batch-frames", self.moss_batch.get_value_as_int()),
                      ("external-idle-seconds", self.external_idle.get_value_as_int()),
                      ("max-loaded-models", self.model_cache.get_value_as_int()),
                      ("audio-cache-mb", self.audio_cache.get_value_as_int()),
                      ("language-minimum-characters", self.language_characters.get_value_as_int()),
                      ("language-minimum-confidence", self.language_confidence.get_value()))
            results = [subprocess.run(command("setting", name, str(value), "--defer-restart"), text=True, capture_output=True)
                       for name, value in values]
            error = next((item.stderr.strip() or item.stdout.strip() for item in results if item.returncode), "")
            if not error:
                reloaded = subprocess.run(command("reload"), text=True, capture_output=True)
                if reloaded.returncode: error = reloaded.stderr.strip() or reloaded.stdout.strip()
            GLib.idle_add(self.window.alert, "Could not save settings" if error else "Settings saved", error)
        threading.Thread(target=work, daemon=True).start()

    def run_diagnostics(self, *_args):
        def work():
            result = subprocess.run(command("doctor"), text=True, capture_output=True)
            GLib.idle_add(self.window.alert, "Diagnostics passed" if result.returncode == 0 else "Diagnostics found a problem",
                          (result.stdout + result.stderr).strip())
        threading.Thread(target=work, daemon=True).start()

    def open_models(self, *_args):
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "uttermux/models"
        path.mkdir(parents=True, exist_ok=True); Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)

    @staticmethod
    def find_system_settings():
        if GLib.find_program_in_path("kcmshell6"):
            result = subprocess.run(["kcmshell6", "--list"], text=True, capture_output=True)
            module = next((line.split()[0] for line in result.stdout.splitlines()
                           if "speech" in line.casefold() or "text-to-speech" in line.casefold()), "")
            if module: return ["kcmshell6", module]
        if GLib.find_program_in_path("gnome-control-center"):
            return ["gnome-control-center", "accessibility"]
        return None

    def open_system(self, *_args):
        if self.system_command: subprocess.Popen(self.system_command)

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

    def configure_provider(self, _button, provider, title):
        fields = self.provider_fields.get(provider, [])
        if not fields:
            self.window.alert("Provider metadata unavailable", "Reload Settings and try again."); return
        dialog = Gtk.Dialog(title=f"Configure {title}", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        entries = {}
        for field in fields:
            entry = Gtk.Entry(placeholder_text=("Leave blank to keep saved value" if field.get("type") == "secret" else field.get("default", "")))
            if field.get("type") == "secret": entry.set_visibility(False)
            elif field.get("value"): entry.set_text(str(field["value"]))
            label = Gtk.Label(label=field.get("label", field["id"]), xalign=0)
            box.append(label); box.append(entry); entries[field["id"]] = (entry, field.get("type") == "secret")
        dialog.get_content_area().append(box)
        def response(_dialog, value):
            if value == Gtk.ResponseType.ACCEPT:
                payload = {name: entry.get_text() for name, (entry, secret) in entries.items()
                           if entry.get_text() or not secret}
                def work():
                    process = subprocess.run(command("provider-config", provider), input=json.dumps(payload),
                                             text=True, capture_output=True)
                    GLib.idle_add(self.window.alert, "Could not save provider" if process.returncode else "Provider saved",
                                  (process.stderr.strip() or process.stdout.strip()) if process.returncode else "Enable it to load its voice catalog.")
                threading.Thread(target=work, daemon=True).start()
            dialog.destroy()
        dialog.connect("response", response); dialog.present()


class TunePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        self.window, self.running = window, False
        heading = Gtk.Label(label="Test and tune local models", xalign=0); heading.add_css_class("title-2"); self.append(heading)
        self.append(Gtk.Label(label="Preview checks that a voice sounds correct. Benchmark measures startup, throughput, memory, and CPU thread choices for each installed artifact; it does not judge speech quality.", xalign=0, wrap=True))
        self.status = Gtk.Label(label="Loading installed models…", xalign=0, wrap=True); self.append(self.status)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE); self.rows.add_css_class("boxed-list"); self.append(self.rows)
        refresh = Gtk.Button(label="Refresh"); refresh.connect("clicked", lambda *_: self.load()); self.append(refresh)
        self.load()

    def load(self):
        def work():
            try: result = (run_json("catalog"), run_json("tuning", "list"), run_json("model-setting", "list"), run_json("settings-schema"))
            except Exception as error: GLib.idle_add(self.loaded, None, {}, {}, {}, str(error)); return
            GLib.idle_add(self.loaded, result[0], result[1], result[2], result[3], "")
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, catalog, tuning, overrides, settings_schema, error):
        while child := self.rows.get_first_child(): self.rows.remove(child)
        if error: self.status.set_text(error); return GLib.SOURCE_REMOVE
        models = {item["id"]: item for item in catalog.get("models", [])}
        seen, entries = set(), []
        for voice in catalog.get("voices", []):
            model = models.get(voice.get("modelId"), {})
            if (not voice.get("ready") or model.get("location") != "on-device" or
                    model.get("providerId") not in {"local", "moss", "qwen"} or model.get("id") in seen): continue
            seen.add(model["id"]); entries.append((model, voice))
        self.status.set_text(f"{len(entries)} installed local artifact{'s' if len(entries) != 1 else ''}")
        for model, voice in sorted(entries, key=lambda item: (item[0].get("family", ""), item[0].get("name", ""))):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            row.append(Gtk.Label(label=model.get("name", model["id"]), xalign=0, wrap=True))
            facts = " · ".join(filter(None, (model.get("family"), model.get("quantization"), model.get("version"))))
            row.append(Gtk.Label(label=facts, xalign=0, wrap=True))
            controls = Gtk.Box(spacing=8); provider = model.get("providerId", "local")
            applied = int(tuning.get(model["id"], {}).get("threads", 0)) if provider == "local" else 0
            model_values = overrides.get(model["id"], {})
            if model_values.get("threads"):
                state_text = f"{model_values['threads']} threads · Model override"
            elif applied:
                state_text = f"{applied} threads · Tuned"
            else:
                state_text = "Inherits global default or automatic choice"
            if provider != "local": state_text = "Model override" if model_values else "Companion runtime · global defaults"
            state = Gtk.Label(label=state_text, xalign=0, hexpand=True, wrap=True)
            controls.append(state)
            preview_spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER); controls.append(preview_spinner)
            preview = Gtk.Button(label="Preview"); preview.connect("clicked", self.preview, voice, preview_spinner); controls.append(preview)
            use = Gtk.Button(label="Use as active voice")
            use.connect("clicked", self.activate_voice, voice); controls.append(use)
            run = Gtk.Button(label="Benchmark" if provider == "local" else "Benchmark current settings")
            run.connect("clicked", self.benchmark if provider == "local" else self.confirm_companion_benchmark, model, voice); controls.append(run)
            settings = Gtk.Button(label="Model settings…")
            settings.connect("clicked", self.model_settings, model, model_values,
                             tuning.get(model["id"], {}), settings_schema.get("playback", {})); controls.append(settings)
            if provider == "local":
                reset = Gtk.Button(label="Reset", sensitive=applied > 0); reset.connect("clicked", self.reset, model["id"]); controls.append(reset)
            row.append(controls); self.rows.append(row)
        return GLib.SOURCE_REMOVE

    def preview(self, button, voice, spinner):
        if self.running: return
        self.running = True; button.set_sensitive(False); button.set_label("Loading / playing…")
        spinner.set_visible(True); spinner.start(); self.status.set_text(f"Loading {voice['name']} preview…")
        def work():
            result = subprocess.run(command("preview", voice["id"]), text=True, capture_output=True)
            GLib.idle_add(done, result)
        def done(result):
            self.running = False; spinner.stop(); spinner.set_visible(False)
            button.set_sensitive(True); button.set_label("Preview")
            self.status.set_text((f"Preview completed for {voice['name']}" if result.returncode == 0 else
                                  result.stderr.strip() or "Preview failed"))
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def activate_voice(self, _button, voice):
        self.window.run_task(command("default", voice["id"]),
                             f"Active voice: {voice['name']}", self.load)

    def benchmark(self, button, model, voice):
        if self.running: return
        self.running = True; button.set_sensitive(False); self.status.set_text(f"Benchmarking {model['id']}…")
        def work():
            result = subprocess.run(command("tune", voice["id"], "--json"), text=True, capture_output=True)
            GLib.idle_add(done, result)
        def done(result):
            self.running = False; button.set_sensitive(True)
            if result.returncode:
                self.status.set_text(result.stderr.strip() or "Benchmark failed"); return GLib.SOURCE_REMOVE
            report = json.loads(result.stdout); winner = report["winner"]
            winner_group = next((group for group in report.get("candidates", [])
                                 if group.get("threads") == winner["threads"]), {})
            runs = winner_group.get("runs", [])
            cold = next((run for run in runs if run.get("cold")), None)
            candidate_lines = [
                f"{group['threads']} thread{'s' if group['threads'] != 1 else ''}: "
                f"RTF {group['medianRtf']:.3f}, first PCM {group['medianFirstAudioMs']:.0f} ms, "
                f"peak {group['peakRssMb']} MB"
                for group in report.get("candidates", [])]
            metrics = [
                f"Recommended: {winner['threads']} threads · {report['classification']}",
                f"Warm/median first PCM: {winner['medianFirstAudioMs']:.0f} ms",
                f"Peak broker memory: {winner['peakRssMb']} MB",
            ]
            if cold: metrics.append(f"Cold first PCM: {cold['firstAudioMs']:.0f} ms")
            dialog = Gtk.AlertDialog(message="Apply tuned profile?", detail=(
                f"{model.get('name', model['id'])}\n" + "\n".join(metrics) +
                "\n\nCandidates\n" + "\n".join(candidate_lines) + "\n\n"
                "This measures performance, not voice quality, and does not change the model variant. "
                "A manual thread value in Model settings remains higher priority than this tuned profile."),
                buttons=["Keep current", "Apply"], cancel_button=0, default_button=1)
            def chosen(_dialog, task):
                try: choice = _dialog.choose_finish(task)
                except GLib.Error: choice = 0
                if choice == 1:
                    self.window.run_task(command("tuning", "apply", model["id"], str(winner["threads"])),
                                         "Tuned profile applied", self.load)
                else: self.load()
            dialog.choose(self.window, None, chosen); return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def confirm_companion_benchmark(self, button, model, voice):
        estimated = int(model.get("estimatedRamMb", 0))
        dialog = Gtk.AlertDialog(message=f"Benchmark {model.get('name', model['id'])}?", detail=(
            f"This runs three syntheses using the current companion settings and may use approximately "
            f"{estimated or 'the documented'} MB RAM. UtterMux will refuse MOSS or Qwen if available "
            "memory is below the model estimate plus a 2 GB reserve. No setting is applied automatically."),
            buttons=["Cancel", "Benchmark"], cancel_button=0, default_button=1)
        def chosen(_dialog, task):
            try: choice = _dialog.choose_finish(task)
            except GLib.Error: choice = 0
            if choice == 1: self.benchmark_companion(button, model, voice)
        dialog.choose(self.window, None, chosen)

    def benchmark_companion(self, button, model, voice):
        if self.running: return
        self.running = True; button.set_sensitive(False); self.status.set_text(f"Benchmarking {model['id']} at current settings…")
        def work():
            result = subprocess.run(command("benchmark", voice["id"], "--runs", "3", "--json", "--save"),
                                    text=True, capture_output=True)
            GLib.idle_add(done, result)
        def done(result):
            self.running = False; button.set_sensitive(True)
            if result.returncode:
                self.status.set_text(result.stderr.strip() or "Benchmark failed"); return GLib.SOURCE_REMOVE
            report = json.loads(result.stdout); summary = report["summary"]
            runs = "\n".join(f"Run {index}: first PCM {run['firstAudioMs']:.0f} ms, "
                             f"RTF {run['rtf']:.3f}, {run['audioSeconds']:.2f} s audio"
                             for index, run in enumerate(report.get("runs", []), 1))
            Gtk.AlertDialog(message="Benchmark completed", detail=(
                f"{model.get('name', model['id'])}\nMedian RTF: {summary['medianRtf']:.3f}\n"
                f"Median first PCM: {summary['medianFirstAudioMs']:.0f} ms\n"
                f"Continuous reading: {'yes' if summary['continuousReading'] else 'no'}\n\n{runs}\n\n"
                "This measured the current companion configuration; it did not apply a setting."),
                buttons=["Close"]).show(self.window)
            self.status.set_text(f"Benchmark completed for {model.get('name', model['id'])}")
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def reset(self, _button, model_id):
        self.window.run_task(command("tuning", "reset", model_id), "Tuned profile removed", self.load)

    def model_settings(self, _button, model, current, tuning, playback):
        dialog = Gtk.Dialog(title=f"{model.get('name', model['id'])} settings", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        content.append(Gtk.Label(label=("These settings apply only to this installed artifact. "
            "Each row identifies the value used when its override is disabled."),
            xalign=0, wrap=True))
        engine = model.get("engine", "").casefold(); global_threads = (
            "pocketThreads" if engine == "pocket" else "mossThreads" if engine == "moss" else
            "" if model.get("providerId") == "qwen" else "localThreads")
        fields = [
            ("threads", "CPU threads", 1, 16, 1, 2, global_threads),
        ]
        if model.get("providerId") == "local":
            fields += [("silence_scale", "Generated silence scale", 0, 2, .05, .2, "localSilenceScale")]
        if engine == "pocket": fields += [("pocket_num_steps", "Pocket refinement steps", 1, 8, 1, 3, "pocketNumSteps"),
                                            ("pocket_chunk_size", "Pocket decoder chunk", 1, 16, 1, 4, "pocketChunkSize")]
        if engine == "zipvoice": fields += [("zipvoice_num_steps", "ZipVoice generation steps", 1, 8, 1, 4, "zipvoiceNumSteps")]
        if engine == "moss" or model.get("providerId") == "moss":
            fields += [("moss_batch_frames", "MOSS batch frames", 1, 16, 1, 4, "mossBatchFrames")]
        widgets = {}
        for key, label, low, high, step, default, global_key in fields:
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            block.append(Gtk.Label(label=label, xalign=0, css_classes=["heading"]))
            global_value = playback.get(global_key, {}).get("value", default)
            if key == "threads" and int(tuning.get("threads", 0)) > 0:
                base_value, source = int(tuning["threads"]), "saved benchmark profile"
            elif key == "threads" and int(global_value) == 0:
                base_value, source = "automatic", "automatic policy"
            else:
                base_value, source = global_value, "global default"
            effective = current.get(key, base_value); effective_source = "model override" if key in current else source
            block.append(Gtk.Label(label=f"Effective: {effective} · {effective_source}", xalign=0, wrap=True))
            line = Gtk.Box(spacing=10); enabled = Gtk.CheckButton(label="Override for this model", hexpand=True)
            enabled.set_active(key in current); line.append(enabled)
            adjustment = Gtk.Adjustment(value=float(current.get(key, default)), lower=low, upper=high,
                                        step_increment=step, page_increment=step)
            spin = Gtk.SpinButton(adjustment=adjustment, digits=2 if isinstance(step, float) else 0)
            spin.set_sensitive(enabled.get_active()); enabled.connect("toggled", lambda check, target=spin: target.set_sensitive(check.get_active()))
            reset_one = Gtk.Button(label="Reset setting", sensitive=enabled.get_active())
            enabled.connect("toggled", lambda check, target=reset_one: target.set_sensitive(check.get_active()))
            reset_one.connect("clicked", lambda _button, check=enabled: check.set_active(False))
            line.append(spin); line.append(reset_one); block.append(line); content.append(block)
            widgets[key] = (enabled, spin, isinstance(step, float))
        reset = Gtk.Button(label="Reset all settings for this model")
        reset.connect("clicked", lambda *_: [item[0].set_active(False) for item in widgets.values()])
        content.append(reset); dialog.get_content_area().append(content)
        def response(_dialog, value):
            if value == Gtk.ResponseType.ACCEPT:
                submitted = {key: (spin.get_value() if floating else spin.get_value_as_int())
                             for key, (enabled, spin, floating) in widgets.items() if enabled.get_active()}
                self.window.run_task(command("model-setting", "replace", model["id"], json.dumps(submitted)),
                                     "Model settings saved", self.load)
            dialog.destroy()
        dialog.connect("response", response); dialog.present()


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="UtterMux", default_width=920, default_height=720)
        header = Gtk.HeaderBar(); self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack); header.set_title_widget(switcher); self.set_titlebar(header)
        self.voices = VoicePage(self); self.stack.add_titled(self.voices, "voices", "Voices")
        self.stack.add_titled(self.scrolled(CreatePage(self)), "create", "Create voice")
        self.test_page = TunePage(self); self.stack.add_titled(self.scrolled(self.test_page), "tune", "Test & tune")
        self.stack.add_titled(self.scrolled(SettingsPage(self)), "settings", "Settings"); self.set_child(self.stack)
        page = os.environ.get("UTTERMUX_SCREENSHOT_PAGE", "")
        if page in {"voices", "create", "tune", "settings"}: self.stack.set_visible_child_name(page)

    def show_test_page(self):
        self.stack.set_visible_child_name("tune")

    @staticmethod
    def scrolled(child):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(child)
        return scroll

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

    def alert(self, message, detail=""):
        Gtk.AlertDialog(message=message, detail=detail).show(self)
        return GLib.SOURCE_REMOVE


class Application(Gtk.Application):
    def __init__(self): super().__init__(application_id="io.uttermux.App", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    def do_activate(self):
        window = self.get_active_window()
        if not window: window = Window(self)
        window.present()


if __name__ == "__main__": raise SystemExit(Application().run([]))
