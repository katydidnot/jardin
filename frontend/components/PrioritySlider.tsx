'use client';

import type { LucideIcon } from 'lucide-react';

interface PrioritySliderProps {
  label: string;
  description: string;
  Icon: LucideIcon;
  value: number;         // 0–100
  onChange: (value: number) => void;
  color?: string;        // hex or tailwind colour — used for the track fill
}

/**
 * Earthy, organic range slider for one ecological priority dimension.
 * The track is partially filled to reflect the current value.
 */
export default function PrioritySlider({
  label,
  description,
  Icon,
  value,
  onChange,
  color = '#336516',
}: PrioritySliderProps) {
  return (
    <div className="group">
      <div className="flex items-start gap-3 mb-2">
        {/* Icon badge */}
        <div
          className="mt-0.5 flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: color + '18' }}
        >
          <Icon className="w-5 h-5" style={{ color }} />
        </div>

        {/* Label + description */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-stone-800">{label}</span>
            <span
              className="text-sm font-bold tabular-nums"
              style={{ color }}
            >
              {value}
            </span>
          </div>
          <p className="text-xs text-stone-500 mt-0.5 leading-relaxed">
            {description}
          </p>
        </div>
      </div>

      {/* Slider track — CSS background trick gives a filled-left appearance */}
      <div className="pl-12">
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full"
          style={{
            background: `linear-gradient(to right, ${color} 0%, ${color} ${value}%, #d6d3d1 ${value}%, #d6d3d1 100%)`,
          }}
          aria-label={`${label} priority: ${value}`}
        />
        <div className="flex justify-between text-[10px] text-stone-400 mt-1 select-none">
          <span>Less</span>
          <span>More</span>
        </div>
      </div>
    </div>
  );
}
