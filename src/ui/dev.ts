import type { HostMessage } from './types';

// Offline bridge for the Vite preview. The plugin entry never imports this file.
let receive: (message: HostMessage) => void;
let light = true;
const image =
  'data:image/svg+xml,' +
  encodeURIComponent(
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
        camera: 'Preview',
        image: message.playing ? image : undefined,
        stats: {
          state: 'printing',
          progress: 0.42,
          current_layer: 36,
          total_layer: 86,
          elapsed: 2160,
          remaining: 3000,
          tool: 3,
          material: 'PLA',
          color: '#FFFFFF',
          bed: { temperature: 59, target: 60 },
          hotend: { temperature: 209, target: 210 },
        },
      });
    }
  },
};
void import('./main');
