// ─── Species ──────────────────────────────────────────────────────────────────

export interface SpeciesOut {
  id: string;
  scientific_name: string;
  common_names: string[];
  family: string | null;
  description: string | null;
  food_utility_notes: string | null;
  pollinator_score: number | null;
  insect_host_score: number | null;
  soil_benefit_score: number | null;
  environmental_score: number | null;
  food_utility_score: number | null;
  /** Optional — present if backend exposes it. 1-indexed month numbers 1-12. */
  bloom_months?: number[] | null;
}

// ─── Recommendations ──────────────────────────────────────────────────────────

export interface ScoreBreakdown {
  pollinators: number;
  insects: number;
  soil: number;
  environment: number;
  food_utility: number;
  size: number;
}

export interface RecommendationItem {
  rank: number;
  final_score: number;
  score_breakdown: ScoreBreakdown;
  agent_reasoning: Record<string, string>;
  species: SpeciesOut;
}

export interface RecommendationCreated {
  request_id: string;
  status: string;
  stream_url: string;
}

// ─── Garden wizard ────────────────────────────────────────────────────────────

export interface Priorities {
  pollinators: number;   // 0-100 from slider
  insects: number;
  soil: number;
  environment: number;
  food_utility: number;
  size: number;
}

export interface LocationData {
  query: string;
  displayName: string;
  lat: number | null;
  lng: number | null;
  countryCode: string;
}

// ─── SSE stream ───────────────────────────────────────────────────────────────

export type AgentKey = 'pollinators' | 'insects' | 'soil' | 'environment' | 'food_utility' | 'size';
export type AgentStatus = 'waiting' | 'running' | 'complete' | 'error';
export type AgentStates = Record<AgentKey, AgentStatus>;

export interface SSEPayload {
  event: string;
  agent?: AgentKey;
  count?: number;
  candidate_count?: number;
  data?: RecommendationItem[];
  message?: string;
  text?: string;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
