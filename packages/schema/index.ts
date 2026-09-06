// Wire contracts shared across the monorepo.
// The canonical definition is telemetry.schema.json + the Python mirror in
// python/aurabee_schema/. Keep all three in step.

export const TELEMETRY_FRAME_VERSION = 1 as const;

export interface AcousticFeatures {
  mfcc?: number[];
  band_energy?: number[];
  peak_hz?: number;
  centroid_hz?: number;
}

export interface TelemetryFrame {
  v: typeof TELEMETRY_FRAME_VERSION;
  node_id: string;
  ts: number;           // unix seconds, UTC
  seq: number;          // monotonic per node
  weight_kg?: number | null;
  t_in?: number | null;
  rh_in?: number | null;
  t_out?: number | null;
  rh_out?: number | null;
  sound_rms?: number | null;
  entrance_in?: number | null;
  entrance_out?: number | null;
  batt_v?: number | null;
  lat?: number | null;
  lon?: number | null;
  features?: AcousticFeatures | null;
  sig: string;
}

export const topics = {
  telemetry: (nodeId: string) => `aurabee/telemetry/${nodeId}`,
  telemetryWildcard: 'aurabee/telemetry/+',
  command: (nodeId: string) => `aurabee/cmd/${nodeId}`,
  status: (nodeId: string) => `aurabee/status/${nodeId}`,
} as const;

export type HiveEventType =
  | 'queenless' | 'swarm' | 'pre_swarm' | 'absconding' | 'robbing'
  | 'varroa' | 'wax_moth' | 'starvation' | 'theft'
  | 'node_offline' | 'sensor_fault' | 'healthy';

export type BatchStatus =
  | 'draft' | 'minted' | 'in_transit' | 'processing'
  | 'packed' | 'sold' | 'flagged' | 'void';
