import type { RiskBand, Severity } from './types'

export const SEVERITIES: Severity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']

export const SEVERITY_RANK: Record<Severity, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
  INFO: 4,
}

/** Tailwind classes per severity, kept in one place so charts and badges agree. */
export const SEVERITY_STYLE: Record<Severity, { chip: string; dot: string; hex: string }> = {
  CRITICAL: {
    chip: 'bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/40',
    dot: 'bg-red-400',
    hex: '#f85149',
  },
  HIGH: {
    chip: 'bg-orange-500/15 text-orange-300 ring-1 ring-inset ring-orange-500/40',
    dot: 'bg-orange-400',
    hex: '#fb8500',
  },
  MEDIUM: {
    chip: 'bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/40',
    dot: 'bg-amber-400',
    hex: '#d29922',
  },
  LOW: {
    chip: 'bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/40',
    dot: 'bg-sky-400',
    hex: '#58a6ff',
  },
  INFO: {
    chip: 'bg-slate-500/15 text-slate-300 ring-1 ring-inset ring-slate-500/40',
    dot: 'bg-slate-400',
    hex: '#8b949e',
  },
}

export function severityStyle(severity: string) {
  return SEVERITY_STYLE[(severity?.toUpperCase() as Severity) ?? 'INFO'] ?? SEVERITY_STYLE.INFO
}

export const RISK_STYLE: Record<RiskBand, { text: string; bar: string; hex: string }> = {
  critical: { text: 'text-red-300', bar: 'bg-red-500', hex: '#f85149' },
  high: { text: 'text-orange-300', bar: 'bg-orange-500', hex: '#fb8500' },
  elevated: { text: 'text-amber-300', bar: 'bg-amber-500', hex: '#d29922' },
  moderate: { text: 'text-sky-300', bar: 'bg-sky-500', hex: '#58a6ff' },
  low: { text: 'text-emerald-300', bar: 'bg-emerald-500', hex: '#3fb950' },
}

export function riskStyle(band: string | null | undefined) {
  return RISK_STYLE[(band as RiskBand) ?? 'low'] ?? RISK_STYLE.low
}

export function bandForScore(score: number): RiskBand {
  if (score >= 80) return 'critical'
  if (score >= 60) return 'high'
  if (score >= 40) return 'elevated'
  if (score >= 20) return 'moderate'
  return 'low'
}

export const STATUS_STYLE: Record<string, string> = {
  completed: 'bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/40',
  failed: 'bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/40',
  queued: 'bg-slate-500/15 text-slate-300 ring-1 ring-inset ring-slate-500/40',
  processing: 'bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/40',
  analyzing: 'bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/40',
  reviewing: 'bg-indigo-500/15 text-indigo-300 ring-1 ring-inset ring-indigo-500/40',
  publishing: 'bg-violet-500/15 text-violet-300 ring-1 ring-inset ring-violet-500/40',
  cancelled: 'bg-slate-500/15 text-slate-400 ring-1 ring-inset ring-slate-500/40',
  open: 'bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/40',
  resolved: 'bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/40',
  dismissed: 'bg-slate-500/15 text-slate-400 ring-1 ring-inset ring-slate-500/40',
  acknowledged: 'bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/40',
}

export const ACTIVE_REVIEW_STATUSES = new Set([
  'queued',
  'processing',
  'analyzing',
  'reviewing',
  'publishing',
])
