import type { HostMessage } from './types';

// Offline bridge for the Vite preview. The plugin entry never imports this file.
let receive: (message: HostMessage) => void;
let light = true;
const image =
  'data:image/svg+xml;base64,' +
  btoa(
    '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="100%" height="100%" fill="#0b0e12"/><text x="50%" y="50%" text-anchor="middle" fill="#778899" font-family="sans-serif" font-size="24">Camera preview</text></svg>',
  );
window.orca = {
  onMessage(callback) {
    receive = callback;
  },
  postMessage(message) {
    if (message.type === 'light') light = message.enabled === true;
    if ((message.type === 'activity' && message.active) || message.type === 'light') {
      receive({ type: 'target', generation: 1, target: 'Workshop · Snapmaker U1' });
      receive({
        generation: 1,
        printer: 'Printing',
        light,
        fps: 4.4,
        frozen: false,
        image: message.playing ? image : undefined,
        stats: {
          state: 'printing',
          phase: 'printing',
          planned_total: 5200,
          progress: 0.42,
          current_layer: 36,
          total_layer: 86,
          elapsed: 2160,
          remaining: 3000,
          tool: 3,
          layer_height: 0.2,
          tools: [
            { state: 'parked', material: 'PLA', color: '#FFB387', nozzle_diameter: 0.4 },
            { state: 'parked', material: 'PETG', color: '#83CEE5', nozzle_diameter: 0.6 },
            { state: 'unavailable' },
            {
              state: 'connected',
              material: 'PLA',
              material_vendor: 'Snapmaker',
              material_subtype: 'SnapSpeed',
              material_profile: 'Snapmaker PLA SnapSpeed @Snapmaker U1',
              color: '#FFFFFF',
              nozzle_diameter: 0.4,
              heater: { temperature: 209, target: 210 },
            },
          ],
          material: 'PLA',
          material_vendor: 'Snapmaker',
          material_subtype: 'SnapSpeed',
          material_profile: 'Snapmaker PLA SnapSpeed @Snapmaker U1 (0.4 nozzle)',
          color: '#FFFFFF',
          psu_fan: { speed: 0.6 },
          hotend_fan: { speed: 0.75, rpm: 16761 },
          exhaust_fan: { speed: 0.6 },
          circulation_fan: { speed: 0, rpm: 0 },
          aux_fan: { speed: 0.7, rpm: 3200 },
          part_fan: { speed: 1, rpm: 8200 },
          bed: { temperature: 59, target: 60 },
          hotend: { temperature: 209, target: 210 },
        },
      });
    }
  },
};
void import('./main');
