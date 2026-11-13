# Weasel virtual pointer transport

Small independently authored Rust library and JSONL process. The physical
desktop owner must serialize its use with keyboard, clipboard and other input
backends. This library does not implement planning, focus policy or UI result
verification.

Direct library entrypoint: `src/lib.rs`. Copy it as a module or add this package
as a dependency. Public types: `Pointer`, `PointerError`, `Button`, `OutputInfo`,
`Capabilities`.

`Pointer::new()` reads registry and output events. It creates no virtual pointer
device and sends no input. Both bootstrap syncs have one-second deadlines.
`capabilities()` and `outputs()` are read-only. The first `move_to()` creates an
output-bound pointer through virtual-pointer protocol version 2.

`move_to(output, x, y, width, height)` accepts displayed output-local coordinates
and full output extents. For a screenshot crop, add its output-local origin and
keep the full PNG dimensions. The transport inverts the confirmed `wl_output`
transform before normalizing to one million units, because the compositor applies
that transform to an output-bound virtual pointer. All eight rotations/reflections
are supported; missing, unknown or incomplete transform updates refuse before input.
`wl_output` integer scale is metadata and must not be substituted for
Niri's fractional scale. Out-of-range coordinates, absent/ambiguous output names
and output changes during a held button are refused.

`button(Button::Left | Right | Middle, pressed)` tracks held state. `scroll(dx,dy)`
sends continuous units; `scroll_steps(dx,dy)` sends discrete wheel steps.
`release_all()` frees held buttons without moving the pointer. A drag should be
implemented by the embedding executor as move, down, individually cancellable
move steps, up. `Drop` attempts a release and destroys its virtual devices.

Action calls flush requests. `sync_timeout(Duration)` confirms compositor
receipt within the specified deadline. `sync()` uses one second. Neither confirms
rendering or target UI changes: perform a fresh independent observation afterward.

## JSONL process

`cargo build --locked` creates `target/debug/weasel-computer-use-pointer`.
On this laptop the available Nix GCC wrapper was needed in PATH:

```sh
env PATH=/nix/store/788mx070y81zjlg5ipcl0cra3afviw9k-gcc-wrapper-15.2.0/bin:$PATH \
  cargo build --locked --manifest-path /tmp/weasel-computer-use-pointer/Cargo.toml
```

Read-only capability query:

```sh
/tmp/weasel-computer-use-pointer/target/debug/weasel-computer-use-pointer --capabilities
```

Normal mode keeps one Wayland connection and accepts newline-separated objects.
Every reply echoes `id`, reports elapsed microseconds, and distinguishes request
acknowledgement from UI verification. Example request shapes (not executed):

```json
{"id":1,"op":"capabilities"}
{"id":2,"op":"move","output":"eDP-1","x":100.5,"y":100.5,"width":1536,"height":960}
{"id":3,"op":"button","button":"left","pressed":true}
{"id":4,"op":"button","button":"left","pressed":false}
{"id":5,"op":"scroll_steps","dx":0,"dy":3}
{"id":6,"op":"release_all"}
{"id":7,"op":"sync"}
```

Do not use the example coordinates against an unobserved desktop.

## Verification performed

- Rust compilation with the locked dependencies succeeded.
- Read-only registry query succeeded; actual Niri advertises
  `zwlr_virtual_pointer_manager_v1` v2 and `wl_output` v4.
- Named outputs `DP-6`, `DP-5`, `eDP-1` were observed.
- No pointer device was created and no input action was tested by this author.
- Cargo rustfmt is unavailable in the current shell; formatting can be performed
  with the embedding project's Nix Rust development tools.

Protocol and crate APIs were inspected from the official MIT-licensed
`wayland-protocols-wlr` crate's bundled wlr-protocol XML and `wayland-client`
source. No source was copied from niri-use or other input automation projects.
