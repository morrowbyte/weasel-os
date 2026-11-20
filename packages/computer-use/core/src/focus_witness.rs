//! Observe Niri's reported toplevel window focus around one object-targeted dispatch.
//! Start before dispatch, check just before sending (and at cooperative cancellation
//! points), and finish after the reply. One subscription stays open throughout.
//! The socket queues events during a synchronous AT-SPI call. A failure after sending
//! cannot undo the call. Niri can coalesce changes between compositor refreshes;
//! this stream has no end-of-dispatch fence. A successful finish describes only
//! complete events received through its last drain, including a short trailing
//! observation period. It says nothing about widget, layer-surface or OS input focus.

use serde::{de::IgnoredAny, Deserialize};
use std::{
    env,
    io::{BufRead, BufReader, ErrorKind, Write},
    mem::{offset_of, size_of, zeroed},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, net::UnixStream},
    },
    path::Path,
    thread,
    time::{Duration, Instant},
};

const SETUP: Duration = Duration::from_millis(300);
const DRAIN: Duration = Duration::from_millis(40);
const TRAILING: Duration = Duration::from_millis(32);
const POLL_INTERVAL: Duration = Duration::from_millis(2);
const MAX_FRAME: usize = 256 * 1024;
const MAX_TOTAL: usize = 1024 * 1024;
const MAX_RECORDS: usize = 1024;

type WitnessResult<T> = Result<T, &'static str>;
const CHANGED: &str = "Niri reported a window focus transition";
const INVALID: &str = "Niri focus event stream is invalid or unsupported";
const UNAVAILABLE: &str = "Niri focus event stream unavailable";
const LIMIT: &str = "Niri focus event stream exceeded observation bounds";
const CANCELLED: &str = "Niri focus observation cancelled";

#[derive(Deserialize)]
struct Window {
    id: u64,
    is_focused: bool,
}

#[derive(Deserialize)]
enum Event {
    WindowsChanged { windows: Vec<Window> },
    WindowFocusChanged {
        // An explicit null means focus was lost. A missing field is malformed.
        #[serde(deserialize_with = "Deserialize::deserialize")]
        id: Option<u64>,
    },
    WindowOpenedOrChanged { window: Window },
    WindowClosed { id: u64 },
    // These Niri 25.11 / 26.04 events do not update the global window focus ID.
    WorkspacesChanged(IgnoredAny),
    WorkspaceUrgencyChanged(IgnoredAny),
    WorkspaceActivated(IgnoredAny),
    WorkspaceActiveWindowChanged(IgnoredAny),
    WindowFocusTimestampChanged(IgnoredAny),
    WindowUrgencyChanged(IgnoredAny),
    WindowLayoutsChanged(IgnoredAny),
    KeyboardLayoutsChanged(IgnoredAny),
    KeyboardLayoutSwitched(IgnoredAny),
    OverviewOpenedOrClosed(IgnoredAny),
    ConfigLoaded(IgnoredAny),
    ScreenshotCaptured(IgnoredAny),
    CastsChanged(IgnoredAny),
    CastStartedOrChanged(IgnoredAny),
    CastStopped(IgnoredAny),
}

#[derive(PartialEq)]
enum Phase {
    Reply,
    Inventory,
    Armed,
}

pub(crate) struct FocusWitness {
    reader: BufReader<UnixStream>,
    partial: Vec<u8>,
    bytes: usize,
    records: usize,
    phase: Phase,
    expected: Option<u64>,
    failure: Option<&'static str>,
}

// A blocking AF_UNIX connect can wait indefinitely on a full accept queue.
// Use a nonblocking socket from creation. On backlog pressure fail before dispatch.
fn connect(path: &Path) -> WitnessResult<UnixStream> {
    let bytes = path.as_os_str().as_bytes();
    // SAFETY: sockaddr_un's integer and byte-array fields admit all-zero values.
    let mut addr: libc::sockaddr_un = unsafe { zeroed() };
    if !path.is_absolute() || bytes.contains(&0) || bytes.len() >= addr.sun_path.len() {
        return Err(UNAVAILABLE);
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
        return Err(UNAVAILABLE);
    }
    let stream = unsafe { UnixStream::from_raw_fd(fd) };
    let len = offset_of!(libc::sockaddr_un, sun_path) + bytes.len() + 1;
    debug_assert!(len <= size_of::<libc::sockaddr_un>());
    // SAFETY: initialized AF_UNIX address with the byte count including its terminator.
    let result = unsafe { libc::connect(fd, (&raw const addr).cast(), len as libc::socklen_t) };
    if result != 0 {
        return Err(UNAVAILABLE);
    }
    Ok(stream)
}

impl FocusWitness {
    pub(crate) fn start(
        expected_focus: Option<u64>,
        cancelled: impl Fn() -> bool,
    ) -> WitnessResult<Self> {
        if cancelled() {
            return Err(CANCELLED);
        }
        let path = env::var_os("NIRI_SOCKET").ok_or(UNAVAILABLE)?;
        Self::subscribe(connect(Path::new(&path))?, expected_focus, cancelled)
    }

    fn subscribe(
        mut stream: UnixStream,
        expected: Option<u64>,
        cancelled: impl Fn() -> bool,
    ) -> WitnessResult<Self> {
        stream.write_all(b"\"EventStream\"\n").map_err(|_| UNAVAILABLE)?;
        let mut witness = Self {
            reader: BufReader::new(stream),
            partial: Vec::new(),
            bytes: 0,
            records: 0,
            phase: Phase::Reply,
            expected,
            failure: None,
        };
        let now = Instant::now();
        witness.drain(now, now + SETUP, cancelled)?;
        Ok(witness)
    }

    pub(crate) fn check(&mut self, cancelled: impl Fn() -> bool) -> WitnessResult<()> {
        if let Some(error) = self.failure {
            return Err(error);
        }
        let now = Instant::now();
        let result = self.drain(now, now + DRAIN, cancelled);
        self.failure = result.err();
        result
    }

    pub(crate) fn finish(mut self, cancelled: impl Fn() -> bool) -> WitnessResult<()> {
        if let Some(error) = self.failure {
            return Err(error);
        }
        let end = Instant::now() + TRAILING;
        self.drain(end, end + DRAIN, cancelled)
    }

    fn drain(
        &mut self,
        observe_until: Instant,
        deadline: Instant,
        cancelled: impl Fn() -> bool,
    ) -> WitnessResult<()> {
        loop {
            if cancelled() {
                return Err(CANCELLED);
            }
            if Instant::now() >= deadline {
                return Err(LIMIT);
            }
            let chunk = match self.reader.fill_buf() {
                Ok([]) => return Err(UNAVAILABLE),
                Ok(chunk) => chunk,
                Err(err) if err.kind() == ErrorKind::WouldBlock => {
                    if self.phase == Phase::Armed && self.partial.is_empty()
                        && Instant::now() >= observe_until
                    {
                        return Ok(());
                    }
                    thread::sleep(POLL_INTERVAL);
                    continue;
                }
                Err(err) if err.kind() == ErrorKind::Interrupted => continue,
                Err(_) => return Err(UNAVAILABLE),
            };
            let newline = chunk.iter().position(|&b| b == b'\n');
            let count = newline.map_or(chunk.len(), |i| i + 1);
            self.bytes += count;
            if self.bytes > MAX_TOTAL || self.partial.len() + count > MAX_FRAME {
                return Err(LIMIT);
            }
            self.partial.extend_from_slice(&chunk[..count]);
            self.reader.consume(count);
            if newline.is_some() {
                self.records += 1;
                if self.records > MAX_RECORDS {
                    return Err(LIMIT);
                }
                self.apply_record()?;
                self.partial.clear();
            }
        }
    }

    fn apply_record(&mut self) -> WitnessResult<()> {
        if self.phase == Phase::Reply {
            #[derive(Deserialize)]
            enum Reply {
                Ok(String),
            }
            if !matches!(serde_json::from_slice(&self.partial), Ok(Reply::Ok(s)) if s == "Handled") {
                return Err(INVALID);
            }
            self.phase = Phase::Inventory;
            return Ok(());
        }
        let event: Event = serde_json::from_slice(&self.partial).map_err(|_| INVALID)?;
        let reported = match event {
            Event::WindowsChanged { windows } => {
                let mut focused = windows.iter().filter(|w| w.is_focused);
                let id = focused.next().map(|w| w.id);
                if focused.next().is_some() {
                    return Err(INVALID);
                }
                if self.phase == Phase::Inventory {
                    if id != self.expected {
                        return Err("Niri initial focus differs from the expected foreground window");
                    }
                    self.phase = Phase::Armed;
                    return Ok(());
                }
                id
            }
            Event::WindowFocusChanged { id } => id,
            Event::WindowOpenedOrChanged { window } if window.is_focused => Some(window.id),
            Event::WindowOpenedOrChanged { window } if Some(window.id) == self.expected => None,
            Event::WindowClosed { id } if Some(id) == self.expected => None,
            _ => return Ok(()),
        };
        if self.phase != Phase::Armed {
            return Err(INVALID);
        }
        if reported != self.expected {
            return Err(CHANGED);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (UnixStream, FocusWitness) {
        let (mut server, client) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        server.write_all(br#"{"Ok":"Handled"}
{"WorkspacesChanged":{"workspaces":[]}}
{"WindowsChanged":{"windows":[{"id":1,"is_focused":true,"title":"private fixture"}]}}
"#).unwrap();
        (server, FocusWitness::subscribe(client, Some(1), || false).unwrap())
    }

    #[test]
    fn round_trip_focus_change_stays_latched_after_return_to_original_window() {
        let (mut server, mut witness) = fixture();
        server.write_all(b"{\"WindowFocusChanged\":{\"id\":2}}\n{\"WindowFocusChanged\":{\"id\":1}}\n").unwrap();
        assert_eq!(witness.check(|| false), Err(CHANGED));
        assert_eq!(witness.finish(|| false), Err(CHANGED));
    }

    #[test]
    fn neutral_updates_do_not_hide_a_focused_window_open() {
        let (mut server, mut witness) = fixture();
        server.write_all(br#"{"WindowFocusTimestampChanged":{"id":2,"focus_timestamp":null}}
{"WindowOpenedOrChanged":{"window":{"id":2,"is_focused":false}}}
{"WindowOpenedOrChanged":{"window":{"id":1,"is_focused":true,"title":"edited"}}}
"#).unwrap();
        witness.check(|| false).unwrap();
        server.write_all(b"{\"WindowOpenedOrChanged\":{\"window\":{\"id\":2,\"is_focused\":true}}}\n").unwrap();
        assert_eq!(witness.finish(|| false), Err(CHANGED));
    }

    #[test]
    fn partial_frame_or_cancel_cannot_pass() {
        let (mut server, mut witness) = fixture();
        server.write_all(b"{\"WindowFocus").unwrap();
        assert_eq!(witness.check(|| false), Err(LIMIT));
        let (_server, witness) = fixture();
        assert_eq!(witness.finish(|| true), Err(CANCELLED));
    }
}
