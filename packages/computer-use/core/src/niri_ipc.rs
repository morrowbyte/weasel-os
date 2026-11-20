//! Small, fresh Niri inventory reads. One socket per request: no inventory
//! cache and no state shared across preflight, dispatch and post-observation.
use super::{begin_acquisition, check_epoch, niri_epoch, State, R};
use serde_json::Value;
use std::{
    env,
    io::{BufRead, BufReader, ErrorKind, Write},
    mem::{offset_of, size_of, zeroed},
    os::{
        fd::FromRawFd,
        unix::{ffi::OsStrExt, net::UnixStream},
    },
    path::Path,
    time::{Duration, Instant},
};

// A blocking AF_UNIX connect can wait indefinitely on a full accept queue.
// Use a nonblocking socket from creation. On backlog pressure fail before dispatch.
pub(super) fn connect(path: &Path) -> Result<UnixStream, &'static str> {
    let bytes = path.as_os_str().as_bytes();
    // SAFETY: sockaddr_un's integer and byte-array fields admit all-zero values.
    let mut addr: libc::sockaddr_un = unsafe { zeroed() };
    if !path.is_absolute() || bytes.contains(&0) || bytes.len() >= addr.sun_path.len() {
        return Err("Niri IPC connection unavailable");
    }
    addr.sun_family = libc::AF_UNIX as _;
    for (dst, &byte) in addr.sun_path.iter_mut().zip(bytes) {
        *dst = byte as _;
    }
    // SAFETY: socket takes no pointers. On success stream takes sole ownership.
    let fd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if fd < 0 {
        return Err("Niri IPC connection unavailable");
    }
    let stream = unsafe { UnixStream::from_raw_fd(fd) };
    let len = offset_of!(libc::sockaddr_un, sun_path) + bytes.len() + 1;
    debug_assert!(len <= size_of::<libc::sockaddr_un>());
    // SAFETY: initialized AF_UNIX address with the byte count including its terminator.
    let result = unsafe { libc::connect(fd, (&raw const addr).cast(), len as libc::socklen_t) };
    if result != 0 {
        return Err("Niri IPC connection unavailable");
    }
    Ok(stream)
}

#[derive(Clone, Copy)]
pub(super) enum Inventory { Windows, Workspaces, Outputs }

impl Inventory {
    fn name(self) -> &'static str {
        match self {
            Self::Windows => "Windows",
            Self::Workspaces => "Workspaces",
            Self::Outputs => "Outputs",
        }
    }

    pub(super) fn read(self, state: &State, epoch: u64) -> R<Value> {
        state.observation_policy.require_enabled()?;
        check_epoch(state, Some(epoch))?;
        let Some(path) = env::var_os("NIRI_SOCKET") else {
            return niri_epoch(state, &self.name().to_ascii_lowercase(), Some(epoch));
        };
        let _acquiring = match self {
            Self::Windows => Some(begin_acquisition(state)?),
            _ => None,
        };
        let stream = connect(Path::new(&path))?;
        self.request(stream, || check_epoch(state, Some(epoch)))
    }

    fn request(self, mut stream: UnixStream, check: impl Fn() -> R<()>) -> R<Value> {
        let deadline = Instant::now() + Duration::from_secs(3);
        let name = self.name();
        check()?;
        let request = format!("\"{name}\"\n");
        stream.write_all(request.as_bytes()).map_err(|_| "Could not write Niri inventory request")?;
        stream.set_nonblocking(false).map_err(|_| "Could not configure Niri inventory socket")?;
        stream.set_read_timeout(Some(Duration::from_millis(5))).map_err(|_| "Could not bound Niri inventory read")?;
        let mut reader = BufReader::new(stream);
        let mut frame = Vec::new();
        loop {
            check()?;
            if Instant::now() >= deadline { return Err("Niri inventory request timed out".into()); }
            let chunk = match reader.fill_buf() {
                Ok([]) => return Err("Niri closed the socket before completing the inventory".into()),
                Ok(chunk) => chunk,
                Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut | ErrorKind::Interrupted) => continue,
                Err(_) => return Err("Could not read Niri inventory".into()),
            };
            let newline = chunk.iter().position(|&b| b == b'\n');
            let n = newline.unwrap_or(chunk.len());
            if frame.len() + n > 32 * 1024 * 1024 { return Err("Niri inventory exceeds 32 MiB".into()); }
            frame.extend_from_slice(&chunk[..n]);
            if newline.is_some() { break; }
            reader.consume(n);
        }
        let mut reply: Result<Value, serde::de::IgnoredAny> =
            serde_json::from_slice(&frame).map_err(|_| "Invalid Niri inventory reply")?;
        let body = reply.as_mut().map_err(|_| "Niri refused the inventory request")?;
        let data = body.get_mut(name).ok_or("Niri inventory response type changed")?.take();
        check()?;
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, thread};

    #[test]
    fn response_is_bound_to_request_and_partial_reads_check_cancellation() {
        for (reply, valid) in [("{\"Ok\":{\"Windows\":[]}}\n", true), ("{\"Ok\":{\"Workspaces\":[]}}\n", false), ("{\"Err\":\"private detail\"}\n", false)] {
            let (mut server, client) = UnixStream::pair().unwrap();
            let responder = thread::spawn(move || {
                let mut request = String::new();
                BufReader::new(&server).read_line(&mut request).unwrap();
                assert_eq!(request, "\"Windows\"\n");
                server.write_all(reply.as_bytes()).unwrap();
            });
            let result = Inventory::Windows.request(client, || Ok(()));
            responder.join().unwrap();
            assert_eq!(result.is_ok(), valid);
            assert!(!format!("{result:?}").contains("private detail"));
        }
        let (mut server, client) = UnixStream::pair().unwrap();
        server.write_all(b"{\"Ok\":").unwrap();
        let checks = Cell::new(0);
        let result = Inventory::Windows.request(client, || {
            checks.set(checks.get() + 1);
            if checks.get() > 3 { Err("cancelled".into()) } else { Ok(()) }
        });
        assert_eq!(result.unwrap_err(), "cancelled");
    }
}
