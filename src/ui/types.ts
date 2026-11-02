export interface Heater {
  temperature?: number;
  target?: number;
}

export interface PrintStats {
  state: string;
  progress?: number;
  current_layer?: number;
  total_layer?: number;
  elapsed?: number;
  printing_duration?: number;
  remaining?: number | null;
  tool?: number;
  material?: string;
  color?: string;
  bed?: Heater;
  hotend?: Heater;
}

export interface HostMessage {
  type?: string;
  generation?: number;
  target?: string;
  error?: string;
  supported?: boolean;
  printer?: string;
  stats?: PrintStats | null;
  light?: boolean | null;
  camera?: string;
  failed?: boolean;
  image?: string;
}

export interface OrcaBridge {
  onMessage(callback: (message: HostMessage) => void): void;
  postMessage(message: {
    type: string;
    active?: boolean;
    playing?: boolean;
    enabled?: boolean;
    generation?: number;
  }): void;
}

declare global {
  interface Window {
    orca: OrcaBridge;
  }
}
