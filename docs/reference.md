# Technical reference

[Back to the README](../README.md)

## Camera and light behavior

- Snapshots use the stock `/server/files/camera/monitor.jpg` feed without a configured FPS limit.
  The next snapshot is requested as soon as the previous image loads, with at most one frame awaiting display.
  This follows Fluidd's adaptive snapshot loading pattern with its target-FPS delay removed.
  The displayed FPS counts changing images rather than repeated downloads of the same image.
- While active, the worker sends `camera.start_monitor` with `domain=lan` and `interval=0`, then renews it every sixty seconds.
- Leaving the page stops local polling and closes its WebSocket.
  The plugin does not send a global camera-stop command, which could interrupt another viewer.
  The printer stops producing images after its own monitoring timeout unless another viewer keeps it awake.
- A 3.5-second activity lease stops abandoned monitoring if the webview loses its connection to the plugin.
- Live status polls follow monotonic three-second deadlines rather than waiting three seconds after each request.
  Status, metadata, and camera operations share one worker, so a poll cannot preempt in-flight I/O.
  After an overrun, one current poll runs when the worker is free and missed ticks are skipped.
- Image timestamps older than five seconds mark the stream as frozen.
  Without timestamps, receiving identical images does not by itself establish that the camera is frozen.
- The stream indicator shows a blue dot with FPS while fresh images arrive and an orange elapsed-seconds counter during interruptions.
  Interruptions lasting more than five seconds show a red outline and the latest error.
  A failed snapshot request, including HTTP 404, keeps the last frame visible.
  Stop and printer selection changes still clear the frame.
- The only light command is `SET_LED LED=cavity_led WHITE=0|1 SYNC=0`.
  Light status is read back from `led cavity_led`.
  The toggle also works before Play and after Stop, without starting camera monitoring.
  Failed light commands show feedback beside the toggle without interrupting camera playback.
- HTTP redirects and environment proxies are disabled, requests have short timeouts, and HTTP response sizes are bounded.
  API keys stay in Python memory and are used to obtain a short-lived WebSocket token.
  The stock HTTP connection is unencrypted.

Orca's Pages API has no tab-activation callback.
The page uses `visibilitychange` and `pagehide` to stop playback when leaving the page.
App and keyboard focus do not control playback.
Test the embedded webview when upgrading Orca or using another platform.

## Status and observed pause timers

The header timer after “Status unavailable” measures the current interruption, starting at the first failed reading or the known ten-second expiry deadline.
Initial waiting has no interruption timer; a successful reading clears it.
A timer after “Paused” measures time since the page observed a transition from continuous fresh printing or preparation readings into paused state.
A first paused reading has no timer because its start is unknown; a status gap clears any observed pause timer.
This is not the printer's exact current pause duration.
The checked [printer statistics API](https://github.com/Snapmaker/u1-klipper/blob/7ac453b50fe6779b2ec0b759038a17da5e6d671d/klippy/extras/print_stats.py#L315-L339) and [pause API](https://github.com/Snapmaker/u1-klipper/blob/7ac453b50fe6779b2ec0b759038a17da5e6d671d/klippy/extras/pause_resume.py#L57-L60) do not expose the current pause start.
The difference between total and printing duration also includes preparation and previous pauses, so it cannot supply the current pause duration.

## Print duration and finish estimates

`planned_total` is the original positive, finite slicer `estimated_time` from the active file's bounded metadata response.
It is never current time left.
`remaining` is an eligible current estimate, null when unknown, and zero only for completed jobs.
Elapsed and printing durations are the printer's clocks; neither clock is subtracted from the plan or extrapolated from file bytes.

Layer zero means `preparing`, even after purge gives a positive printing duration.
A positive integer model layer means `printing`; absent, invalid, or contradictory markers mean `unknown`.
Confirmed M73 progress of 100% during an active job means `finishing`, with unknown ETA.
Preparation has unknown remaining duration; no learned startup constant is applied.
With a known plan, the preparation finish label is “after” the latest status time plus that plan.
This is an approximate plan-based baseline, not a measured completion time.

Finish estimates currently support metadata identifying `OrcaSlicer`, including the 2.5.0-dev producer, using its normal-mode time plan.
Other producers, including unverified SnapmakerOrca variants, show only the original plan.
The same status query must include display and virtual-SD progress fractions between zero and one.
Display progress must differ from byte progress by more than 0.000001 and represent an integer percentage within 0.000001 after multiplication by 100.
Under the standard [U1 display fallback](https://github.com/Snapmaker/u1-klipper/blob/7ac453b50fe6779b2ec0b759038a17da5e6d671d/klippy/extras/display_status.py#L20-L39) and [Orca M73 convention](https://github.com/OrcaSlicer/OrcaSlicer/blob/7e3d5d4d040a519815f62dc9dc94a0cf75ccb0c2/src/libslic3r/GCode/GCodeProcessor.cpp#L1455-L1471), this is conditional evidence of simulated-time progress.
Only raw state `printing`, model phase `printing`, and a finite speed factor within 0.000001 of one permit `planned_total * (1 - display_progress)`.
Paused jobs, speed overrides, missing inputs, and ambiguous source evidence have no finish forecast.
The UI rounds plan and remaining durations up to minutes and anchors finish time to the received snapshot without animating a countdown.
Elapsed time shows whole printer-reported seconds as `mm:ss` or `h:mm:ss`, refreshed with status rather than extrapolated locally.
The preparation bar uses a moving gradient without a percentage or accessibility value.
It is orange while a reported heater is more than 2 °C below target and returns to blue when warm.
Printing and finishing use a subtle blue highlight inside the filled portion of the bar; paused jobs retain an amber shimmer.
Active jobs without completed progress use an indeterminate activity bar.
Unavailable metric fields and finish estimates are omitted.
Job states with bars share the longer preparation width and fixed layout slots, so missing values do not move status labels or bars.
Entire absent sections, such as job progress in standby, still collapse.
Reduced-motion mode keeps all gradients still.

Metadata refreshes at most every 30 seconds, including successful entries, to observe updated metadata.
Filename or file-size changes, inactive-to-active transitions, and backwards clock or file-position movement clear cached metadata and fetch immediately.
Pause/resume without a reset remains the same run.
A failed or malformed refresh clears estimate metadata until the next bounded retry while live status remains usable.
A fresh connection starts without cached metadata and can estimate immediately from an eligible mid-print snapshot.
A metadata size that differs from a known active-reader file size is rejected.
Size and modified metadata do not prove content identity when an overwrite preserves the observed fields.
This bounded inference cannot certify unknown firmware, manual progress commands, or derivative slicers.
The installed Orca development build and full cold/warm accuracy still require field validation.

## Connection discovery

An explicit plugin host takes priority over the selected preset.
Without it, the plugin reads the selected U1 printer preset's connection settings through Orca's API.
Before reading the address (`print_host`) or API key (`printhost_apikey`), it asks for consent in a native dialog.
The decision is saved as `allow_preset_connection` in plugin Config and applies to all selected U1 presets.
Switching presets does not require another confirmation.
The current saved decision is checked before reading connection settings.
There is no cached Yes that can override a cleared or revoked decision.
After saving a decision, the plugin reads it back from Orca's effective configuration before allowing access.
Declining prevents both connection-setting reads and the JSON file fallback.
If the API has no address, it checks only that selected preset's JSON file, when available.
Orca's configured-device API deliberately omits IP addresses and credentials, and physical printer connections are not exposed through the preset-bundle API.
An address configured only in that device connection still needs the [manual host configuration](configuration.md#printer-connection).
The plugin does not read Orca's private app configuration or scan unrelated printer presets.
The API key is used only for requests to the selected printer and stays out of the camera page and logs.

## Network permissions

Orca can ask for several network permissions when the tab first connects to a printer.
It remembers approved permissions for later requests.
The plugin declares its background thread permission when it loads.
Orca's public plugin API cannot group network permissions for a host discovered after loading.

The preset connection confirmation is plugin-level consent, separate from Orca's audit permission prompts.
Orca allows file reads inside its data directory without an additional prompt.
Its `fs_read` declarations accept exact paths during registration, not wildcard paths or a preset selected later.
The plugin therefore asks explicitly before accessing connection settings, even when Orca does not show a file permission prompt.

## API references

- [Orca Pages API](https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/pages)
- [Orca plugin development](https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_development)
- [Orca permissions](https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/registry)
- [Orca selected-device API implementation](https://github.com/OrcaSlicer/OrcaSlicer/blob/main/src/slic3r/plugin/host/PluginHostApp.cpp)
- [Snapmaker's LED implementation](https://github.com/Snapmaker/u1-klipper/blob/main/klippy/extras/led.py)
- [Snapmaker's Fluidd light controls](https://github.com/Snapmaker/u1-fluidd/blob/main/src/components/widgets/outputs/OutputLed.vue)
- [Snapmaker's Fluidd adaptive camera](https://github.com/Snapmaker/u1-fluidd/blob/main/src/components/widgets/camera/services/MjpegstreamerAdaptiveCamera.vue)

## Page activity and connection changes

Activity messages renew the current generation without repeating target announcements or full connection discovery.
The page checks its effective plugin configuration on every activity or light callback.
Device selection, printer preset selection, and preset-save events invalidate the resolved connection immediately.
Orca does not expose a plugin configuration-change event, and preset connection fields can change without a selection event.

The UI callback therefore resolves connection details again after two seconds, including a saved selected-preset file when needed.
The refresh deadline does not move with routine renewals, so normal one-second activity detects unannounced changes within about three seconds.
For a live generation, frame callbacks also refresh connection details after two seconds instead of retaining unchecked credentials.
The callback copies connection values before background work and never passes live host or preset handles to the worker.

## Network deadlines

Each HTTP request and camera-monitor WebSocket operation has a two-second elapsed deadline.
This includes TCP connection attempts, TLS negotiation, HTTP headers and bodies, and WebSocket send and receive processing.
A watchdog is a timer that interrupts network waits.
It shuts down active sockets at the deadline, so a slow stream of bytes or control frames cannot keep resetting the wait.
Changing the target, stopping playback, leaving the page, or unloading the plugin cancels the current client's active socket I/O.
WebSocket redirects are disabled because redirected connections bypass the deadline's socket tracking.

The operating system's synchronous hostname resolver cannot be interrupted portably.
A stalled DNS lookup can delay the worker until the resolver returns, even after cancellation or the deadline.
The plugin checks cancellation and elapsed time immediately after resolution and does not open a late connection.
All resolved addresses share the remaining deadline.
The plugin does not restart the deadline for each address.
