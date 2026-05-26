import type {
  RecommendationCreated,
  RecommendationItem,
  SpeciesOut,
} from './types';

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ─── Recommendations ──────────────────────────────────────────────────────────

export interface CreateRecommendationParams {
  latitude: number;
  longitude: number;
  country_code: string;
  region?: string | null;
  priority_pollinators: number;   // 0-1
  priority_insects: number;
  priority_soil: number;
  priority_environment: number;
  priority_food_utility: number;
  priority_size: number;
  description?: string | null;
}

export async function createRecommendation(
  params: CreateRecommendationParams,
): Promise<RecommendationCreated> {
  const res = await fetch(`${API_URL}/api/recommendations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`POST /api/recommendations ${res.status}: ${body}`);
  }
  return res.json();
}

/** Build the SSE stream URL for a garden request. */
export function makeStreamUrl(requestId: string): string {
  return `${API_URL}/api/recommendations/${requestId}/stream`;
}

// ─── Species ──────────────────────────────────────────────────────────────────

export async function fetchSpecies(id: string): Promise<SpeciesOut> {
  const res = await fetch(`${API_URL}/api/species/${id}`);
  if (!res.ok) throw new Error(`GET /api/species/${id}: ${res.status}`);
  return res.json();
}

export async function searchSpecies(
  query: string,
  options?: { country?: string; limit?: number },
): Promise<SpeciesOut[]> {
  const params = new URLSearchParams({ q: query });
  if (options?.country) params.set('country', options.country);
  if (options?.limit) params.set('limit', String(options.limit));

  const res = await fetch(`${API_URL}/api/species/search?${params}`);
  if (!res.ok) throw new Error(`GET /api/species/search: ${res.status}`);
  return res.json();
}

// ─── Geocoding (Nominatim) ────────────────────────────────────────────────────

export interface NominatimResult {
  lat: string;
  lon: string;
  display_name: string;
  address: {
    country_code?: string;
    country?: string;
    state?: string;
    city?: string;
    town?: string;
  };
}

export async function geocode(
  query: string,
  countryCode?: string,
): Promise<NominatimResult[]> {
  const params = new URLSearchParams({
    q: query,
    format: 'json',
    limit: '5',
    addressdetails: '1',
  });
  if (countryCode) params.set('countrycodes', countryCode.toLowerCase());

  const res = await fetch(
    `https://nominatim.openstreetmap.org/search?${params}`,
    {
      headers: {
        // Nominatim usage policy requires a descriptive User-Agent
        'User-Agent': 'Jardin/1.0 (garden-planner; contact@example.com)',
        'Accept-Language': 'en',
      },
    },
  );
  if (!res.ok) throw new Error(`Nominatim ${res.status}`);
  return res.json();
}

// ─── Chat (streaming) ─────────────────────────────────────────────────────────

export interface ChatParams {
  species_id: string;
  message: string;
  history: Array<{ role: 'user' | 'assistant'; content: string }>;
}

/**
 * POST /api/chat and return the raw Response for streaming.
 * The caller reads the SSE body via ReadableStream.
 */
export async function postChat(params: ChatParams): Promise<Response> {
  const res = await fetch(`${API_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`POST /api/chat: ${res.status}`);
  return res;
}
