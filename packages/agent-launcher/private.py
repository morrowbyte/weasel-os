"""Executed by weasel-workspace open, with its private home, bus and display."""
import os
from pathlib import Path
import sys

entry, bindir, datadir = sys.argv[1:]
os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
os.environ["XDG_DATA_DIRS"] = datadir
# GLib caches the search paths on first use.
import gi

gi.require_version("GioUnix", "2.0")
from gi.repository import GioUnix, GLib

# The isolated bus has no host activation services. Use the desktop entry's
# Exec fallback and let GIO expand its field codes, quoting and launch context.
info = GioUnix.DesktopAppInfo.new_from_filename(entry)
if info.get_boolean("DBusActivatable"):
    keyfile = GLib.KeyFile.new()
    keyfile.load_from_file(entry, GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    keyfile.set_boolean("Desktop Entry", "DBusActivatable", False)
    private_entry = Path.home() / ".local/share/applications" / Path(entry).name
    private_entry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    keyfile.save_to_file(str(private_entry))
    info = GioUnix.DesktopAppInfo.new_from_filename(str(private_entry))
info.launch([], None)
