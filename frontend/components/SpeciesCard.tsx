'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  ChevronDown,
  ChevronUp,
  Flower2,
  Bug,
  Sprout,
  TreePine,
  Wheat,
  Maximize2,
  MessageCircle,
  Utensils,
} from 'lucide-react';
import type { RecommendationItem } from '@/lib/types';
import { ScoreBar } from '@/components/ScoreBar';

// ─── Bloom calendar ───────────────────────────────────────────────────────────

const MONTH_ABBRS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function BloomBar({ months }: { months?: number[] | null }) {
  if (!months?.length) return null;
  const active = new Set(months);
  return (
    <div>
      <p className="text-xs font-medium text-stone-600 mb-1.5">Bloom season</p>
      <div className="flex gap-0.5">
        {MONTH_ABBRS.map((m, i) => (
          <div
            key={i}
            className={`flex-1 h-6 rounded flex items-center justify-center text-[9px] font-semibold select-none ${
              active.has(i + 1)
                ? 'text-white'
                : 'bg-stone-100 text-stone-400'
            }`}
            style={active.has(i + 1) ? { backgroundColor: '#3b6810' } : undefined}
            title={m}
          >
            {m[0]}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

interface SpeciesCardProps {
  recommendation: RecommendationItem;
}

export default function SpeciesCard({ recommendation }: SpeciesCardProps) {
  const [expanded, setExpanded] = useState(false);
  const { species, rank, final_score, score_breakdown, agent_reasoning } = recommendation;

  const primaryCommonName =
    species.common_names[0] ??
    species.scientific_name.split(' ').slice(0, 2).join(' ');

  const isFoodWorthy =
    (species.food_utility_score ?? 0) > 0.6 || !!species.food_utility_notes;

  // Prefer the agent's contextual score_breakdown over static DB defaults (0.5)
  const scores: Array<{
    label: string;
    key: keyof typeof score_breakdown;
    icon: React.ReactNode;
    raw: number | null;
  }> = [
    {
      label: 'Pollinators',
      key: 'pollinators',
      icon: <Flower2 className="w-3.5 h-3.5" />,
      raw: score_breakdown?.pollinators ?? species.pollinator_score,
    },
    {
      label: 'Insects',
      key: 'insects',
      icon: <Bug className="w-3.5 h-3.5" />,
      raw: score_breakdown?.insects ?? species.insect_host_score,
    },
    {
      label: 'Soil',
      key: 'soil',
      icon: <Sprout className="w-3.5 h-3.5" />,
      raw: score_breakdown?.soil ?? species.soil_benefit_score,
    },
    {
      label: 'Environment',
      key: 'environment',
      icon: <TreePine className="w-3.5 h-3.5" />,
      raw: score_breakdown?.environment ?? species.environmental_score,
    },
    {
      label: 'Food',
      key: 'food_utility',
      icon: <Wheat className="w-3.5 h-3.5" />,
      raw: score_breakdown?.food_utility ?? species.food_utility_score,
    },
    {
      label: 'Space fit',
      key: 'size',
      icon: <Maximize2 className="w-3.5 h-3.5" />,
      raw: score_breakdown?.size ?? null,
    },
  ];

  return (
    <article className="bg-white rounded-2xl border border-stone-200 shadow-sm hover:shadow-md transition-shadow duration-200 overflow-hidden flex flex-col">
      {/* Rank + score ribbon */}
      <div className="flex items-center justify-between px-5 pt-4 pb-0">
        <span className="text-xs font-semibold text-stone-400 tracking-wider uppercase">
          #{rank}
        </span>
        <span
          className="text-xs font-bold rounded-full px-2.5 py-0.5"
          style={{ color: '#336516', backgroundColor: '#edf2dc' }}
        >
          {Math.round(final_score * 100)}
        </span>
      </div>

      {/* Header */}
      <div className="px-5 pt-2 pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-stone-900 leading-snug truncate">
              {primaryCommonName}
            </h3>
            <p className="text-sm text-stone-400 italic mt-0.5 truncate">
              {species.scientific_name}
            </p>
            {species.family && (
              <p className="text-xs text-stone-400 mt-0.5">{species.family}</p>
            )}
          </div>
          {isFoodWorthy && (
            <span
              className="flex-shrink-0 flex items-center gap-1 text-[10px] font-semibold rounded-full px-2 py-0.5 mt-0.5"
              style={{ color: '#92400e', backgroundColor: '#fef3c7', border: '1px solid #fde68a' }}
              title="High food / medicinal value"
            >
              <Utensils className="w-3 h-3" />
              Edible
            </span>
          )}
        </div>
      </div>

      {/* Score bars */}
      <div className="px-5 space-y-1.5">
        {scores.map((s) => (
          <ScoreBar key={s.key} label={s.label} icon={s.icon} score={s.raw} />
        ))}
      </div>

      {/* Expand / collapse toggle */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-center gap-1 mt-4 mx-5 mb-0 py-2.5 rounded-xl bg-stone-50 hover:bg-stone-100 text-xs font-medium text-stone-500 transition-colors w-[calc(100%-2.5rem)]"
        aria-expanded={expanded}
      >
        {expanded ? (
          <>
            <ChevronUp className="w-3.5 h-3.5" />
            Less detail
          </>
        ) : (
          <>
            <ChevronDown className="w-3.5 h-3.5" />
            More detail
          </>
        )}
      </button>

      {/* Expanded panel */}
      {expanded && (
        <div className="px-5 pt-4 pb-5 space-y-4 border-t border-stone-100 mt-3">
          {species.description && (
            <div>
              <p className="text-xs font-medium text-stone-600 mb-1">About</p>
              <p className="text-sm text-stone-700 leading-relaxed line-clamp-5">
                {species.description}
              </p>
            </div>
          )}

          <BloomBar months={species.bloom_months} />

          {species.food_utility_notes && (
            <div>
              <p className="text-xs font-medium text-stone-600 mb-1 flex items-center gap-1">
                <Utensils className="w-3 h-3" />
                Food &amp; medicinal uses
              </p>
              <p className="text-sm text-stone-700 leading-relaxed">
                {species.food_utility_notes}
              </p>
            </div>
          )}

          {/* Agent reasoning accordion */}
          {Object.keys(agent_reasoning).length > 0 && (
            <details className="group">
              <summary className="text-xs font-medium text-stone-500 cursor-pointer select-none list-none flex items-center gap-1 hover:text-stone-700">
                <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-180" />
                Expert reasoning
              </summary>
              <div className="mt-2 space-y-2">
                {Object.entries(agent_reasoning).map(([dim, text]) => (
                  <div key={dim}>
                    <p className="text-[10px] uppercase tracking-wider font-semibold text-stone-400">
                      {dim.replace('_', ' ')}
                    </p>
                    <p className="text-xs text-stone-600 leading-relaxed">{text}</p>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* CTA buttons */}
          <div className="flex gap-2 pt-1">
            <Link
              href={`/species/${species.id}`}
              className="flex-1 flex items-center justify-center gap-1.5 rounded-xl text-white text-xs font-semibold py-2.5 transition-colors"
              style={{ backgroundColor: '#376113' }}
            >
              Full profile
            </Link>
            <Link
              href={`/species/${species.id}#chat`}
              className="flex items-center justify-center gap-1.5 rounded-xl bg-stone-100 hover:bg-stone-200 text-stone-700 text-xs font-semibold px-4 py-2.5 transition-colors"
            >
              <MessageCircle className="w-3.5 h-3.5" />
              Ask
            </Link>
          </div>
        </div>
      )}

      <div className="pb-5" />
    </article>
  );
}
