A dedicated Snapmaker U1 tab with a full-viewport camera, printer status, and a chamber light toggle.

## Install

Use an OrcaSlicer nightly with the Python Pages plugin API and a Snapmaker U1 reachable on your local network.

1. Open File → Plugins and search for `Snapmaker U1`.
2. Install the camera plugin and activate it.
3. Allow the background thread and printer access when prompted.

Orca installs the required Python dependencies.

### Install from a local file

1. Download `orca-snapmaker-u1-camera-plugin_any.py` from [Releases](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/releases).
2. Open File → Plugins → Install local plugin and select the file.
3. Activate `orca-snapmaker-u1-camera-plugin` and allow the requested permissions.

## Connect your printer

Select a Snapmaker U1 printer in Prepare, then open the Snapmaker U1 tab.

The plugin asks once before reading selected U1 presets' addresses and API keys and remembers your choice.
Choose Yes to use that connection, or No to configure an address manually.
If the page asks for an address, open the plugin's Config tab and save:

```json
{"host": "http://YOUR_PRINTER_IP"}
```

The saved host overrides the selected preset's connection settings until you clear it.
Use a trusted local network because the stock HTTP connection is unencrypted.

## Use

Click the play overlay to start the camera.
Hover over the player to reveal Stop.
Leaving the tab stops playback.
Switching to another app keeps playback running if the tab remains visible.

Use the light toggle with playback running or stopped.
The light stays in its last state after leaving the page.
The stock camera feed delivers roughly 4–5 FPS.

## Data access

With your saved consent, the plugin reads `print_host` and `printhost_apikey` from the selected U1 preset.
If Orca's API has no address, it reads only that selected preset's JSON file.
The API key is used only to connect to that printer and stays out of the camera page and logs.
The plugin does not read Orca's private app configuration or scan other presets.
An explicit plugin host bypasses preset access entirely.
You can revoke access by setting `allow_preset_connection` to `false` in Config, or set it to `null` to ask again.

## Project

Source code, updates, and documentation: [orca-snapmaker-u1-camera-plugin](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin).
This community project uses the [MIT license](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/blob/main/LICENSE).
