# Connection and updates

[Back to the README](../README.md)

## Printer connection

Without an explicit host, the plugin asks to read the selected Snapmaker U1 preset's address and API key.
The confirmation also covers reading that selected preset's JSON file if Orca's API has no address.
Choose Yes to use the preset connection, or No to configure a host manually.
The plugin saves a separate Yes or No for each preset in `allow_preset_connection` in Config.
It identifies the preset by its file path, or by its name when no file path is available.
Selecting another preset requires that preset's own approval.
Returning to an approved preset does not show another prompt.
The current saved decision is checked before each connection-setting read.

To revoke one preset's permission, change its entry to `false`.
Remove that entry to ask again, or set `allow_preset_connection` to `{}` to clear all decisions.
Set the entire field to `false` to disable all preset connection access.
Older global `true` approvals require a new confirmation for each preset.
If the plugin cannot save the decision, it blocks access and asks you to configure a host manually or reload the plugin.
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
