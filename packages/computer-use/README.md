# Niri computer use

A persistent Rust desktop actor and stdio MCP bridge for the actual Wayland session.

When the live tool schema exposes `close_window`, a caller can request closure
of one freshly observed, focused, task-owned window by its explicit Niri ID.
It is a standalone action with matching top-level and action IDs. Prove safe
saved state before requesting it; the actor cannot attest document ownership
or unsaved buffers. A request acknowledgement is not window disappearance:
inspect a fresh full inventory and any save dialog. Never use shared process
termination as window cleanup. This source path still needs live qualification.

The cooperative desktop policy treats physical Esc as an abort only while a
Computer Use task is live: its actor action is queued/preparing/running, or its
task-owned indicator scope is open across model-thinking gaps. An idle Esc still
advances the physical input generation and invalidates old observations, but
does not latch a takeover or persist a marker. An in-scope Esc advances the
cancel epoch immediately, stops preparation and queued actions, releases owned
keys/buttons, and closes the current task scope without persisting a takeover.
Completed/partial effects remain reported and are never replayed. A real
explicit desktop takeover remains a separate persistent stop.

The indicator opens an actor-owned task scope when it begins and closes it on
end, disconnect or stop. The helper binds its own PID/start-time and its task
owner's PID/start-time to that scope. Both processes must remain live; there is
no elapsed-time cap on an owner-bound job or approval wait. Old clients without
process binding retain the legacy 20-minute stale-scope deadline. After Escape,
the agent stops the current turn's desktop work. User steering or a continuation
request in a new turn permits a fresh task ID and indicator without a separate
unlock prompt or warning delay. The actor retains used task IDs so the stopped
scope cannot be reopened; the new task must obtain fresh observations. Include
the same task ID on every `desktop_act` call. A live indicator permits multiple
batches for its one task; stale, ended, reused or mismatched IDs are refused.
Released controls and the existing 300 ms quiet gate still apply. Legacy
persisted `physical_escape` markers migrate to the normal backend-startup gate;
the current authorized task permits its bound startup resume. Explicit takeover
and other legacy persisted markers retain their explicit-return policy. No
stopped task or queued action is resumed or replayed. Turn authorization is the
agent's responsibility; task IDs alone do not attest a user turn.
Authoritative bounded evdev key snapshots retain only the aggregate number of
held physical controls. Held modifiers/buttons or unknown input state temporarily
refuse dispatch, without latching takeover; release and fresh observation allow
continuation. Raw key bitmasks are discarded immediately.
The laptop Home Manager module installs the package, graphical-session service,
additive Codex MCP entry and the `niri-computer-use` skill. T3 and ordinary Codex
share the existing Codex home and the same single physical writer. Existing
clients need their supported MCP reconnect or a fresh agent session after config
changes. Source and service installation alone are not live acceptance.
For task-owned Meld launches from T3, omit only the inherited `LD_LIBRARY_PATH`
in that child process. The tested GI import otherwise loads conflicting T3 and
Meld GLib/AT-SPI versions. Preserve graphical-session variables and rely on
Meld's own package wrapper; actual window/edit/save verification remains required.

T3 desktop execution defaults to one delegated GPT-6-luna Codex worker with
medium reasoning; the parent conversation can retain its selected model.
The worker receives the full task and is the sole input owner. Optional
`desktop_decide` uses the separately budgeted Decisions endpoint for bounded
predicate/choice checks, rather than free action planning. Deterministic
readback should avoid unnecessary paid model checks.

A separate small GTK3/layer-shell indicator marks the selected output with a
blue rim and `Computer Use · Esc zum Abbrechen`. It is click-through and never
takes keyboard focus. The owned workflow helper spans model-thinking gaps,
binds the actor session/epoch, and closes on end, disconnect, explicit abort or
lease expiry. The renderer is C and the lifecycle helper is Python; capture,
input, freshness and cancellation remain in Rust. This is an explicit workflow
lifetime, not a claim that a per-action active flag covers an entire task.

The managed `mcp_servers.weasel_desktop` entry uses
`default_tools_approval_mode = "approve"` for the explicitly authorized desktop
control. This is a policy for this owned server only; global Codex approval and
sandbox settings and other MCP servers retain their existing configuration.
Tool annotations identify observation tools as read-only, actions as potentially
destructive writes, and cancel/takeover/resume as local control mutations. Use
these tools within the user's requested task. After activation, verify the
connection in a fresh ordinary agent session and complete an authorized workflow.
The owned entry enables `supports_parallel_tool_calls` so status and priority
control can reach the daemon while an action is waiting. The daemon still owns
the single physical input queue. Existing Codex clients need a fresh connection
to load this setting. The laptop's [source-built Codex](../codex-source/README.md)
also forwards interrupted MCP requests; an agent's local Stop acknowledgement
alone does not establish actor release.

## Operations and boundaries

Pointer actions require a displayed view no larger than 1200 pixels on either
axis. Larger screenshots remain available as overviews. The actor validates
every move, click, scroll and drag before admitting the batch, so a later
oversized pointer target cannot follow earlier typing or other batch effects.
The error asks for a fresh target crop and crop-local coordinates. Smaller
crowded targets still require appropriate tighter grounding by the planner;
this size bound alone does not establish target identity or UI success.

Protocol schema 1 exposes status, real window/output/workspace inventory,
observations, Cua accessibility reads, direct AT-SPI reads, typed actions,
priority cancel, latched takeover and explicit resume. Observations carry a
unique session/observation ID, monotonic timing, actual PNG/crop dimensions,
Niri identity, fractional output geometry and capabilities. Coordinates are local
to the returned screenshot or crop. Cua bounds are not screenshot coordinates.

Direct AT-SPI labels remain readable during ordinary input or held controls.
The standalone `semantic_focus` action targets an actionable opaque handle.
It invokes Component.GrabFocus once, repeats exact context validation and reads
FOCUSED on the same object. A boolean acknowledgement alone is insufficient.
Fresh visual and semantic observations must confirm the desired text focus
before dependent keys; selection, clipboard and artifact checks remain separate.
Duplicate window-title mappings retain their existing refusal.

When the live MCP schema exposes `include_text_selection`, direct semantics
can opt into bounded selection metadata for one focused, showing, enabled,
unprotected Text object. Its provider character count and Unicode-codepoint
[start,end) range are sampled twice, with focus readback. Only
`status: "available"`, `stable_readback: true` and
`full_text_selected: true` attest a nonempty whole-object selection.
These samples are not atomic and `text_identity_verified` remains false:
check the exact document/content, complete tree, current generation and
input readiness separately. Missing metadata, unsupported/error, multiple
selections or stale state supply no proof. Default false adds no selection
reads. This optional schema needs a fresh tool connection and actual
GTK/Electron bridge qualification; it does not normalize a terminal LF or
change input/cancel permissions.

Direct snapshots also expose versioned `traversal_diagnostics`: effective node
and depth bounds, the existing soft elapsed-time checkpoint budget, and counters
for actually encountered cutoffs, read failures and visibility contradictions.
Counters describe observed checks, not a count of omitted descendants. Legacy
snapshots publish `null`, which means the producer supplied no diagnosis.
These fields do not grant handles or change completeness or input readiness.
If an explicitly lower bound caused truncation, one fresh read at the supported
defaults (depth 40, 1000 nodes) can clarify the result. Persistent read errors,
visibility contradictions or deadlines require a deliberate alternative route;
increasing limits does not establish a valid semantic target. The 3000 ms value
remains a soft checkpoint boundary, not a per-call or atomic timeout guarantee.
`status: "available"` or `"limited"` describes the tree, while `input_ready`
and `action_ready` describe the separate readiness snapshot. Incomplete or
initially/finally non-actionable trees issue no new handles or mutation
capabilities. A conflict removes only this read's newly staged handles;
Escape, deadline and local cancellation remain errors. After controls are
released, obtain a new complete semantic snapshot and a fresh visual observation
before input. Native MCP cancellation of this read remains limited by its
existing `Other` request classification; the action cancellation path is separate.

The visual guard keeps one bounded decoded reference image: at most 16 MiB of
pixels and 4 MiB of original PNG bytes. A hit still rereads and compares the exact
reference bytes and checks the observation, session, epoch and input generation.
Click, scroll and drag still capture and decode fresh current pixels before
dispatch. Move retains its existing absence of a current-pixel comparison.
Decode and file I/O do not hold the cache mutex; stop and recovery invalidate
the reference. These storage limits describe retained payload, not process RSS.
The cache is an implementation optimization; live timing and successful task
outcomes are required before claiming a speed improvement.

Startup status distinguishes `capture_state: "not_attempted"` from a failed
capture. `capture_available: false` by itself does not establish a failure or
prevent an authorized startup resume. Inspect the current latch, actor release,
queue and `resume_input_readiness`; readiness is advisory, not permission.
An active `desktop_resume` requires `expected_epoch` and
`expected_session_id` from the current backend status for the stop the user has
authorized resuming. Do not use the historical cause's session or automatically
refresh a stale binding after another stop. The proxy checks binding support
and forwards a distinct bound wire method so an older replacement backend cannot
ignore these fields. An idle, released, already-resumed actor permits a read-only
no-op without changing the epoch or cached targets.
Both action and resume MCP requests have request-scoped cancellation ownership.
A cancellation can still arrive after a transition has completed; inspect fresh
status rather than assuming reversal. Physical Escape cancels work without
downgrading an active explicit takeover. Every accepted persistent stop gets its
own cause identity. A failed persistence or rollback remains a visible stop.

For visual input, the skill treats images over 1200 pixels on either axis as
overviews and requires a fresh crop no larger than 1200 pixels on either axis.
Small or crowded targets also need crops. Ground points in the actual returned
crop dimensions and view; do not infer a model's prepared image dimensions from
monitor scale or a chat thumbnail. The actor checks freshness and nearby pixels,
but cannot prove that an arbitrary point belongs to the named window when Niri
does not provide absolute bounds. Check the new focus and intended UI effect;
a wrong window requires a separate focus and fresh grounding before recovery.

Input uses output-bound wlr virtual pointers and a canonical German Wayland
keyboard for app shortcuts. Every app chord refreshes and acknowledges the keymap.
`key` accepts `key_scope: "app" | "compositor"`, defaulting to `app`.
Super/meta/logo automatically select the compositor route. Niri's current
Wayland virtual-keyboard handler sends chords directly to the focused app and
does not run the compositor shortcut filter, so global shortcuts use an owned
persistent uinput keyboard. Preparation verifies the authenticated Niri process
has opened that exact device, then rechecks layout, observation, focus and stop
state before input. Device readiness is not evidence that a shortcut worked;
verify the resulting UI. A new proxy rejects a global batch against an older
backend before any action, requiring `global_keyboard.routing_revision = 2`
and the same session, epoch and observation. Capability failure has no blind
Wayland fallback. Release cleanup tracks possible partial writes and destroys
the owned device if necessary; unconfirmed release blocks further input.
Focus is a separate
action and requires fresh observation. The actor revalidates focus, window
identity, geometry, workspace/output binding and pointer target pixels. It does
not invent Niri window bounds or prove a coordinate belongs to a named control:
the planning client must ground the actual intended target from the observation.

`desktop_act` accepts at most 32 stable actions with a whole-operation deadline.
Queue, validation, subprocesses, input and post-action capture share that budget;
release cleanup retains its own bounded budget. Default post-action settle is
80 ms and adjustable to 0–1000 ms. It is not proof of repaint. The returned
`after_observation` and image may feed the next action directly. Changed or slow
UI needs a bounded fresh result check rather than replaying toggle input.
Effects, partial effects, release status and failures remain explicit.
At the stdio MCP boundary, observation/action replies abbreviate exact known
explanatory prose and replace valid display mode catalogs with the exact selected
mode, original index and catalog fingerprint. All window identities, geometry,
guards, effects, errors, timings and image bytes remain. Control fields precede
large inventories in JSON text. `detailed: true` returns the original complete
envelope; `desktop_windows` also retains full mode catalogs. Internal observations,
actor state, event receipts and ordinary CLI replies are not projected.
When the live MCP schema exposes `compact`, `compact:true` additionally narrows
the window inventory in observation/action replies. It retains focused,
capture-active, explicitly targeted, urgent, floating and unfamiliar windows,
and reports omitted IDs, original count, an inventory fingerprint and
`windows_complete:false`. All other state and PNG bytes remain unchanged.
Use `desktop_windows` to discover the complete window set or `detailed:true`
for the full reply. A missing window in this opt-in subset is not evidence
that it is closed. This is a payload optimization, not a measured model-latency
or reliability improvement; the actor continues using its full observations.
Fields must match the selected action kind. An unknown or misplaced field
rejects the complete batch before input. In particular, `restore_clipboard`
belongs to `paste`; `type` always requests preservation of the prior selection.

If a bounded cleanup receipt fails, new input remains refused. When the actor is
idle and the queue is empty, `desktop_recover_release` explicitly retries only
release/receipt on existing owned actuators. It never creates a device, moves the
pointer, presses a key or replays an action. Its shared timeout is 200–2000 ms
(default 1500 ms); busy locks refuse immediately. A successful retry preserves
the takeover latch, cause and epoch, retains the original failed result, and
invalidates captures and semantic targets. Check status, then obtain a new
observation before input; a human takeover still requires the user's return of
control. A genuinely broken connection remains unconfirmed and needs a deliberate
repair of this owned backend. Receipt confirmation is separate from UI success.

Auto text uses plain clipboard for verified Electron IDs and text over 1000
characters; other shorter input uses wtype. Electron keyboard text is known to
lose physical keycodes and supplementary Unicode on this laptop. Clipboard
preservation uses a bounded RAM-only snapshot from one data-control offer and
fresh source identity checks. Supported text/rich payloads and original Chromium
custom data/provenance are restored together; the temporary agent text never
carries original Chromium source tags. Duplicate MIME names are normalized.
`SAVE_TARGETS` and `GTK_TEXT_BUFFER_CONTENTS` are intentionally omitted without
reading, with each omission reported. The latter is GTK's SAME_APP pointer
optimization; replaying that address from another process would be invalid.
The exact `application/x-gtk-text-buffer-rich-text` representation is preserved
as bounded opaque bytes together with the text formats. This follows
[GTK3's own clipboard persistence rules](https://github.com/GNOME/gtk/blob/3.24.51/gtk/gtktextbuffer.c#L3809-L3812).
Unknown, sensitive, portal-handle or oversized
formats refuse before replacement. Restoration remains best effort because
Wayland provides no atomic selection CAS, and is skipped after cancellation,
takeover or ownership loss. A separate owned user scope holds the restored
source until a new copy or graphical-session stop; live restart persistence
still requires runtime verification. `paste` with `restore_clipboard=false` is deliberate replacement,
appropriate only when authorized and the previous selection is understood.
Do not suggest unreliable keyboard text as an Electron fallback. Caps/Num lock
state is not supported. VS Code's tested isolated profile uses keyCode dispatch;
other profiles need actual shortcut verification.

Direct AT-SPI uses immutable unique-owner D-Bus object identities and daemon-owned
opaque handles, not integer indices or caller-supplied object paths. Handles bind
to the observed Niri instance, bus generation, epoch and expiry. Mutations
revalidate role, interfaces, ancestry, enabled/showing state, modal membership
and visible text context. Traversal retains the selected window root and
descendants whose ancestor chain reports SHOWING. Hidden branches are omitted;
immediate visibility contradictions, unknown states, budget exhaustion or a
hidden selected root make the result read-only. Completeness applies to this
versioned visible scope, not inactive tabs or unvisited hidden descendants.
Pruning counters and scope metadata are returned explicitly. Other long text
fields contribute bounded excerpts; set-value checks the target's complete prior
buffer. Missing bridges require the verified visual path, not blind retries.
Individual action names and CharacterCount-bounded text reads avoid the tested
Gecko bulk-action/range defects. The tested Zen 1.21.8b setter still acknowledged
a no-op, which exact readback reported as uncertain. Browser editing therefore
uses verified keyboard input; semantic activation/press needs separate live
proof. Advertised interfaces alone are not an app capability guarantee.

Capture failure invalidates old observations/semantic handles until a successful
fresh capture. Hotplug/global removal refreshes capabilities and refuses stale
connections. One lifetime flock spans socket paths; a second daemon cannot own
physical input. Normal-client disconnect/cancel applies only to its own request;
explicit cancel/takeover stops the shared pending queue. Physical evdev Esc
press latches takeover; other activity advances observation generation without
retaining event contents or permanently stopping the task. Controlled
input simulation is not a claim of real human takeover.

## Start, stop and handover

```sh
systemctl --user status weasel-computer-use.service
weasel-computer-use call desktop_status '{}'
weasel-computer-use call desktop_cancel '{}'
weasel-computer-use call desktop_takeover '{}'
```

Physical **Esc** requests abort only during a live Computer Use task scope;
idle Esc invalidates old observations without latching the actor.
**Ctrl+Alt+Escape** remains a separate explicit takeover through Niri.
Synthetic agent Escape for dialogs does not trigger physical abort. Check
confirmed input release; completed effects persist. After Escape, stop the
current desktop task. User steering in a new turn permits a fresh task ID,
indicator and observations without a separate unlock prompt. Quiet/released
gates still apply, and stopped task IDs cannot reopen. An explicit takeover
still requires the user's return of control.
Repair only this owned backend; do not
restart T3, the compositor or foreign clients as chaos tests.

Typical requests: edit and save a specified test document in an identified app;
fill a local form in an existing browser test tab; copy a specified selection
between identified apps and verify the destination. Preserve original tabs,
profiles and documents. Authentication and locked-session bypass are unsupported.

## Future Rust/voice boundary

Capture/Input stay separate from model planning. `observe` produces identity,
geometry, semantic data, image references and timings; `act` binds task/action
intent to an observation and deadline. Verification consumes a *new* observation
and independent artifact evidence and yields success/failed/uncertain in the
planning client; input acknowledgement is not verification. `cancel/takeover`
preempt pending execution and report completed effects. Private schema-1 events
record request/progress/capability/action/result/timing and failure metadata.
This version provides no voice implementation. Speech processing must not block
the actor or priority stop operations.

The optional Python UI Decisions evaluator has its own service, API credentials
and persistent authorized budget ledger. The Rust actor has no model credentials
or API access. See the evaluator README for explicit start and cost boundaries.

## GUI application lifetime

A background command owned by a Codex `exec_command` session can be finalized
when that agent run ends. This happened to our three isolated Calc test profiles,
after their verified files were already saved. For an app that should remain
open, launch its own transient user service with `systemd-run --user --collect
--unit=weasel-cu-app-<unique-task> --service-type=exec --property=ExitType=cgroup
<absolute-app-command> <explicit-own-profile-and-files>`. Keep its unit, profile,
process and window identities together. An existing shared application may
forward to its earlier process; the launcher unit alone does not establish
window ownership. Use a supported per-app profile/new-instance option where
needed, and verify the actual result after the agent exits. Do not stop shared
user apps or their services to clean up a launcher.

Meld's GtkSource buffer omits an implicit final LF while loading, then restores
it on Save. Native Select-All/Copy therefore may omit that file byte. For an
exact file-transfer task, compare the genuinely pasted/saved result before
normalizing it: only when the sole difference is one required final LF, move
to the destination's end, insert one Return and Save through the UI. Report
that normalization; other differences require investigation.

## Verification status

Offline Rust subprocess/deadline/capture/transport regression is reproducible
with the locked Cargo files. Nix host builds check packaging. Real application,
client, restart, takeover and multi-display evidence belongs to the private
acceptance matrix and demo; builds and fixtures alone do not complete acceptance.

### Direct actions while the human uses another window

`desktop_semantic_direct({window_id, background:true})` requests object-targeted
click/edit handles. Use those handles with `desktop_act` and an observation of
the target output. These actions call AT-SPI directly; they do not request
focus, simulate keys, move the pointer or replace the clipboard. Physical input
in another window does not invalidate this route. Input overlapping the actual
target window, changed window/output identity, changed semantic context, an
unavailable Escape monitor, task cancellation or incomplete semantics still
refuse dispatch. The existing foreground mode remains available for explicit
focus and keyboard/pointer work. Do not mix background actions with those
actions when independent operation is required. A named app action can itself
change focus; verify the result instead of promising universal focus isolation.

This route requires the updated actor and actual app acceptance. Source tests
are not proof of live app behavior or independent OS input seats.
