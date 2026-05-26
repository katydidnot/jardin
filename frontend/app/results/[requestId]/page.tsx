'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  Loader2,
  Check,
  AlertCircle,
  Flower2,
  Bug,
  Sprout,
  TreePine,
  Wheat,
  Maximize2,
  Leaf,
  ChevronLeft,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import SpeciesCard from '@/components/SpeciesCard';
import { makeStreamUrl } from '@/lib/api';
import type { AgentKey, AgentStatus, AgentStates, RecommendationItem, SSEPayload } from '@/lib/types';

// ─── Agent display config ─────────────────────────────────────────────────────

interface AgentConfig {
  key: AgentKey;
  label: string;
  Icon: LucideIcon;
  color: string;
}

const AGENTS: AgentConfig[] = [
  { key: 'pollinators',   label: 'Pollinators',    Icon: Flower2,   color: '#b45309' },  // harvest amber
  { key: 'insects',       label: 'Insects',         Icon: Bug,       color: '#3b6810' },  // avocado green
  { key: 'soil',          label: 'Soil health',     Icon: Sprout,    color: '#78350f' },  // warm brown-earth
  { key: 'environment',   label: 'Environment',     Icon: TreePine,  color: '#336516' },  // brand forest
  { key: 'food_utility',  label: 'Food & utility',  Icon: Wheat,     color: '#92400e' },  // harvest gold-brown
  { key: 'size',          label: 'Space fit',        Icon: Maximize2, color: '#1d4e6b' },  // deep teal-slate
];

const INITIAL_AGENTS: AgentStates = {
  pollinators: 'waiting',
  insects:     'waiting',
  soil:        'waiting',
  environment: 'waiting',
  food_utility:'waiting',
  size:        'waiting',
};

// ─── Agent progress card ──────────────────────────────────────────────────────

function AgentProgressCard({
  config,
  status,
  count,
}: {
  config: AgentConfig;
  status: AgentStatus;
  count?: number;
}) {
  const { Icon, label, color } = config;
  return (
    <div
      className={`flex items-center gap-3 p-4 rounded-xl border transition-all duration-300 ${
        status === 'complete'
          ? 'border-green-200 bg-green-50'
          : status === 'running'
          ? 'border-stone-200 bg-white shadow-sm'
          : status === 'error'
          ? 'border-red-200 bg-red-50'
          : 'border-stone-100 bg-stone-50'
      }`}
    >
      {/* Icon */}
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{
          backgroundColor:
            status === 'waiting' ? '#f5f5f4' : color + '18',
        }}
      >
        <Icon
          className="w-5 h-5"
          style={{ color: status === 'waiting' ? '#a8a29e' : color }}
        />
      </div>

      {/* Label + subtitle */}
      <div className="flex-1 min-w-0">
        <p
          className={`text-sm font-semibold ${
            status === 'waiting' ? 'text-stone-400' : 'text-stone-800'
          }`}
        >
          {label}
        </p>
        {status === 'complete' && count !== undefined && (
          <p className="text-xs text-green-700 mt-0.5">
            {count} species scored
          </p>
        )}
        {status === 'running' && (
          <p className="text-xs text-stone-500 mt-0.5">Analysing…</p>
        )}
        {status === 'waiting' && (
          <p className="text-xs text-stone-400 mt-0.5">Waiting</p>
        )}
        {status === 'error' && (
          <p className="text-xs text-red-600 mt-0.5">Error</p>
        )}
      </div>

      {/* Status indicator */}
      <div className="flex-shrink-0">
        {status === 'complete' && (
          <div className="w-6 h-6 rounded-full bg-green-600 flex items-center justify-center">
            <Check className="w-3.5 h-3.5 text-white" />
          </div>
        )}
        {status === 'running' && (
          <Loader2 className="w-5 h-5 text-green-700 animate-spin" />
        )}
        {status === 'waiting' && (
          <div className="w-5 h-5 rounded-full border-2 border-stone-300" />
        )}
        {status === 'error' && (
          <AlertCircle className="w-5 h-5 text-red-500" />
        )}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ResultsPage() {
  const { requestId } = useParams<{ requestId: string }>();

  const [phase, setPhase] = useState<
    'connecting' | 'fetching' | 'scoring' | 'done' | 'error'
  >('connecting');
  const [candidateCount, setCandidateCount] = useState<number | null>(null);
  const [agents, setAgents] = useState<AgentStates>({ ...INITIAL_AGENTS });
  const [agentCounts, setAgentCounts] = useState<Partial<Record<AgentKey, number>>>({});
  const [recommendations, setRecommendations] = useState<RecommendationItem[]>([]);
  const [errors, setErrors] = useState<string[]>([]);

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!requestId) return;

    const url = makeStreamUrl(requestId);
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (event: MessageEvent) => {
      let payload: SSEPayload;
      try {
        payload = JSON.parse(event.data as string);
      } catch {
        return;
      }

      switch (payload.event) {
        case 'fetch_complete':
          setCandidateCount(payload.candidate_count ?? null);
          setPhase('fetching');
          break;

        case 'agent_started':
          setPhase('scoring');
          if (payload.agent) {
            setAgents((prev) => ({ ...prev, [payload.agent!]: 'running' }));
          }
          break;

        case 'agent_complete':
          if (payload.agent) {
            setAgents((prev) => ({ ...prev, [payload.agent!]: 'complete' }));
            setAgentCounts((prev) => ({
              ...prev,
              [payload.agent!]: payload.count ?? 0,
            }));
          }
          break;

        case 'recommendations_ready':
          setRecommendations(payload.data ?? []);
          setPhase('done');
          es.close();
          break;

        case 'error':
          if (payload.message) {
            setErrors((prev) => [...prev, payload.message!]);
          }
          break;

        case 'ping':
          break;

        default:
          break;
      }
    };

    es.onerror = () => {
      if (phase !== 'done') {
        setPhase('error');
        setErrors((prev) => [
          ...prev,
          'Connection to the analysis stream was lost.',
        ]);
      }
      es.close();
    };

    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  const allComplete = AGENTS.every((a) => agents[a.key] === 'complete');

  return (
    <div className="min-h-dvh bg-stone-50">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-stone-50/90 backdrop-blur border-b border-stone-200 px-6 py-4 flex items-center gap-4">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-stone-500 hover:text-stone-800 transition-colors"
        >
          <ChevronLeft className="w-4 h-4" />
          New search
        </Link>
        <div className="flex items-center gap-2 ml-auto">
          <Leaf className="w-4 h-4 text-green-700" />
          <span className="font-serif font-bold text-green-900 text-base">
            jardin
          </span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* Status heading */}
        <div>
          {phase === 'connecting' && (
            <div className="flex items-center gap-3">
              <Loader2 className="w-5 h-5 text-green-700 animate-spin" />
              <p className="text-stone-600 text-sm">
                Connecting to analysis stream…
              </p>
            </div>
          )}
          {phase === 'fetching' && (
            <div>
              <h1 className="text-2xl font-bold text-green-950">
                Gathering candidates
              </h1>
              {candidateCount !== null && (
                <p className="mt-1 text-stone-500 text-sm">
                  Found {candidateCount} species — analysing with 6 expert
                  agents…
                </p>
              )}
            </div>
          )}
          {phase === 'scoring' && (
            <div>
              <h1 className="text-2xl font-bold text-green-950">
                Scoring species
              </h1>
              <p className="mt-1 text-stone-500 text-sm">
                Five expert agents are running in parallel.
              </p>
            </div>
          )}
          {phase === 'done' && (
            <div>
              <h1 className="text-2xl font-bold text-green-950">
                Your recommendations
              </h1>
              <p className="mt-1 text-stone-500 text-sm">
                {recommendations.length} native plants ranked for your garden.
              </p>
            </div>
          )}
          {phase === 'error' && (
            <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-5 py-4">
              <AlertCircle className="w-5 h-5 text-red-600 mt-0.5 flex-shrink-0" />
              <div>
                <p className="font-semibold text-red-800 text-sm">
                  Something went wrong
                </p>
                {errors.map((e, i) => (
                  <p key={i} className="text-xs text-red-700 mt-1">
                    {e}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Non-fatal errors */}
        {errors.length > 0 && phase !== 'error' && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-5 py-3">
            <p className="text-xs font-semibold text-amber-800 mb-1">
              Partial warnings
            </p>
            {errors.map((e, i) => (
              <p key={i} className="text-xs text-amber-700">
                {e}
              </p>
            ))}
          </div>
        )}

        {/* Agent progress — show until done */}
        {phase !== 'done' && phase !== 'error' && (
          <div className="space-y-2">
            {AGENTS.map((config) => (
              <AgentProgressCard
                key={config.key}
                config={config}
                status={agents[config.key]}
                count={agentCounts[config.key]}
              />
            ))}
          </div>
        )}

        {/* Keep progress visible after done (collapsed summary) */}
        {phase === 'done' && allComplete && (
          <details className="group">
            <summary className="cursor-pointer select-none text-sm text-stone-500 hover:text-stone-700 list-none flex items-center gap-1">
              <span className="group-open:hidden">▶</span>
              <span className="hidden group-open:inline">▼</span>
              Show agent summary
            </summary>
            <div className="mt-3 space-y-2">
              {AGENTS.map((config) => (
                <AgentProgressCard
                  key={config.key}
                  config={config}
                  status={agents[config.key]}
                  count={agentCounts[config.key]}
                />
              ))}
            </div>
          </details>
        )}

        {/* Recommendation grid */}
        {phase === 'done' && recommendations.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {recommendations.map((rec) => (
              <SpeciesCard key={rec.species.id} recommendation={rec} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
