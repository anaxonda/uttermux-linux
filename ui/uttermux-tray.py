#!/usr/bin/env python3
"""Small StatusNotifierItem service for opening and controlling UtterMux."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from gi.repository import Gio, GLib

CLI = Path(__file__).resolve().with_name("uttermux")
APP = Path(__file__).resolve().with_name("uttermux-app")
if not CLI.exists(): CLI = Path(__file__).resolve().parents[1] / "cli/uttermux"
if not APP.exists(): APP = Path(__file__).resolve().parents[1] / "ui/uttermux-app.py"
if not CLI.exists(): CLI = Path("/usr/bin/uttermux")
if not APP.exists(): APP = Path("/usr/bin/uttermux-app")
ITEM_PATH, MENU_PATH = "/StatusNotifierItem", "/MenuBar"

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="u" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/><property name="OverlayIconName" type="s" access="read"/>
<property name="AttentionIconName" type="s" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
<signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewStatus"><arg type="s"/></signal><signal name="NewToolTip"/>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""

MENU_ITEMS = {1: "Open UtterMux", 2: "Read selection", 3: "Stop speech", 4: "Quit tray"}


class Tray:
    def __init__(self):
        self.loop = GLib.MainLoop(); self.connection = None; self.title = "UtterMux"
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

    def properties(self, _connection, _sender, _path, interface, name):
        if interface == "org.kde.StatusNotifierItem":
            values = {"Category": GLib.Variant("s", "ApplicationStatus"), "Id": GLib.Variant("s", "uttermux"),
                "Title": GLib.Variant("s", self.title), "Status": GLib.Variant("s", "Active"),
                "WindowId": GLib.Variant("u", 0), "IconName": GLib.Variant("s", "io.uttermux.Tray"),
                "IconPixmap": GLib.Variant("a(iiay)", []), "OverlayIconName": GLib.Variant("s", ""),
                "AttentionIconName": GLib.Variant("s", ""),
                "ToolTip": GLib.Variant("(sa(iiay)ss)", ("io.uttermux.Tray", [], "UtterMux", self.title)),
                "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", MENU_PATH)}
        else:
            values = {"Version": GLib.Variant("u", 4), "TextDirection": GLib.Variant("s", "ltr"),
                      "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}
        return values.get(name)

    @staticmethod
    def menu_properties(item_id):
        if item_id == 0: return {"children-display": GLib.Variant("s", "submenu")}
        return {"label": GLib.Variant("s", MENU_ITEMS[item_id]), "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True)}

    def layout(self):
        children = [GLib.Variant("(ia{sv}av)", (item_id, self.menu_properties(item_id), []))
                    for item_id in MENU_ITEMS]
        return (0, self.menu_properties(0), children)

    def activate(self, item_id=1):
        if item_id == 1: subprocess.Popen([str(APP)])
        elif item_id == 2: subprocess.Popen([str(CLI), "speak-selection"])
        elif item_id == 3: subprocess.run(["spd-say", "--cancel"], check=False)
        elif item_id == 4: self.loop.quit()

    def item_method(self, _connection, _sender, _path, _interface, method, _params, invocation):
        if method in {"Activate", "ContextMenu"}: self.activate(1)
        elif method == "SecondaryActivate": subprocess.run(["spd-say", "--cancel"], check=False)
        invocation.return_value(None)

    def menu_method(self, _connection, _sender, _path, _interface, method, params, invocation):
        if method == "GetLayout": invocation.return_value(GLib.Variant("(u(ia{sv}av))", (1, self.layout())))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack(); rows = [(item, self.menu_properties(item)) for item in ids if item in MENU_ITEMS or item == 0]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
        elif method == "GetProperty":
            item, name = params.unpack(); value = self.menu_properties(item).get(name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item, event, _data, _timestamp = params.unpack()
            if event == "clicked": self.activate(item)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item, event, _data, _timestamp in params.unpack():
                if event == "clicked": self.activate(item)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow": invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup": invocation.return_value(GLib.Variant("(aiai)", ([], [])))

    def refresh(self):
        try:
            status = json.loads(subprocess.run([str(CLI), "status"], text=True, capture_output=True, timeout=3, check=True).stdout)
            voice = status.get("activeVoice") or status.get("configuredDefault") or "No active voice"
            title = f"{voice}\n{status.get('status', 'idle').title()}"
        except Exception as error: title = f"Broker unavailable: {error}"
        if title != self.title and self.connection:
            self.title = title
            self.connection.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
            self.connection.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewTitle", None)
        return GLib.SOURCE_CONTINUE

    def bus_acquired(self, connection, _name):
        self.connection = connection
        item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        connection.register_object(ITEM_PATH, item_info, self.item_method, self.properties, None)
        connection.register_object(MENU_PATH, menu_info, self.menu_method, self.properties, None)
        self.refresh(); GLib.timeout_add_seconds(10, self.refresh)

    def name_acquired(self, connection, _name):
        # The watcher may resolve a well-known service immediately. Register only
        # after D-Bus confirms that this process owns it, otherwise Waybar never
        # receives the item even though the tray process remains healthy.
        connection.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
            "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)),
            None, Gio.DBusCallFlags.NONE, 5000, None, None, None)

    def run(self):
        Gio.bus_own_name(Gio.BusType.SESSION, self.name, Gio.BusNameOwnerFlags.NONE,
                         self.bus_acquired, self.name_acquired, None)
        self.loop.run()


if __name__ == "__main__": Tray().run()
