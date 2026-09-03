import clsx from 'clsx'
import { useState } from 'react'

import { SeverityBadge, Spinner, StatusBadge } from './ui'
import { useUpdateFindingStatus } from '../hooks/queries'
import { formatPercent, titleCase } from '../lib/format'
import type { Finding, FindingWithContext } from '../lib/types'

const NEXT_STATUSES: { value: string; label: string }[] = [
  { value: 'open', label: 'Reopen' },
  { value: 'acknowledged', label: 'Acknowledge' },
  { value: 'resolved', label: 'Resolve' },
  { value: 'dismissed', label: 'Dismiss' },
]

function StatusActions({ finding }: { finding: Finding }) {
  const mutation = useUpdateFindingStatus()
  const [note, setNote] = useState('')
  const [noteFor, setNoteFor] = useState<string | null>(null)

  const apply = (status: string) => {
    mutation.mutate(
      { id: finding.id, status, note: note || undefined },
      { onSuccess: () => setNoteFor(null) },
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {NEXT_STATUSES.filter((option) => option.value !== finding.status).map((option) => (
          <button
            key={option.value}
            type="button"
            className="btn-secondary px-2.5 py-1 text-xs"
            disabled={mutation.isPending}
            onClick={() => (noteFor === option.value ? apply(option.value) : setNoteFor(option.value))}
          >
            {mutation.isPending && mutation.variables?.status === option.value ? (
              <Spinner className="h-3 w-3" />
            ) : null}
            {noteFor === option.value ? 'Confirm' : option.label}
          </button>
        ))}
      </div>
      {noteFor ? (
        <div className="flex gap-2">
          <input
            className="input py-1 text-xs"
            placeholder="Optional note (why?)"
            value={note}
            autoFocus
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={() => {
              setNoteFor(null)
              setNote('')
            }}
          >
            Cancel
          </button>
        </div>
      ) : null}
      {mutation.isError ? (
        <p className="text-xs text-red-400">
          {mutation.error instanceof Error ? mutation.error.message : 'Could not update.'}
        </p>
      ) : null}
    </div>
  )
}

export default function FindingCard({
  finding,
  context,
  defaultOpen = false,
}: {
  finding: Finding | FindingWithContext
  context?: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const dimmed = finding.status !== 'open'

  return (
    <article
      className={clsx(
        'border-b border-surface-border/60 last:border-b-0',
        dimmed && 'opacity-70',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start gap-3 px-5 py-3 text-left hover:bg-surface-overlay/60"
      >
        <span className="mt-0.5 w-3 shrink-0 text-xs text-slate-500">{open ? '▾' : '▸'}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-slate-100">{finding.title}</span>
          <span className="mt-0.5 block truncate font-mono text-xs text-slate-500">
            {finding.file_path}
            {finding.line_number ? `:${finding.line_number}` : ''}
          </span>
          {context ? <span className="mt-0.5 block text-xs text-slate-500">{context}</span> : null}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <StatusBadge status={finding.status} />
        </span>
      </button>

      {open ? (
        <div className="space-y-4 border-t border-surface-border/60 px-5 py-4">
          <p className="prose-review whitespace-pre-wrap">{finding.description}</p>

          {finding.evidence ? (
            <div>
              <p className="label">Evidence</p>
              <pre className="overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-xs text-slate-300">
                {finding.evidence}
              </pre>
            </div>
          ) : null}

          {finding.code_snippet ? (
            <div>
              <p className="label">Code</p>
              <pre className="overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-xs text-slate-300">
                {finding.code_snippet}
              </pre>
            </div>
          ) : null}

          {finding.suggested_fix ? (
            <div>
              <p className="label">Suggested fix</p>
              <pre className="overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-xs text-emerald-300">
                {finding.suggested_fix}
              </pre>
            </div>
          ) : null}

          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500">
            <span>
              Category <span className="text-slate-300">{titleCase(finding.category)}</span>
            </span>
            <span>
              Source <span className="text-slate-300">{titleCase(finding.source)}</span>
            </span>
            <span>
              Confidence{' '}
              <span className="text-slate-300">
                {formatPercent(finding.confidence)} ({finding.confidence_label})
              </span>
            </span>
            {finding.rule_id ? (
              <span>
                Rule <span className="font-mono text-slate-300">{finding.rule_id}</span>
              </span>
            ) : null}
            <span>
              Published{' '}
              <span className="text-slate-300">{finding.published ? 'yes' : 'no'}</span>
            </span>
          </dl>

          {finding.resolution_note ? (
            <p className="rounded-md border border-surface-border bg-surface px-3 py-2 text-xs text-slate-400">
              <span className="text-slate-500">Note:</span> {finding.resolution_note}
            </p>
          ) : null}

          <StatusActions finding={finding} />
        </div>
      ) : null}
    </article>
  )
}
