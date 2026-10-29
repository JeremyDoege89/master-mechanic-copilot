/** Shared API helper (M6 §4). Same-origin by default; VITE_API_URL overrides. */

export const API_BASE: string =
  (import.meta as unknown as { env?: Record<string, string> }).env
    ?.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

export const apiGet = <T>(path: string): Promise<T> => request<T>(path);

export const apiPost = <T>(path: string, body: unknown): Promise<T> =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface LevelTrace {
  seq: number;
  evidence_id: string;
  test_id: string | null;
  expected: string;
  measured: string;
  prior: number;
  posterior: number;
  likelihood_ratio: number;
  lr_source: string;
  text_L1: string;
  text_L2: string;
  text_L3: string;
}

export interface Hypothesis {
  hypothesis_id: string;
  label: { L1: string; L2: string; L3: string };
  origin: string;
  prior: number;
  current: number;
  history: { seq: number; p: number; cause_event: string }[];
  trace: LevelTrace[];
}

export interface NextTest {
  rank: number;
  kp_test_ref: string;
  kind: string;
  eig_bits: number;
  cost: { minutes: number; usd: number; risk: number };
  score: number;
  why: string;
  expected_outcomes: { outcome: string; p: number; effect: Record<string, number> }[];
  required_tools: string[];
  min_level: string;
  safety_gate: string;
  instructions_by_level: { L1: string; L2: string; L3: string };
  gate_checklist?: { title: string; items: string[]; l1_extra: string[] };
  blocked_reasons: string[];
}

export interface BlockedTest {
  kp_test_ref: string;
  blocked_reasons: string[];
  kind: string;
  required_tools: string[];
  min_level: string;
  safety_gate: string;
  instructions_by_level: { L1: string; L2: string; L3: string };
  gate_checklist?: { title: string; items: string[]; l1_extra: string[] };
}

export interface SessionState {
  session_id: string;
  vehicle?: { vin_last6?: string } | null;
  hypotheses: Hypothesis[];
  next_tests: NextTest[];
  blocked_tests: BlockedTest[];
  missing_evidence: string[];
  tests_log: {
    test_ref: string;
    outcome: string;
    expected: unknown;
    measured: unknown;
    evaluator: string;
    evidence_ids: string[];
    result_id: string;
  }[];
  stop_and_see_pro?: { triggers: string[]; banner: string | null };
  vision_second_opinions?: unknown[];
}

export interface SessionEvent {
  seq: number;
  ts: string;
  type: string;
  payload: Record<string, unknown>;
}

export type Level = "L1" | "L2" | "L3";
