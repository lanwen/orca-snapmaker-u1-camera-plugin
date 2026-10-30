# Connection and updates

[Back to the README](../README.md)

## Printer connection

Without an explicit host, the plugin asks to read the selected Snapmaker U1 preset's address and API key.
The confirmation also covers reading that selected preset's JSON file if Orca's API has no address.
Choose Yes to use the preset connection, or No to configure a host manually.
The plugin saves your choice as `allow_preset_connection` in Config.
Set it to `true` to allow access or `false` to disable access.
This choice applies to selected U1 presets and can be changed at any time.
If the page asks for an address, open the plugin's Config tab and save:

```json
{"host": "http://YOUR_PRINTER_IP"}
```

Use the port that serves Moonraker on your printer.
The stock U1 works on port 80.
An explicit port such as `:7125` is also accepted.
A configured host overrides the selected preset until you clear it.
With an explicit host, the plugin does not read the selected preset or ask for preset connection access.
It keeps the camera and light controls available regardless of the selected preset.
If the plugin cannot determine a valid address, the page shows a warning with configuration instructions.
Orca saves this host in its local plugin settings.

Use the manual host on a trusted LAN.
The stock HTTP connection is unencrypted.
The manual host does not reuse the selected preset's API key.
For an authenticated printer, use a selected preset that exposes both the host and API key through Orca's API.
See [connection discovery](reference.md#connection-discovery) for the API limitations.

## Updates

Deactivate the plugin, install the updated file, and reactivate it.
If Orca does not notice the update, use Refresh in the Plugins window.
Installed local files live under `~/Library/Application Support/OrcaSlicer/orca_plugins/` on macOS.
