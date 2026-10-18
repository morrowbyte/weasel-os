# Task-owned Computer Use monitor indicator

This is presentation beside the Rust desktop actor. A small C/GTK3 renderer uses
the already available GTK layer-shell library; it contains no screenshot,
accessibility, focus, input, model, API or credential code. The Python standard
library helper owns its lifetime and reads actor status. GTK is isolated here so
the Rust capture/input core does not gain a GTK dependency or another Cargo tree.

The indicator has four solid blue 4-logical-pixel edge surfaces and a compact top
label: **Computer Use · Esc zum Abbrechen**. All five surfaces request the overlay
layer, explicitly disable keyboard focus, use empty input regions, and reserve no
workspace. They target one explicitly selected output, including full-screen
windows. The intended live target on this laptop is `DP-6`; the user's T3 monitor
is not automatically selected.

This implementation needs live Niri validation. Successful compilation, fixture
tests and the renderer's `ready` event do not prove visible edges, click-through,
correct physical output, full-screen stacking, or the real T3 Stop lifecycle.

## Ordinary worker-owned session

After the user grants control, explicitly resume the actor and verify it is idle,
released and unlatched. Run this in the actual worker's own `exec_command` session
with `tty=true` so stdin is held open:

```sh
weasel-computer-use-indicator run \
  --task-id demo-luna-r1 --output DP-6 --stdin-control
```

Wait for the concise `ready` event before desktop actions. Retain the command's
session ID. The helper deliberately stays foreground and keeps the indicator
alive while the model thinks between desktop batches. Do not detach it into an
unrelated always-running service. Do not substitute the broad shared T3 appserver
PID for a task's actual lifetime. Default parent-process identity is checked; an
explicit owner requires both `--owner-pid` and its exact `/proc` starttime field
through `--owner-start-ticks`.

End on workflow completion, client cancellation or failure:

```sh
weasel-computer-use-indicator end --task-id demo-luna-r1
```

Alternatively send `v1 end\n` through the retained session's stdin, or terminate
the exact owned helper process. The `end` command acknowledges the request; the
foreground helper's final `ended` event and process completion confirm renderer
exit. It reports the cleanup duration separately. The indicator does not itself
cancel desktop input; the actor's explicit Escape/Stop path owns cancellation.

```sh
weasel-computer-use-indicator status --task-id demo-luna-r1
```

Only the exact task ID and same-user Unix peer can control the session. Private
mode-0600 task socket/lease files are under
`$XDG_RUNTIME_DIR/weasel-computer-use/indicators`. One indicator owner per output
is enforced. An existing task identity is never silently replaced. Metadata
contains only task/process/actor identities, timing, target geometry and the
private control reference. It contains no credentials or app/window content.

## Lifecycle contract, revision 1

The renderer accepts a held stdin pipe with these exact newline-terminated lines:

```text
v1 begin
v1 heartbeat
v1 end
```

Arguments fix output name, task ID and the freshly obtained Niri logical rectangle
for the lifetime. Its GDK monitor must match that rectangle uniquely; fractional
scaling may differ by one logical pixel in width or height, while position stays
exact. Once selected, any change to the original GDK geometry ends the renderer;
the helper independently requires unchanged Niri geometry. There is no
default-output fallback. All surfaces begin hidden. A 1-second helper heartbeat
keeps the renderer alive. The renderer hides on end, EOF, malformed protocol,
signal, monitor invalidation/layout change, or a heartbeat gap exceeding
4 seconds. Startup also expires if no begin arrives. Namespace:
`weasel-computer-use-indicator`.

The helper's private control socket uses one bounded JSON line per connection:

```json
{"revision":1,"event":"end","task_id":"demo-luna-r1"}
```

`event` is `status` or `end`; both require exact task identity and same-user
`SO_PEERCRED`. Replies and stdout lifecycle events also carry revision 1.
Stdout events include a monotonic timestamp; timeout events additionally report
the owned request start/elapsed time without exporting the actor payload.

The helper registers one actor-owned task scope when its visible indicator
starts and closes it on normal end, disconnect or stop. Core checks the exact
PID/start-time of both helper and task owner, including before action admission
and between actions. Owner-bound scopes have no elapsed-time cap; process death,
Escape and epoch changes retire them without reopening a used task ID. Older
clients without process binding retain their legacy stale-scope deadline. A new
helper refuses READY if the actor cannot confirm owner-bound lifetime. A resume warning returns an ownership `scope_token`; pass it
to `run --scope-token` with that exact task ID. Ordinary new tasks may omit the
option and the helper creates a fresh token. Each `desktop_act` call must carry
the indicator's exact task ID; multiple batches within that scope remain
allowed. The helper reads the actor's internal
`desktop_indicator_lifecycle` contract, revision 3 with `complete=true`:
session, cancellation epoch, task ID and owner token, takeover latch, active
flag, task-scope state, queue count and confirmed release. Begin, status and end
are bound to the exact session/epoch/token tuple; an uncertain begin ACK still
attempts matching cleanup. It excludes full `last_result`,
images, display modes and capture/input-readiness inventories. Unknown endpoint,
revision, incomplete or malformed replies fail closed; there is no full-status
fallback. This endpoint is internal, not another agent action tool.

After a valid startup response, exactly one nonblocking probe runs through the
helper's existing selector on a bounded 100 ms cadence. Each probe has a total
300 ms connect/send/read deadline, including fragmented responses. One timeout
may retry once with the same bound; a second consecutive timeout ends the frame.
No heartbeat is renewed while the response is pending or authority is unknown.
Only a fresh valid response for the original session/epoch and an unlatched actor
allows renewal. Startup timeout, EOF and other capability/contract errors have
no retry. Explicit end, stdin end/EOF, signals and parent identity checks remain
reachable while the probe is pending; closing it cancels only this read socket.

The helper ends on epoch or backend session change, takeover latch, idle
unconfirmed release, unavailable lifecycle, owner PID/starttime change, explicit
end, stdin EOF (when requested), SIGTERM/SIGINT/SIGHUP, target output/layout
change, or an explicitly requested runtime bound. Active actions may legitimately have
unconfirmed release; that transient state does not hide the indicator. The
renderer still expires after 4 seconds without heartbeats. There is no automatic
resume or action replay and no replacement-backend reconnect. By default the
indicator follows the task owner's lifetime without a fixed session timeout.
Bounded trials can opt in with `--max-runtime-seconds SECONDS`; this does not
change owner-death, physical Escape or backend-session handling.

Lifecycle and output queries are read-only. Raw status, window titles,
environment, command arguments and credentials are never logged. The helper's
termination and the renderer lease do not establish that the actor has released
input: inspect fresh actor status independently after Stop/Escape.

## Component verification

```sh
python3 -m unittest discover -s packages/computer-use-indicator -p test_indicator.py -v
```

The tests use a fake actor, Niri inventory and renderer. They cover thinking gaps,
explicit end, cancellation epochs, takeover, backend replacement, active versus
idle release state, refused initial latch/owner mismatch, signal cleanup, hard
expiry and one output's competing ownership. Delayed and fragmented lifecycle
fixtures cover the single timeout recovery, second-timeout shutdown, no renewal
while unknown, known stop responses, unknown contract rejection, and end/parent
exit during a pending socket. These are private process/socket tests, with a fake
renderer; they do not prove visible monitor edges or ordinary T3 task cancellation.
The Niri geometry subprocess remains bounded by its existing 1-second timeout;
that independent query can still delay local event processing. Core lifecycle
reads retain the active-state mutex and may time out while release recovery holds
it; the bounded retry does not redefine such release/capability uncertainty.
Root must still prove the real monitor rendering, click-through and task Stop
including a model-thinking gap from ordinary T3/Codex sessions.

Primary API references:
[GTK layer-shell](https://github.com/wmww/gtk-layer-shell),
[layer-shell API](https://wmww.github.io/gtk-layer-shell/),
[GTK input shape](https://docs.gtk.org/gtk3/method.Widget.input_shape_combine_region.html).
GTK3 layer-shell is maintained; it was selected to reuse this laptop's available
small presentation stack. A future Rust/voice application can replace this
renderer while preserving the task/lease boundary.
