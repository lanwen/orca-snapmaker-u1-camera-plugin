export interface Heater {
  temperature?: number;
  target?: number;
}

export interface Toolhead {
  state:
    | 'connected' // Physically attached, or selected on firmware without park sensors.
    | 'changing' // Firmware is executing a tool change.
    | 'parked' // Park sensor confirms the head is docked.
    | 'unavailable' // No status object for this head.
    | 'unknown'; // Status exists but attachment is not established.
  material?: string | null;
  material_vendor?: string | null;
  material_subtype?: string | null;
  material_profile?: string | null;
  filament_present?: boolean | null;
  color?: string | null;
  nozzle_diameter?: number | null;
  heater?: Heater;
}

export interface Fan {
  speed?: number | null;
  rpm?: number | null;
}

export interface PrintStats {
  aux_fan?: Fan;
  part_fan?: Fan;
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
  tools?: Toolhead[];
  layer_height?: number | null;
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
