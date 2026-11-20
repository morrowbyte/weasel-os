"""Append to a running agent-first guest driver with machine, operator and subtest."""
import json
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


def verify_viewer_pointer() -> None:
    name = "pointer-" + uuid.uuid4().hex[:8]
    root = "/tmp/" + name
    document = root + "/pointer.txt"
    cli = ["weasel-workspace", "--name", name]
    shot = Path(machine.out_dir) / "viewer-pointer-narrow.png"
    qmp = machine.qmp_client
    assert qmp is not None

    def run(*args):
        return machine.succeed(operator(shlex.join(args))).strip()

    def wait_for(command):
        return machine.wait_until_succeeds(operator(command), timeout=20).strip()

    def save_input(expected):
        machine.send_key("ctrl-s")
        wait_for("printf %s " + shlex.quote(expected) + " | cmp -s - " + shlex.quote(document))

    def color_box(color):
        geometry = subprocess.check_output([
            "magick", str(shot), "-fuzz", "10%", "-fill", "black", "+opaque", color,
            "-fill", "white", "-opaque", color, "-format", "%@", "info:",
        ], text=True)
        box = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", geometry)
        assert box, (color, geometry)
        return tuple(map(int, box.groups()))

    def click(x, y, width, height):
        assert 0 <= x < width and 0 <= y < height
        motion: dict[str, Any] = {"events": [
            {"type": "abs", "data": {"axis": axis, "value": round(value * 32767 / (size - 1))}}
            for axis, value, size in (("x", x, width), ("y", y, height))
        ]}
        qmp.send("input-send-event", motion)
        for down in (True, False):
            button: dict[str, Any] = {"events": [
                {"type": "btn", "data": {"button": "left", "down": down}}
            ]}
            qmp.send("input-send-event", button)

    def press_count(filename):
        # Counts presses delivered by the respective compositor to this client.
        return int(run("sh", "-c", "grep -Ec 'wl_pointer[@#][0-9]+\\.button\\([^)]*, 272, 1\\)' "
                       + shlex.quote(root + "/" + filename) + " || test $? = 1"))

    workspaces = json.loads(run("niri", "msg", "--json", "workspaces"))
    original, = [w for w in workspaces if w["is_focused"]]
    empty = next(w for w in workspaces if w["output"] == original["output"] and w["active_window_id"] is None)
    def devices() -> list[dict]:
        # QMPSession.send annotates all values as strings; query-mice returns a JSON list.
        result: object = qmp.send("query-mice")["return"]
        assert isinstance(result, list) and all(isinstance(m, dict) for m in result), result
        return result

    mice = devices()
    previous, = [m for m in mice if m["current"]]
    tablet, = [m for m in mice if m["absolute"] and "tablet" in m["name"].lower()]
    run(*cli, "start", "--exchange", root)
    try:
        machine.send_monitor_command(f"mouse_set {tablet['index']}")
        assert any(m["index"] == tablet["index"] and m["current"] for m in devices())
        run("niri", "msg", "action", "focus-workspace", str(empty["idx"]))
        wait_for("niri msg --json workspaces | jq -e " + shlex.quote(f".[] | select(.is_focused and .id == {empty['id']})"))
        initial = "\n" * 6 + "M" * 30 + "\n"
        ink, paper = "#ef4ba3", "#19566d"
        css = f"textview, textview text {{ color: {ink}; background-color: {paper}; font: 38px monospace; }}"
        run("mkdir", "-p", root + "/config/gtk-3.0")
        for file, content in [(document, initial), (root + "/config/gtk-3.0/gtk.css", css)]:
            run("sh", "-c", "printf %s " + shlex.quote(content) + " > " + shlex.quote(file))
        mousepad = run("readlink", "-f", run("sh", "-c", "command -v mousepad"))
        run(*cli, "open", "--", "sh", "-c", "export XDG_CONFIG_HOME=/exchange/config WAYLAND_DEBUG=client; exec "
            + shlex.join([mousepad, "--disable-server", "/exchange/pointer.txt"]) + " 2>/exchange/editor.wl")
        wait_for(shlex.join([*cli, "status"]) + """ | jq -e '.windows[] | select(.focused and (.title | contains("pointer.txt")))'""")
        run("niri", "msg", "action", "spawn", "--", "sh", "-c",
            "exec env WAYLAND_DEBUG=client " + shlex.join([*cli, "view"]) + " 2>" + root + "/viewer.wl")
        selector = f'.[] | select(.workspace_id == {empty["id"]} and .is_focused and .title == "{name} · Manuelle Eingabe")'
        viewer = json.loads(wait_for("niri msg --json windows | jq -ce " + shlex.quote(selector)))
        run("niri", "msg", "action", "set-column-width", "600")
        narrow = selector + " | select(.layout.window_size[0] <= 610 and .layout.window_size[1] > 610)"
        layout = json.loads(wait_for("niri msg --json windows | jq -ce " + shlex.quote(narrow)))["layout"]

        # Observe the rendered glyphs. Two matching samples fence the resize animation.
        deadline = time.monotonic() + 15
        prior = None
        while True:
            machine.screenshot(str(shot))
            glyphs, page = color_box(ink), color_box(paper)
            if (glyphs, page) == prior and glyphs[0] > 90 and glyphs[1] >= 5 and 490 <= page[0] <= 610:
                break
            assert time.monotonic() < deadline, ("editor colors or narrow geometry", glyphs, page, layout)
            prior = glyphs, page
            time.sleep(0.12)
        width, height = map(int, subprocess.check_output(
            ["magick", "identify", "-format", "%w %h", str(shot)], text=True).split())
        logical = json.loads(run("niri", "msg", "--json", "focused-output"))["logical"]
        assert (logical["width"], logical["height"], logical["scale"]) == (width, height, 1)
        gw, gh, gx, gy = glyphs
        # Before the eleventh M. A wrong x changes the split; a wrong y changes the line.
        x, y = round(gx + gw / 3), round(gy + gh / 2)
        gutter = round(layout["tile_pos_in_workspace_view"][1] + layout["window_offset_in_tile"][1]
                       + layout["window_size"][1] - 40)
        assert page[3] + page[1] + 60 < gutter < height, (page, gutter)
        assert (press_count("viewer.wl"), press_count("editor.wl")) == (0, 0)
        click(x, y, width, height)
        machine.send_chars("x")
        result = "\n" * 6 + "M" * 10 + "x" + "M" * 20 + "\n"
        save_input(result)
        assert (press_count("viewer.wl"), press_count("editor.wl")) == (1, 1)
        click(x, gutter, width, height)
        machine.send_chars("y")
        save_input(result.replace("x", "xy"))
        assert (press_count("viewer.wl"), press_count("editor.wl")) == (2, 1)
        machine.copy_from_machine(document)
        machine.log(f"{tablet['name']} -> Niri viewer {viewer['id']}: editor ({x},{y}), gutter "
                    f"({x},{gutter}); received presses viewer=2/editor=1; saved M*10 + xy + M*20")
    finally:
        run(*cli, "stop")
        machine.send_monitor_command(f"mouse_set {previous['index']}")
        run("niri", "msg", "action", "focus-workspace", str(original["idx"]))


with subtest("QEMU tablet hits the chosen editor character and leaves the narrow viewer gutter inert"):
    verify_viewer_pointer()
