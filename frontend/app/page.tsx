'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation } from '@tanstack/react-query';
import {
  MapPin,
  ChevronRight,
  ChevronLeft,
  Loader2,
  Check,
  Flower2,
  Bug,
  Sprout,
  TreePine,
  Wheat,
  Maximize2,
  Leaf,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import PrioritySlider from '@/components/PrioritySlider';
import {
  geocode,
  createRecommendation,
  type NominatimResult,
} from '@/lib/api';
import type { LocationData, Priorities } from '@/lib/types';

// ─── Country list ─────────────────────────────────────────────────────────────

const COUNTRIES = [
  { code: 'GB', name: 'United Kingdom' },
  { code: 'FR', name: 'France' },
  { code: 'DE', name: 'Germany' },
  { code: 'NL', name: 'Netherlands' },
  { code: 'BE', name: 'Belgium' },
  { code: 'IE', name: 'Ireland' },
  { code: 'ES', name: 'Spain' },
  { code: 'PT', name: 'Portugal' },
  { code: 'IT', name: 'Italy' },
  { code: 'SE', name: 'Sweden' },
  { code: 'NO', name: 'Norway' },
  { code: 'DK', name: 'Denmark' },
  { code: 'FI', name: 'Finland' },
  { code: 'PL', name: 'Poland' },
  { code: 'CZ', name: 'Czech Republic' },
  { code: 'AT', name: 'Austria' },
  { code: 'CH', name: 'Switzerland' },
  { code: 'US', name: 'United States' },
  { code: 'CA', name: 'Canada' },
  { code: 'AU', name: 'Australia' },
  { code: 'NZ', name: 'New Zealand' },
  { code: 'ZA', name: 'South Africa' },
];

interface PriorityDimension {
  key: keyof Priorities;
  label: string;
  description: string;
  Icon: LucideIcon;
  color: string;
}

const DIMENSIONS: PriorityDimension[] = [
  {
    key: 'pollinators',
    label: 'Pollinators',
    description:
      'Value to bees, butterflies, moths and hoverflies — nectar, pollen, and specialist relationships.',
    Icon: Flower2,
    color: '#b45309',   // harvest amber
  },
  {
    key: 'insects',
    label: 'Insect habitat',
    description:
      'Host plant for caterpillars, beetles, gall-makers, and other beneficial invertebrates.',
    Icon: Bug,
    color: '#3b6810',   // avocado green
  },
  {
    key: 'soil',
    label: 'Soil health',
    description:
      'Nitrogen fixation, mycorrhizal networks, deep tap-roots, and organic matter contribution.',
    Icon: Sprout,
    color: '#78350f',   // warm earth brown
  },
  {
    key: 'environment',
    label: 'Environment',
    description:
      'Carbon sequestration, water retention, microclimate regulation, and erosion control.',
    Icon: TreePine,
    color: '#336516',   // brand forest green
  },
  {
    key: 'food_utility',
    label: 'Food & utility',
    description:
      'Edible parts, medicinal uses, fibre, dyes, and other practical harvests.',
    Icon: Wheat,
    color: '#92400e',   // harvest gold-brown
  },
  {
    key: 'size',
    label: 'Space fit',
    description:
      'Prefer compact, clump-forming, or container-friendly plants over large trees and spreading species.',
    Icon: Maximize2,
    color: '#1d4e6b',   // deep teal-slate
  },
];

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepDots({ current }: { current: 1 | 2 | 3 }) {
  return (
    <div className="flex items-center gap-2" aria-label={`Step ${current} of 3`}>
      {([1, 2, 3] as const).map((n) => (
        <div
          key={n}
          className={`rounded-full transition-all duration-300 ${
            n === current
              ? 'w-6 h-2.5 bg-green-700'
              : n < current
              ? 'w-2.5 h-2.5 bg-green-400'
              : 'w-2.5 h-2.5 bg-stone-300'
          }`}
        />
      ))}
    </div>
  );
}

// ─── Step 1: Location ─────────────────────────────────────────────────────────

interface Step1Props {
  location: LocationData;
  onChange: (loc: LocationData) => void;
  onNext: () => void;
}

function Step1({ location, onChange, onNext }: Step1Props) {
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<NominatimResult[]>([]);
  const [error, setError] = useState('');

  const handleSearch = async () => {
    if (!location.query.trim()) return;
    setSearching(true);
    setError('');
    setResults([]);
    try {
      const hits = await geocode(
        location.query,
        location.countryCode || undefined,
      );
      if (!hits.length) {
        setError('No locations found. Try a more specific search term.');
      } else if (hits.length === 1) {
        onChange({
          ...location,
          lat: parseFloat(hits[0].lat),
          lng: parseFloat(hits[0].lon),
          displayName: hits[0].display_name,
          countryCode:
            hits[0].address.country_code?.toUpperCase() ||
            location.countryCode,
        });
      } else {
        setResults(hits);
      }
    } catch {
      setError('Could not reach the geocoding service. Check your connection.');
    } finally {
      setSearching(false);
    }
  };

  const selectResult = (hit: NominatimResult) => {
    onChange({
      ...location,
      lat: parseFloat(hit.lat),
      lng: parseFloat(hit.lon),
      displayName: hit.display_name,
      countryCode:
        hit.address.country_code?.toUpperCase() || location.countryCode,
    });
    setResults([]);
  };

  const confirmed = location.lat !== null && location.lng !== null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-green-950 leading-tight">
          Where is your garden?
        </h1>
        <p className="mt-2 text-stone-500 text-sm leading-relaxed">
          We'll recommend native plants suited to your region's climate and
          ecology.
        </p>
      </div>

      <div className="space-y-3">
        <label className="block">
          <span className="text-sm font-medium text-stone-700">
            Location name
          </span>
          <div className="relative mt-1">
            <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-stone-400" />
            <input
              type="text"
              value={location.query}
              onChange={(e) =>
                onChange({
                  ...location,
                  query: e.target.value,
                  lat: null,
                  lng: null,
                  displayName: '',
                })
              }
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="e.g. Brittany, Yorkshire, Tuscany…"
              className="w-full pl-9 pr-4 py-3 rounded-xl border border-stone-200 bg-white text-stone-900 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-green-700 text-sm"
            />
          </div>
        </label>

        <label className="block">
          <span className="text-sm font-medium text-stone-700">Country</span>
          <select
            value={location.countryCode}
            onChange={(e) =>
              onChange({
                ...location,
                countryCode: e.target.value,
                lat: null,
                lng: null,
              })
            }
            className="mt-1 w-full px-4 py-3 rounded-xl border border-stone-200 bg-white text-stone-900 focus:outline-none focus:ring-2 focus:ring-green-700 text-sm"
          >
            <option value="">Select a country…</option>
            {COUNTRIES.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        onClick={handleSearch}
        disabled={!location.query.trim() || searching}
        className="flex items-center gap-2 px-5 py-3 rounded-xl bg-stone-800 hover:bg-stone-900 disabled:bg-stone-200 disabled:text-stone-400 text-white text-sm font-semibold transition-colors"
      >
        {searching ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <MapPin className="w-4 h-4" />
        )}
        {searching ? 'Searching…' : 'Find location'}
      </button>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 rounded-xl px-4 py-3">
          {error}
        </p>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-stone-500 font-medium">
            Multiple matches found — choose one:
          </p>
          {results.map((hit, i) => (
            <button
              key={i}
              onClick={() => selectResult(hit)}
              className="w-full text-left px-4 py-3 rounded-xl border border-stone-200 bg-white hover:bg-green-50 hover:border-green-200 text-sm text-stone-700 transition-colors"
            >
              {hit.display_name}
            </button>
          ))}
        </div>
      )}

      {confirmed && (
        <div className="flex items-start gap-3 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
          <Check className="w-4 h-4 text-green-700 mt-0.5 flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-green-800">
              Location confirmed
            </p>
            <p className="text-xs text-green-700 mt-0.5 break-words">
              {location.displayName}
            </p>
          </div>
        </div>
      )}

      <button
        onClick={onNext}
        disabled={!confirmed}
        className="w-full flex items-center justify-center gap-2 py-3.5 rounded-xl bg-green-800 hover:bg-green-900 disabled:bg-stone-200 disabled:text-stone-400 text-white font-semibold text-sm transition-colors"
      >
        Continue
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  );
}

// ─── Step 2: Priorities ───────────────────────────────────────────────────────

interface Step2Props {
  priorities: Priorities;
  onChange: (p: Priorities) => void;
  onNext: () => void;
  onBack: () => void;
}

function Step2({ priorities, onChange, onNext, onBack }: Step2Props) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-green-950 leading-tight">
          What matters most?
        </h1>
        <p className="mt-2 text-stone-500 text-sm leading-relaxed">
          Adjust each slider to weight the ecological dimensions you care about.
          Plants will be ranked by your priorities.
        </p>
      </div>

      <div className="space-y-5">
        {DIMENSIONS.map((dim) => (
          <PrioritySlider
            key={dim.key}
            label={dim.label}
            description={dim.description}
            Icon={dim.Icon}
            value={priorities[dim.key]}
            color={dim.color}
            onChange={(v) => onChange({ ...priorities, [dim.key]: v })}
          />
        ))}
      </div>

      <div className="flex gap-3 pt-2">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-4 py-3 rounded-xl bg-stone-100 hover:bg-stone-200 text-stone-700 text-sm font-semibold transition-colors"
        >
          <ChevronLeft className="w-4 h-4" />
          Back
        </button>
        <button
          onClick={onNext}
          className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-green-800 hover:bg-green-900 text-white font-semibold text-sm transition-colors"
        >
          Continue
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ─── Step 3: Description + Submit ─────────────────────────────────────────────

interface Step3Props {
  description: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onBack: () => void;
  isSubmitting: boolean;
  error: string;
}

function Step3({
  description,
  onChange,
  onSubmit,
  onBack,
  isSubmitting,
  error,
}: Step3Props) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-green-950 leading-tight">
          Describe your garden
        </h1>
        <p className="mt-2 text-stone-500 text-sm leading-relaxed">
          Anything helps — soil type, aspect, size, existing plants, or goals.
          This context improves the recommendations.
        </p>
      </div>

      <label className="block">
        <span className="text-sm font-medium text-stone-700">
          Garden description{' '}
          <span className="font-normal text-stone-400">(optional)</span>
        </span>
        <textarea
          value={description}
          onChange={(e) => onChange(e.target.value)}
          rows={5}
          placeholder="e.g. Shady corner with clay soil near a small pond, looking to support local pollinators and add some edible plants…"
          className="mt-1 w-full px-4 py-3 rounded-xl border border-stone-200 bg-white text-stone-900 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-green-700 text-sm leading-relaxed resize-none"
        />
      </label>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 rounded-xl px-4 py-3">
          {error}
        </p>
      )}

      <div className="flex gap-3">
        <button
          onClick={onBack}
          disabled={isSubmitting}
          className="flex items-center gap-1.5 px-4 py-3 rounded-xl bg-stone-100 hover:bg-stone-200 text-stone-700 text-sm font-semibold transition-colors disabled:opacity-50"
        >
          <ChevronLeft className="w-4 h-4" />
          Back
        </button>
        <button
          onClick={onSubmit}
          disabled={isSubmitting}
          className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-green-800 hover:bg-green-900 disabled:bg-stone-300 disabled:text-stone-500 text-white font-semibold text-sm transition-colors"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Starting…
            </>
          ) : (
            <>
              Get recommendations
              <ChevronRight className="w-4 h-4" />
            </>
          )}
        </button>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const DEFAULT_PRIORITIES: Priorities = {
  pollinators: 60,
  insects: 50,
  soil: 50,
  environment: 50,
  food_utility: 40,
  size: 50,
};

const DEFAULT_LOCATION: LocationData = {
  query: '',
  displayName: '',
  lat: null,
  lng: null,
  countryCode: '',
};

export default function GardenWizardPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [location, setLocation] = useState<LocationData>(DEFAULT_LOCATION);
  const [priorities, setPriorities] = useState<Priorities>(DEFAULT_PRIORITIES);
  const [description, setDescription] = useState('');

  const mutation = useMutation({
    mutationFn: createRecommendation,
    onSuccess: (data) => {
      router.push(`/results/${data.request_id}`);
    },
  });

  const handleSubmit = () => {
    if (!location.lat || !location.lng) return;
    const scale = (v: number) => v / 100;
    mutation.mutate({
      latitude: location.lat,
      longitude: location.lng,
      country_code: location.countryCode || 'GB',
      region: location.displayName.split(',')[0]?.trim() || null,
      priority_pollinators: scale(priorities.pollinators),
      priority_insects: scale(priorities.insects),
      priority_soil: scale(priorities.soil),
      priority_environment: scale(priorities.environment),
      priority_food_utility: scale(priorities.food_utility),
      priority_size: scale(priorities.size),
      description: description.trim() || null,
    });
  };

  return (
    <div className="min-h-dvh flex flex-col">
      {/* Header */}
      <header className="px-6 pt-6 pb-2 flex items-center justify-between max-w-md mx-auto w-full">
        <div className="flex items-center gap-2">
          <Leaf className="w-5 h-5 text-green-700" />
          <span className="font-serif font-bold text-green-900 text-lg">
            jardin
          </span>
        </div>
        <StepDots current={step} />
      </header>

      {/* Wizard */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 py-8">
        <div className="w-full max-w-md">
          {step === 1 && (
            <Step1
              location={location}
              onChange={setLocation}
              onNext={() => setStep(2)}
            />
          )}
          {step === 2 && (
            <Step2
              priorities={priorities}
              onChange={setPriorities}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}
          {step === 3 && (
            <Step3
              description={description}
              onChange={setDescription}
              onSubmit={handleSubmit}
              onBack={() => setStep(2)}
              isSubmitting={mutation.isPending}
              error={mutation.error?.message ?? ''}
            />
          )}
        </div>
      </main>

      <footer className="pb-6 text-center text-xs text-stone-400">
        Powered by GBIF · Claude AI · pgvector
      </footer>
    </div>
  );
}
