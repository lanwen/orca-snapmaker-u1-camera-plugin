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
  // Eligible current estimate; zero only after completion.
  remaining?: number | null;
  planned_total?: number | null;
  phase?:
    | 'preparing' // Layer zero, including purge and heating.
    | 'printing' // A valid positive model layer.
    | 'finishing' // Confirmed M73 completion while the job remains active.
    | 'unknown'; // Missing or contradictory phase evidence.
  tool?: number;
  material?: string;
  material_vendor?: string | null;
  material_subtype?: string | null;
  material_profile?: string | null;
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
  fps?: number;
  frozen?: boolean;
  light_error?: string;
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
