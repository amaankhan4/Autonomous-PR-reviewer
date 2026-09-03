import clsx from 'clsx'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../hooks/useAuth'
import { useHealth, useQueueStats } from '../hooks/queries'
import { Spinner } from './ui'

const NAV = [
  { to: '/', label: 'Dashboard', end: true, icon: '◈' },
  { to: '/repositories', label: 'Repositories', icon: '▤' },
  { to: '/pull-requests', label: 'Pull requests', icon: '⑃' },
  { to: '/reviews', label: 'Reviews', icon: '✓' },
  { to: '/findings', label: 'Findings', icon: '!' },
  { to: '/analytics', label: 'Analytics', icon: '◔' },
]

function QueueIndicator() {
  const { data } = useQueueStats()
  if (!data) return null
  const busy = data.queued + data.processing
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      {busy > 0 ? <Spinner className="h-3 w-3 text-accent" /> : null}
      <span>
        {busy > 0 ? `${busy} review${busy === 1 ? '' : 's'} in flight` : 'Queue idle'}
      </span>
    </div>
  )
}

export default function Layout() {
  const { user, demoMode, logout } = useAuth()
  const { data: health } = useHealth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen bg-surface">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-surface-border bg-surface-raised lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent">
            ⌁
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-100">PR Reviewer</p>
            <p className="text-[0.6875rem] text-slate-500">
              v{health?.version ?? '0.1.0'} · {health?.environment ?? '—'}
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-accent/12 font-medium text-accent'
                    : 'text-slate-400 hover:bg-surface-overlay hover:text-slate-200',
                )
              }
            >
              <span className="w-4 text-center text-xs opacity-70" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-surface-border px-3 py-3">
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-accent/12 font-medium text-accent'
                  : 'text-slate-400 hover:bg-surface-overlay hover:text-slate-200',
              )
            }
          >
            <span className="w-4 text-center text-xs opacity-70" aria-hidden="true">
              ⚙
            </span>
            Settings
          </NavLink>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-surface-border bg-surface-raised/80 px-6 py-3 backdrop-blur">
          <div className="flex items-center gap-3">
            <nav className="flex gap-1 lg:hidden">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    clsx(
                      'rounded-md px-2 py-1 text-xs',
                      isActive ? 'bg-accent/15 text-accent' : 'text-slate-400',
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
            {demoMode ? (
              <span
                className="chip bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/40"
                title="GitHub and the LLM are mocked; no external calls are made."
              >
                Demo mode
              </span>
            ) : null}
          </div>

          <div className="flex items-center gap-4">
            <QueueIndicator />
            <div className="flex items-center gap-2.5">
              <div className="text-right">
                <p className="text-xs font-medium text-slate-200">
                  {user?.full_name || user?.username}
                </p>
                <p className="text-[0.6875rem] text-slate-500">{user?.email}</p>
              </div>
              <button
                type="button"
                onClick={() => void handleLogout()}
                className="btn-ghost px-2 py-1 text-xs"
              >
                Sign out
              </button>
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto w-full max-w-7xl animate-fade-in">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
