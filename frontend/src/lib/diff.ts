/**
 * Unified-diff parsing.
 *
 * Kept out of the component file so it can be reasoned about (and tested) on
 * its own, and so the diff view stays a pure rendering concern.
 */

export interface DiffLine {
  kind: 'hunk' | 'add' | 'del' | 'context'
  content: string
  oldLine: number | null
  newLine: number | null
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/**
 * Parse a unified diff hunk-by-hunk, tracking both old and new line numbers so
 * findings (which are anchored to lines in the *new* file) can be placed inline.
 */
export function parsePatch(patch: string): DiffLine[] {
  const lines: DiffLine[] = []
  let oldLine = 0
  let newLine = 0

  for (const raw of patch.split('\n')) {
    const hunk = HUNK.exec(raw)
    if (hunk) {
      oldLine = Number(hunk[1])
      newLine = Number(hunk[2])
      lines.push({ kind: 'hunk', content: raw, oldLine: null, newLine: null })
      continue
    }
    if (raw.startsWith('+')) {
      lines.push({ kind: 'add', content: raw.slice(1), oldLine: null, newLine })
      newLine += 1
    } else if (raw.startsWith('-')) {
      lines.push({ kind: 'del', content: raw.slice(1), oldLine, newLine: null })
      oldLine += 1
    } else if (raw.startsWith('\\')) {
      // "\ No newline at end of file" — informational, advances nothing.
      lines.push({ kind: 'context', content: raw, oldLine: null, newLine: null })
    } else {
      lines.push({ kind: 'context', content: raw.slice(1), oldLine, newLine })
      oldLine += 1
      newLine += 1
    }
  }
  return lines
}
