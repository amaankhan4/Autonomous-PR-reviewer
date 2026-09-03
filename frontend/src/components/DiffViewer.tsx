import clsx from 'clsx'
import { useMemo, useState } from 'react'

import { parsePatch } from '../lib/diff'
import { severityStyle } from '../lib/severity'
import type { Finding, ReviewDiffFile } from '../lib/types'

function FindingAnnotation({ finding }: { finding: Finding }) {
  const style = severityStyle(finding.severity)
  return (
    <tr>
      <td colSpan={3} className="border-y border-surface-border bg-surface-overlay/70 px-4 py-3">
        <div className="flex items-start gap-2.5">
          <span className={clsx('mt-1 h-2 w-2 shrink-0 rounded-full', style.dot)} />
          <div className="min-w-0 space-y-1">
            <p className="text-xs font-semibold text-slate-200">
              {finding.severity} · {finding.title}
            </p>
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-400">
              {finding.description}
            </p>
            {finding.suggested_fix ? (
              <pre className="mt-1 overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-[0.7rem] text-emerald-300">
                {finding.suggested_fix}
              </pre>
            ) : null}
          </div>
        </div>
      </td>
    </tr>
  )
}

function FileDiff({ file }: { file: ReviewDiffFile }) {
  const [open, setOpen] = useState(file.findings.length > 0)
  const lines = useMemo(() => (file.patch ? parsePatch(file.patch) : []), [file.patch])

  const findingsByLine = useMemo(() => {
    const map = new Map<number, Finding[]>()
    for (const finding of file.findings) {
      const line = finding.line_number ?? finding.end_line ?? finding.start_line
      if (line === null || line === undefined) continue
      const bucket = map.get(line)
      if (bucket) bucket.push(finding)
      else map.set(line, [finding])
    }
    return map
  }, [file.findings])

  const unanchored = file.findings.filter(
    (finding) => (finding.line_number ?? finding.end_line ?? finding.start_line) === null,
  )

  return (
    <div className="border-b border-surface-border last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-surface-overlay/60"
      >
        <span className="w-3 text-xs text-slate-500">{open ? '▾' : '▸'}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-200">
          {file.filename}
        </span>
        {file.findings.length > 0 ? (
          <span className="chip bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/40">
            {file.findings.length} finding{file.findings.length === 1 ? '' : 's'}
          </span>
        ) : null}
        <span className="shrink-0 text-xs tabular-nums">
          <span className="text-emerald-400">+{file.additions}</span>{' '}
          <span className="text-red-400">−{file.deletions}</span>
        </span>
        <span className="w-16 shrink-0 text-right text-xs uppercase tracking-wide text-slate-500">
          {file.status}
        </span>
      </button>

      {open ? (
        lines.length > 0 ? (
          <div className="overflow-x-auto border-t border-surface-border">
            <table className="w-full border-collapse font-mono text-[0.75rem] leading-5">
              <tbody>
                {lines.flatMap((line, index) => {
                  if (line.kind === 'hunk') {
                    return [
                      <tr key={`l${index}`}>
                        <td colSpan={3} className="bg-surface-overlay px-4 py-1 text-slate-500">
                          {line.content}
                        </td>
                      </tr>,
                    ]
                  }
                  const anchored = line.newLine ? findingsByLine.get(line.newLine) : undefined
                  const gutter = clsx(
                    'w-12 select-none border-r border-surface-border/60 px-2 text-right text-slate-600',
                    line.kind === 'add' && 'bg-emerald-500/5',
                    line.kind === 'del' && 'bg-red-500/5',
                  )
                  return [
                    <tr key={`l${index}`}>
                      <td className={gutter}>{line.oldLine ?? ''}</td>
                      <td className={clsx(gutter, anchored && 'bg-amber-500/10 text-amber-300')}>
                        {line.newLine ?? ''}
                      </td>
                      <td
                        className={clsx(
                          'whitespace-pre-wrap break-all px-3',
                          line.kind === 'add' && 'bg-emerald-500/10 text-emerald-200',
                          line.kind === 'del' && 'bg-red-500/10 text-red-200',
                          line.kind === 'context' && 'text-slate-400',
                          anchored && 'bg-amber-500/10',
                        )}
                      >
                        {line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}
                        {line.content}
                      </td>
                    </tr>,
                    ...(anchored ?? []).map((finding) => (
                      <FindingAnnotation key={finding.id} finding={finding} />
                    )),
                  ]
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="border-t border-surface-border px-5 py-4 text-xs text-slate-500">
            GitHub did not return a patch for this file (it is binary, or too large).
          </p>
        )
      ) : null}

      {open && unanchored.length > 0 ? (
        <div className="border-t border-surface-border px-5 py-3">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            File-level findings
          </p>
          <ul className="space-y-2">
            {unanchored.map((finding) => (
              <li key={finding.id} className="text-xs text-slate-400">
                <span className="font-semibold text-slate-200">{finding.severity}</span> ·{' '}
                {finding.title}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export default function DiffViewer({
  files,
  truncated,
}: {
  files: ReviewDiffFile[]
  truncated: boolean
}) {
  if (files.length === 0) {
    return <p className="px-5 py-6 text-sm text-slate-500">No file changes were returned.</p>
  }
  return (
    <div>
      {truncated ? (
        <p className="border-b border-surface-border bg-amber-500/10 px-5 py-2 text-xs text-amber-300">
          This diff was truncated — only the first files are shown.
        </p>
      ) : null}
      {files.map((file) => (
        <FileDiff key={file.filename} file={file} />
      ))}
    </div>
  )
}
