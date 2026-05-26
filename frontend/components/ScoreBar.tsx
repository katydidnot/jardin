'use client';

/**
 * Shared ecological score bar component.
 * Used on both the species detail page and inside SpeciesCard.
 */

// ─── Palette ──────────────────────────────────────────────────────────────────

/** Map a 0–1 score to an earthy colour: avocado green / amber / warm red. */
export function scoreColor(score: number): string {
  if (score >= 0.7) return '#3b6810';   // avocado green-700
  if (score >= 0.4) return '#b45309';   // warm amber-700
  return '#dc2626';                     // warm red-600
}

// ─── Compact bar (used in SpeciesCard grid) ───────────────────────────────────

interface ScoreBarProps {
  label: string;
  icon: React.ReactNode;
  score: number | null;
  /** Extra px height; defaults to 8 (h-2). */
  barHeight?: number;
}

export function ScoreBar({ label, icon, score, barHeight = 8 }: ScoreBarProps) {
  const val = score ?? 0;
  const pct = Math.round(val * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="text-stone-400 flex-shrink-0">{icon}</span>
      <span className="w-20 text-xs text-stone-500 text-right flex-shrink-0">
        {label}
      </span>
      <div className="flex-1 bg-stone-100 rounded-full min-w-0" style={{ height: barHeight }}>
        <div
          className="rounded-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            height: barHeight,
            backgroundColor: scoreColor(val),
          }}
        />
      </div>
      <span className="w-7 text-right text-xs text-stone-400 flex-shrink-0 tabular-nums">
        {pct}
      </span>
    </div>
  );
}

// ─── Row variant (used on species detail page) ────────────────────────────────

interface ScoreRowProps {
  icon: React.ReactNode;
  label: string;
  score: number | null;
}

export function ScoreRow({ icon, label, score }: ScoreRowProps) {
  const val = score ?? 0;
  const pct = Math.round(val * 100);
  const color = scoreColor(val);

  return (
    <div className="flex items-center gap-3">
      <span className="text-stone-400 w-5 flex-shrink-0">{icon}</span>
      <span className="w-28 text-sm text-stone-600 flex-shrink-0">{label}</span>
      <div className="flex-1 bg-stone-100 rounded-full h-2.5">
        <div
          className="h-2.5 rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span
        className="w-8 text-right text-sm font-semibold tabular-nums"
        style={{ color }}
      >
        {pct}
      </span>
    </div>
  );
}
