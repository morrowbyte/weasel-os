import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import private_clipboard as clipboard


class ClipboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_requests_only_advertised_plain_text_keeps_final_newline(self):
        data = "Friday.\r\nGrüße 👋\n".encode()
        with patch.object(clipboard, "paste", side_effect=[b"text/html\nUTF8_STRING\n", data]) as paste:
            result = await clipboard.read_text()
            self.assertEqual(result["text"].encode(), data)
            self.assertEqual(result["bytes"], len(data))
            self.assertEqual(paste.await_args.args, ("--no-newline", "--type", "UTF8_STRING"))
        with patch.object(clipboard, "paste", return_value=b"text/html\nimage/png\n") as paste:
            with self.assertRaisesRegex(ValueError, "plain text"):
                await clipboard.read_text()
            paste.assert_awaited_once()
        with patch.object(clipboard, "paste", side_effect=[b"UTF8_STRING\n", b"\xff"]):
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                await clipboard.read_text()

    async def test_overflow_kills_exact_paste_process_without_returning_prefix(self):
        stdout = asyncio.StreamReader()
        stdout.feed_data(b"X" * 9)
        proc = Mock(stdout=stdout, returncode=None, wait=AsyncMock(return_value=-9))
        with patch.object(asyncio, "create_subprocess_exec", return_value=proc):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                await clipboard.paste("--no-newline", limit=8)
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_owned_foreground_source_is_reaped_on_revocation(self):
        service = clipboard.Clipboard()
        admitted = Mock(return_value=True)
        stdin = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        proc = Mock(stdin=stdin, returncode=None, wait=AsyncMock(return_value=-9))
        proc.kill.side_effect = lambda: setattr(proc, "returncode", -9)
        with patch.object(asyncio, "create_subprocess_exec", return_value=proc) as spawn, \
             patch.object(clipboard, "read_text", return_value={"text": "Grüße\n"}):
            result = await service.write("Grüße\n", "job", admitted)
            self.addAsyncCleanup(service.close)
            self.assertEqual(result, {"action": "clipboard-write", "bytes": 8})
            self.assertIn("--foreground", spawn.await_args.args)
            self.assertNotIn("Grüße\n", spawn.await_args.args)
            stdin.write.assert_called_once_with("Grüße\n".encode())
            directory = Path(spawn.await_args.kwargs["env"]["TMPDIR"])
            self.assertTrue(directory.is_dir())
            await service.close(job="other")
            proc.kill.assert_not_called()
            admitted.return_value = False
            await service.close(revoked_only=True)
            proc.kill.assert_called_once()
            proc.wait.assert_awaited_once()
            self.assertFalse(directory.exists())
