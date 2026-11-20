---
name: weasel-desktop
description: Operate the WeaselOS Niri desktop, address an existing application window, or run a third-party Wayland app in a private background workspace while the user keeps typing.
---

Use the application's own API, CLI or MCP when it provides the requested effect. For desktop actions, discover the installed surface with `weasel catalog` or the `weasel_os` catalog tool. The keyboard palette opens with Super+Space. Check the focus effect in the catalog before acting.

For an existing window, obtain its current Niri ID, inspect its accessibility tree, then use the offered action and verify the new content. The window-bound observation and handle belong to one actor session and stop epoch. A named button can open a dialog. Check the recorded focus events and the application result before continuing.

For a visual task that can open its own app instance, use a named private workspace. Open the application, wait for its window, inspect its screenshot and send input there. The app receives a separate home, clipboard and display. To return a document, start with an explicitly selected private exchange directory, then save under `/exchange` in the app and check the exported file. Network access is an explicit workspace start option. Files open in the person's existing window belong to the original app instance.

Compose independent or stable actions using `weasel compose --request-id ID`. Inspect intermediate state before acting on a newly opened window or dialog. Read step receipts and resulting content after cancellation, partial effects or lost transport. Alt+Escape stops agent input. Resume private input only for a new authorized continuation.

For a second look at a private workspace, pass the previous screenshot's `sha256` as `if_sha256`. The tool samples the live output each time. A matching hash returns `unchanged` immediately after capture and saves transferring the same image again.

For a file task in an explicitly shared workspace directory, use `private_exchange_list` to identify the files. Compose `private_exchange_rename_noreplace` with `private_exchange_lstat` to rename and verify the destination in one call. An existing destination stops the rename. Use the graphical file chooser when the task calls for interacting with that particular UI.

Foreground control uses the installed `weasel_desktop` actor. After login or an explicit takeover the desktop owner can press Super+Shift+A to allow new desktop actions. Respect the current stop and session. Obtain fresh observations, bind physical actions to their task and use the packaged indicator. Supply an explicit target and verify changed application state. Keep personal credentials in their existing stores.
