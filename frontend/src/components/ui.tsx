import clsx from 'clsx'
import type { ReactNode } from 'react'

import { riskStyle, severityStyle, STATUS_STYLE } from '../lib/severity'
import { titleCase } from '../lib/format'

export function Card({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <section className={clsx('card', className)}>{children}</section>
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
}) {
  return (
    <header className="card-header">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold text-slate-100">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p> : null}
      </div>
      {action}
    </header>
  )
}

export function SeverityBadge({ severity }: { severity: string }) {
  const style = severityStyle(severity)
  return (
    <span className={clsx('chip', style.chip)}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', style.dot)} />
      {severity.toUpperCase()}
    </span>
  )
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-500">—</span>
  const className =
    STATUS_STYLE[status] ?? 'bg-slate-500/15 text-slate-300 ring-1 ring-inset ring-slate-500/40'
  return <span className={clsx('chip', className)}>{titleCase(status)}</span>
}

export function RiskBadge({
  score,
  band,
}: {
  score: number | null | undefined
  band: string | null | undefined
}) {
  if (score === null || score === undefined) return <span className="text-slate-500">—</span>
  const style = riskStyle(band)
  return (
    <span className="inline-flex items-center gap-2">
      <span className={clsx('font-mono text-sm font-semibold', style.text)}>
        {Math.round(score)}
      </span>
      <span className="text-xs uppercase tracking-wide text-slate-500">{band ?? 'low'}</span>
    </span>
  )
}

export function ProgressBar({
  value,
  className,
  colorClass = 'bg-accent',
}: {
  value: number
  className?: string
  colorClass?: string
}) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div
      className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-surface-border', className)}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={clsx('h-full rounded-full transition-all duration-500', colorClass)}
        style={{ width: `${clamped}%` }}
      />
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={clsx('animate-spin', className ?? 'h-4 w-4')}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  )
}

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-slate-500">
      <Spinner className="h-5 w-5" />
      {label}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <h3 className="text-sm font-semibold text-slate-300">{title}</h3>
      {description ? (
        <p className="max-w-md text-sm text-slate-500">{description}</p>
      ) : null}
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const message = error instanceof Error ? error.message : 'Something went wrong.'
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <span className="text-2xl" aria-hidden="true">
        ⚠️
      </span>
      <h3 className="text-sm font-semibold text-slate-200">Could not load this view</h3>
      <p className="max-w-md text-sm text-slate-500">{message}</p>
      {onRetry ? (
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

export function Stat({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'danger' | 'warning' | 'success'
}) {
  const toneClass = {
    default: 'text-slate-100',
    danger: 'text-red-300',
    warning: 'text-amber-300',
    success: 'text-emerald-300',
  }[tone]
  return (
    <div className="card px-5 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={clsx('mt-1.5 text-2xl font-semibold tabular-nums', toneClass)}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  )
}

export function Pagination({
  page,
  pages,
  total,
  onChange,
}: {
  page: number
  pages: number
  total: number
  onChange: (page: number) => void
}) {
  if (pages <= 1) {
    return (
      <p className="px-4 py-3 text-xs text-slate-500">
        {total} result{total === 1 ? '' : 's'}
      </p>
    )
  }
  return (
    <div className="flex items-center justify-between border-t border-surface-border px-4 py-3">
      <p className="text-xs text-slate-500">
        Page {page} of {pages} · {total} result{total === 1 ? '' : 's'}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-secondary px-2.5 py-1 text-xs"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn-secondary px-2.5 py-1 text-xs"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  )
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-slate-100">{title}</h1>
        {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
      </div>
      {action ? <div className="flex flex-wrap items-center gap-2">{action}</div> : null}
    </header>
  )
}

export function Select({
  label,
  value,
  onChange,
  options,
  className,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  className?: string
}) {
  return (
    <label className={clsx('block', className)}>
      <span className="label">{label}</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function SearchInput({
  label,
  value,
  onChange,
  placeholder,
  className,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <label className={clsx('block', className)}>
      <span className="label">{label}</span>
      <input
        type="search"
        className="input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}

/** A definition-list row used by the detail views. */
export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="min-w-0 truncate text-right text-sm text-slate-200">{children}</dd>
    </div>
  )
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  label: string
  description?: string
  disabled?: boolean
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 py-2.5">
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-200">{label}</span>
        {description ? (
          <span className="mt-0.5 block text-xs text-slate-500">{description}</span>
        ) : null}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors',
          checked ? 'bg-accent' : 'bg-surface-border',
          disabled && 'cursor-not-allowed opacity-50',
        )}
      >
        <span
          className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform"
          style={{ transform: checked ? 'translateX(1rem)' : 'translateX(0)' }}
        />
      </button>
    </label>
  )
}
