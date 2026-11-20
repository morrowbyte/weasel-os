"""One explicitly requested local view of the running private compositor."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile


def open_window(paths, epoch):
    """Transfer the broker connection and one connected RFB fd to the host widget."""
    with socket.socket(socket.AF_UNIX) as control:
        control.settimeout(8)
        control.connect(str(paths.sock))
        control.sendall(json.dumps({"op": "view", "epoch": epoch}).encode() + b"\n")
        data, fds, flags, _ = socket.recv_fds(control, 4096, 1, socket.MSG_CMSG_CLOEXEC)
        try:
            if data != b"V" or len(fds) != 1 or flags & socket.MSG_CTRUNC:
                while data and not data.endswith(b"\n") and len(data) < 4096:
                    chunk = control.recv(4096 - len(data))
                    if not chunk:
                        break
                    data += chunk
                raise RuntimeError(json.loads(data)["error"] if data else "view connection closed")
            control.settimeout(None)
            for fd in (control.fileno(), *fds):
                os.set_inheritable(fd, True)
            viewer = Path(__file__).with_name("workspace-view")
            os.execv(viewer, [str(viewer), paths.name, str(control.fileno()), str(fds[0])])
        finally:
            for fd in fds:
                os.close(fd)


class Presentation:
    def __init__(self, epoch):
        self.epoch = epoch
        self.manual = False
        self.revoked = asyncio.Event()
        self.closed = asyncio.Event()

    def admitted(self, broker):
        state = broker.gate()
        return not state["paused"] and state["epoch"] == self.epoch

    async def revoke(self):
        self.revoked.set()
        await self.closed.wait()

    async def watch(self, broker):
        while self.admitted(broker) and not self.revoked.is_set():
            try:
                await asyncio.wait_for(self.revoked.wait(), 0.05)
            except TimeoutError:
                pass

    async def focus(self, broker, reader, writer):
        while event := await reader.read(1):
            if event not in (b"F", b"B") or not self.admitted(broker):
                return
            self.manual = event == b"F" and broker.active is None
            writer.write(b"F" if self.manual else b"B")
            await writer.drain()

    async def serve(self, broker, reader, writer):
        proc = None
        tasks = []
        try:
            # Queue exactly our client and unlink the pathname before starting
            # the server. No address or host file is exposed to the viewer.
            with tempfile.TemporaryDirectory(dir="/tmp") as directory, \
                    socket.socket(socket.AF_UNIX) as listener, socket.socket(socket.AF_UNIX) as peer:
                path = str(Path(directory, "rfb"))
                listener.bind(path)
                listener.listen(1)
                peer.connect(path)
                os.unlink(path)
                proc = await asyncio.create_subprocess_exec(
                    "@wayvnc@", "--config", "/dev/null", "--output", "HEADLESS-1",
                    "--seat", "seat0", "--disable-resizing", "--exit-on-disconnect",
                    "--socket", str(Path(directory, "ctl")), f"fd:{listener.fileno()}",
                    pass_fds=(listener.fileno(),), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL)
                listener.close()
                if not self.admitted(broker) or self.revoked.is_set():
                    raise RuntimeError("view admission expired")
                with writer.get_extra_info("socket").dup() as control:
                    socket.send_fds(control, [b"V"], [peer.fileno()])
                peer.close()
                tasks = [asyncio.create_task(work) for work in (
                    proc.wait(), self.focus(broker, reader, writer), self.watch(broker))]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            broker.view = None
            self.closed.set()
