# Desktop pet and task-owned Computer Use indicator

The native companion is an animated Wayland desktop pet, not a text status island.
A small C/GTK3 renderer uses GTK layer-shell. Its body is pointer-through, its
separate speech bubble accepts only its explicit buttons, and neither surface
requests keyboard focus or reserves workspace. It never captures the desktop.
The Python helper keeps the existing Actor task, release, Stop and paint fences.

The warm speech bubble is at most 280 logical pixels wide, with three visible
lines and a curved tail toward the pet. Its message and arrow open the original
thread; the separate X only dismisses the reminder. There is no toolbar/header
or visible action text. A question or unanswered shield marks pending input;
only a result uses a check. The exact `Thread N · Question: ` / `Result: ` prefix
is omitted from the visible preview, while the complete original remains in
the tooltip and accessible description. Unknown prefixes are displayed as sent.
Compact native eye state and the existing Alt+Esc keyboard hint remain separate
from the message. The keyboard hint is not a clickable Stop control.

Computer Use shows the pet for the task lifetime, including when Crew's persistent
pet setting is off. Crew can separately start `weasel-desktop-pet --stdio` for an
opt-in persistent companion; that preference defaults to off. A private same-user
socket transfers only the bounded Crew event projection to a task-owned pet.
The persistent renderer is hidden while the task renderer is attached, avoiding
two pets. The feed cannot set observation policy, phase, visibility or task leases.
After the task ends, a selected persistent pet returns. Losing Crew clears its
notifications without stopping or authorizing Computer Use.

The pet follows Niri's focused output using structural `focused-output`/`outputs`
queries, including while observation is disabled. Presentation movement does not
change the action target, geometry guard, task identity, epoch or Stop behavior.
No window inventory, title, pixels or accessibility tree is read for this purpose.
If a focus query fails, the pet stays on its last confirmed monitor and retries
after 500 ms. Only fixed diagnostic codes are logged. This presentation retry
does not end the Computer Use task; the independent target-output geometry,
monitor-loss, Actor lifecycle and paint-acknowledgement guards remain in force.

The visual states distinguish permission from acquisition: covered eyes mean
confirmed observation disabled; reading eyes indicate acquisition preparation or
an actual read; unconfirmed policy has a separate closed-eye/question pose. No
privacy claim is inferred from Crew arguments or old chat results. A custom pack
cannot replace the renderer-controlled disabled/unknown gesture. The body does
not use privacy status text; accessible names describe each state. Alt + Esc
remains visible during Computer Use. Thinking dots represent real ongoing work,
not private model reasoning. A safe authored activity remains available through
the existing `activity` command and accessibility name, without echoing input.

Before capture or scoped input the Actor still requires the matching transition
ACK after the mapped pet has painted its current phase and privacy pose. This
attests GTK drawing, not physical compositor presentation. Native target checks
retain their structural path and do not add per-action paint transitions. Animation
runs in bounded four-frame bursts and then settles; heartbeats never redraw.
Reduced motion settles immediately. There is no permanent idle GPU animation.

## Crew JSONL v1

Crew spawns `weasel-desktop-pet --stdio` without a shell. Each line is at most
16 KiB. `configure` accepts `persistent`, `visible`, `reducedMotion` and a local
`petPath` or null. `projection` accepts an increasing integer `revision`, a bounded
`workingCount` and at most 20 items with opaque `id`, `kind` (approval, question,
result) and an explicitly safe public `summary` of at most 240 characters. No
Actor authority fields are accepted on stdin. `toggle` changes persistent
visibility; the task-owned Computer Use indicator remains independently visible.
`shutdown` or EOF ends the Crew feed, not a live Actor task.

Output contains versioned `ready` capabilities, `state`, opaque `action` events
(open/dismiss), stable-code `error` and `disconnected`. `computerUse` is null when
unconfirmed; otherwise session, epoch, task, observation mode/active/generation
come only from the Actor lifecycle endpoint. Dismiss only removes a reminder;
open returns its opaque ID to Crew's existing thread/approval flow. Nothing is
automatically approved. Approvals precede questions, then results. Stale revisions,
duplicate IDs, unknown fields and unsupported actions are rejected.

## Custom artwork

The bundled original generated weasel Lumi has eight four-frame strips. The
asset compiler normalizes its original atlas into the integer-cell pack ABI.
Importable packs contain `pet.json` and `pet.png`, with no URLs, scripts or SVG.
Frames are 64–512 px, a 4 by 8 grid, RGBA 8-bit PNG up to 16 MiB; metadata is at
most 16 KiB. Rows are idle, working, reading, lookingAway, waiting, attention,
success and error. Each state fixes frames=4, fps=6, loop=false. Names are at
most 60 characters. Paths, symlinks, special files, control/Bidi characters and
oversized dimensions are rejected, and the renderer decodes PNG before use.
Crew copies imports privately and can return to the default pack. Its generation
entry opens the normal Agent draft; actual image-tool/provider availability and
successful artwork generation are distinct from opening that draft.

Live evidence must cover the actual native overlay and Electron integration.
Compilation, unit fixtures and `ready` are separate from visible poses, input
routing, monitor selection, animation, thread actions and physical device behavior.

## Ordinary worker-owned session

After the user grants control, explicitly resume the actor and verify it is idle,
released and unlatched. Run this in the actual worker's own `exec_command` session
with `tty=true` so stdin is held open:

```sh
weasel-computer-use-indicator run \
  --task-id demo-luna-r1 --output DP-6 --activity "Einstellungen prüfen" --stdin-control
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
v1 phase waiting
v1 phase queued
v1 phase active
v1 phase observing
v1 phase unknown
v1 observation enabled idle
v1 observation enabled active
v1 observation disabled idle
v1 observation unknown idle
v1 activity <plain text>
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
`desktop_indicator_lifecycle` contract, revision 4 with `complete=true`:
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

The optional "Blick aus" mode is deliberate looking away, not secret detection
or a universal privacy guarantee. It does not block separately authorized
private-store references or direct process injection through existing tools.
The compact lifecycle carries `observation_mode` (`enabled` or `disabled`) and
`observation_active` (actual content acquisition). Enabled means permission to
read, not proof of a read. Disabled is shown as private only after the actor
confirms it. Older actors without these fields show access as unverified; an
incomplete or contradictory policy response is rejected. A lifecycle timeout
immediately removes the privacy claim before the bounded retry. The helper
forwards only these typed fields, never full status, action text or window names.

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

## Current activity and cancellation

Supply a short concrete `--activity` at begin. Update it between workflow phases
without replacing the task scope:

```sh
weasel-computer-use-indicator activity --task-id demo-luna-r1 --text "Ergebnis kontrollieren"
```

Text is bounded to 120 printable characters and rendered literally, never as
markup. Do not include credentials or private document contents. Physical
Alt+Escape cancels current Computer Use and its queued batches, releases owned
input and hides the indicator. It does not permanently lock Crew. Ask the user
why they stopped and await further instructions before a new task; never replay
the stopped task or resume automatically. Bare Escape is ordinary app input.
Ctrl+Alt+Escape remains the independent explicit persistent takeover command.

### Managed user deployment

After a scoped Home Manager rollout, invoke
`~/.local/bin/weasel-computer-use-indicator` explicitly for `run`, `activity`
and `end`. The system profile can still expose an older package of the same
version until the next OS activation; a bare PATH lookup therefore does not
identify the current managed helper. The installed Computer Use skill uses this
explicit path. Do not fall back to an incompatible system-profile helper.
