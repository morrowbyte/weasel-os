"""Explicit text transfers on the broker's private Wayland seat."""
import asyncio
import os
import tempfile
from subprocess import DEVNULL, PIPE


MAX_TEXT_BYTES = 10 * 1024
TEXT_MIMES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain")
SEAT = ("--seat", "seat0")


def encode_text(text):
    if not isinstance(text, str) or "\0" in text:
        raise ValueError("clipboard requires UTF-8 text without NUL")
    try:
        data = text.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("clipboard requires valid UTF-8 text") from exc
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError("clipboard text exceeds 10 KiB")
    return data


def kill(process):
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


async def paste(*options, limit):
    process = await asyncio.create_subprocess_exec(
        "wl-paste", *SEAT, *options, stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, limit=limit + 1)
    try:
        async with asyncio.timeout(3):
            try:
                await process.stdout.readexactly(limit + 1)
            except asyncio.IncompleteReadError as end:
                if await process.wait() != 0:
                    raise RuntimeError("private clipboard selection unavailable") from None
                return end.partial
            raise ValueError("private clipboard transfer exceeds its byte limit")
    except TimeoutError as exc:
        raise RuntimeError("private clipboard read timed out") from exc
    finally:
        kill(process)
        await process.wait()


async def read_text():
    offered = (await paste("--list-types", limit=4096)).split(b"\n")
    mime = next((kind for kind in TEXT_MIMES if kind.encode() in offered), None)
    if mime is None:
        raise ValueError("private clipboard offers no supported plain text MIME type")
    data = await paste("--no-newline", "--type", mime, limit=MAX_TEXT_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("private clipboard contains invalid UTF-8") from exc
    encode_text(text)
    return {"action": "clipboard-read", "text": text, "mime": mime, "bytes": len(data)}


class Source:
    def __init__(self, process, directory, job, admitted):
        self.process = process
        self.directory = directory
        self.job = job
        self.admitted = admitted
        self.lifetime = asyncio.create_task(self.watch())

    async def watch(self):
        try:
            while self.process.returncode is None and self.admitted():
                await asyncio.sleep(0.05)
        finally:
            kill(self.process)
            await self.process.wait()
            self.directory.cleanup()

    async def close(self):
        kill(self.process)
        await asyncio.shield(self.lifetime)


class Clipboard:
    def __init__(self):
        self.source = None

    async def close(self, *, job=None, revoked_only=False):
        source = self.source
        if source is None or (job is not None and source.job != job):
            return
        if revoked_only and source.admitted():
            return
        self.source = None
        await source.close()

    async def write(self, text, job, admitted):
        data = encode_text(text)
        if not admitted():
            raise RuntimeError("private clipboard admission expired")
        directory = tempfile.TemporaryDirectory(prefix="clipboard-", dir="/tmp")
        process = None
        try:
            # wl-copy may spool stdin. /tmp is private tmpfs and this directory
            # is removed after the exact foreground owning process is reaped.
            process = await asyncio.create_subprocess_exec(
                "wl-copy", *SEAT, "--foreground", "--sensitive", "--type", TEXT_MIMES[0],
                stdin=PIPE, stdout=DEVNULL, stderr=DEVNULL,
                env={**os.environ, "TMPDIR": directory.name})
            async with asyncio.timeout(3):
                process.stdin.write(data)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
                await self.close()
                while admitted() and process.returncode is None:
                    try:
                        observed = await read_text()
                    except (ValueError, RuntimeError):
                        pass
                    else:
                        if observed["text"] == text and admitted() and process.returncode is None:
                            self.source = Source(process, directory, job, admitted)
                            return {"action": "clipboard-write", "bytes": len(data)}
                    await asyncio.sleep(0.025)
            raise RuntimeError("private clipboard write unconfirmed")
        except TimeoutError as exc:
            raise RuntimeError("private clipboard write timed out") from exc
        finally:
            if self.source is None or self.source.process is not process:
                if process is not None:
                    kill(process)
                    await process.wait()
                directory.cleanup()
