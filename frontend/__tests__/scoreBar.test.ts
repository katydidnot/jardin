/**
 * Unit tests for components/ScoreBar.tsx
 *
 * Tests cover:
 * - scoreColor: colour thresholds for avocado green / amber / red
 */

import { describe, it, expect } from 'vitest';
import { scoreColor } from '../components/ScoreBar';

describe('scoreColor', () => {
  it('returns avocado green for high scores (≥ 0.7)', () => {
    expect(scoreColor(0.7)).toBe('#3b6810');
    expect(scoreColor(0.8)).toBe('#3b6810');
    expect(scoreColor(1.0)).toBe('#3b6810');
  });

  it('returns amber for moderate scores (0.4 – 0.69)', () => {
    expect(scoreColor(0.4)).toBe('#b45309');
    expect(scoreColor(0.5)).toBe('#b45309');
    expect(scoreColor(0.69)).toBe('#b45309');
  });

  it('returns warm red for low scores (< 0.4)', () => {
    expect(scoreColor(0.0)).toBe('#dc2626');
    expect(scoreColor(0.1)).toBe('#dc2626');
    expect(scoreColor(0.39)).toBe('#dc2626');
  });

  it('uses avocado green exactly at 0.7 boundary', () => {
    expect(scoreColor(0.7)).toBe('#3b6810');
  });

  it('uses amber exactly at 0.4 boundary', () => {
    expect(scoreColor(0.4)).toBe('#b45309');
  });

  it('uses red just below 0.4 boundary', () => {
    expect(scoreColor(0.399)).toBe('#dc2626');
  });
});
