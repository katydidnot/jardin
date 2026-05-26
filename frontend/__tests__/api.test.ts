/**
 * Unit tests for lib/api.ts
 *
 * Tests cover:
 * - makeStreamUrl: URL construction
 * - createRecommendation: request shaping, error handling
 * - geocode: parameter forwarding
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  makeStreamUrl,
  createRecommendation,
  geocode,
  API_URL,
  type CreateRecommendationParams,
} from '../lib/api';

// ─── Fetch mock ────────────────────────────────────────────────────────────────

const mockFetch = vi.fn();
beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});
afterEach(() => {
  vi.restoreAllMocks();
});

// ─── makeStreamUrl ─────────────────────────────────────────────────────────────

describe('makeStreamUrl', () => {
  it('returns the correct SSE endpoint URL', () => {
    const url = makeStreamUrl('abc-123');
    expect(url).toBe(`${API_URL}/api/recommendations/abc-123/stream`);
  });

  it('includes the request ID verbatim', () => {
    const id = 'some-uuid-here';
    expect(makeStreamUrl(id)).toContain(id);
  });
});

// ─── createRecommendation ─────────────────────────────────────────────────────

const BASE_PARAMS: CreateRecommendationParams = {
  latitude: 51.5,
  longitude: -0.1,
  country_code: 'GB',
  priority_pollinators: 0.6,
  priority_insects: 0.5,
  priority_soil: 0.5,
  priority_environment: 0.5,
  priority_food_utility: 0.4,
  priority_size: 0.5,
};

describe('createRecommendation', () => {
  it('calls the correct endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ request_id: 'abc', status: 'processing', stream_url: '/stream' }),
    });
    await createRecommendation(BASE_PARAMS);
    expect(mockFetch).toHaveBeenCalledWith(
      `${API_URL}/api/recommendations`,
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('sends JSON body with all parameters', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ request_id: 'abc', status: 'processing', stream_url: '/stream' }),
    });
    await createRecommendation(BASE_PARAMS);
    const [, init] = mockFetch.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.latitude).toBe(51.5);
    expect(body.country_code).toBe('GB');
    expect(body.priority_pollinators).toBe(0.6);
    expect(body.priority_size).toBe(0.5);
  });

  it('returns the parsed response on success', async () => {
    const expected = { request_id: 'test-id', status: 'processing', stream_url: '/stream/test-id' };
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => expected });
    const result = await createRecommendation(BASE_PARAMS);
    expect(result).toEqual(expected);
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });
    await expect(createRecommendation(BASE_PARAMS)).rejects.toThrow('500');
  });

  it('sets Content-Type to application/json', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ request_id: 'abc', status: 'processing', stream_url: '/stream' }),
    });
    await createRecommendation(BASE_PARAMS);
    const [, init] = mockFetch.mock.calls[0];
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });
});

// ─── geocode ──────────────────────────────────────────────────────────────────

describe('geocode', () => {
  it('calls the Nominatim search endpoint', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await geocode('London');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('nominatim.openstreetmap.org/search'),
      expect.any(Object),
    );
  });

  it('includes the query string in the URL', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await geocode('Paris');
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain('q=Paris');
  });

  it('includes countryCode when provided', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await geocode('Paris', 'FR');
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain('countrycodes=fr');
  });

  it('does not include countrycodes when not provided', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await geocode('Berlin');
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).not.toContain('countrycodes=');
  });

  it('requests JSON format and address details', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await geocode('Berlin');
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain('format=json');
    expect(url).toContain('addressdetails=1');
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 429 });
    await expect(geocode('London')).rejects.toThrow('429');
  });

  it('returns the parsed results array', async () => {
    const results = [{ lat: '51.5', lon: '-0.1', display_name: 'London', address: {} }];
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => results });
    const out = await geocode('London');
    expect(out).toEqual(results);
  });
});
