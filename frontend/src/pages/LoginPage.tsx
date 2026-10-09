import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { useConfig } from '../hooks/queries'
import { Spinner } from '../components/ui'

export default function LoginPage() {
  const { user, loading, login, register } = useAuth()
  const {
    data: config,
    isLoading: configLoading,
    refetch: refetchConfig,
  } = useConfig()
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center text-slate-500">
        <Spinner className="h-6 w-6" />
      </div>
    )
  }
  if (user) {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setFieldErrors({})
    setBusy(true)
    try {
      if (mode === 'login') {
        await login(email, password)
      } else {
        await register({
          email,
          username,
          password,
          full_name: fullName || undefined,
        })
      }
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
        setFieldErrors(err.fieldErrors)
      } else {
        setError('Could not reach the API. Is the backend running?')
      }
    } finally {
      setBusy(false)
    }
  }

  const tryDemo = async () => {
    setError(null)
    setBusy(true)
    try {
      // A cold serverless function can make the initial config request fail or
      // race its seed. Re-fetch at click time so the action is never inert.
      const latestConfig = config ?? (await refetchConfig()).data
      const credentials = latestConfig?.demo_credentials
      if (!credentials) {
        setError('Demo mode is disabled on this backend, so there is no seeded account.')
        return
      }
      await login(credentials.email, credentials.password)
      navigate('/', { replace: true })
    } catch {
      setError('Demo sign-in failed. Check that the API deployment is healthy and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between border-r border-surface-border bg-surface-raised p-12 lg:flex">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/15 text-lg text-accent">
            ⌁
          </span>
          <span className="text-lg font-semibold text-slate-100">Autonomous PR Reviewer</span>
        </div>

        <div className="max-w-md space-y-6">
          <h1 className="text-3xl font-semibold leading-tight text-slate-100">
            Reviews that cite their evidence.
          </h1>
          <p className="text-sm leading-relaxed text-slate-400">
            Deterministic analyzers find the facts, repository-aware retrieval supplies the
            context, and the model explains the consequence. Every finding is validated
            against the real diff before it is ever published.
          </p>
          <ul className="space-y-3 text-sm text-slate-400">
            {[
              'Findings anchored to lines that actually changed',
              'Risk scored by named factors, not by the model',
              'Hallucinated files and lines rejected before publishing',
              'Runs fully offline in demo mode',
            ].map((item) => (
              <li key={item} className="flex gap-2.5">
                <span className="text-accent">✓</span>
                {item}
              </li>
            ))}
          </ul>
        </div>

        <p className="text-xs text-slate-600">
          Automated review does not replace human judgement.
        </p>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <h2 className="text-xl font-semibold text-slate-100">
            {mode === 'login' ? 'Sign in' : 'Create your account'}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {mode === 'login'
              ? 'Use your email and password to continue.'
              : 'Passwords need 10+ characters with upper, lower and a digit.'}
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              {fieldErrors.email ? (
                <p className="mt-1 text-xs text-red-400">{fieldErrors.email}</p>
              ) : null}
            </div>

            {mode === 'register' ? (
              <>
                <div>
                  <label className="label" htmlFor="username">
                    Username
                  </label>
                  <input
                    id="username"
                    required
                    className="input"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                  {fieldErrors.username ? (
                    <p className="mt-1 text-xs text-red-400">{fieldErrors.username}</p>
                  ) : null}
                </div>
                <div>
                  <label className="label" htmlFor="fullName">
                    Full name <span className="normal-case text-slate-600">(optional)</span>
                  </label>
                  <input
                    id="fullName"
                    className="input"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </div>
              </>
            ) : null}

            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                required
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {fieldErrors.password ? (
                <p className="mt-1 text-xs text-red-400">{fieldErrors.password}</p>
              ) : null}
            </div>

            {error ? (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {error}
              </div>
            ) : null}

            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? <Spinner /> : null}
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </button>
          </form>

          <div className="mt-4 flex items-center justify-between text-xs">
            <button
              type="button"
              className="link"
              onClick={() => {
                setMode(mode === 'login' ? 'register' : 'login')
                setError(null)
                setFieldErrors({})
              }}
            >
              {mode === 'login' ? 'Need an account?' : 'Already registered?'}
            </button>
            <button
              type="button"
              className="text-slate-500 hover:text-slate-300"
              onClick={() => void tryDemo()}
              disabled={busy || configLoading}
              title={
                config?.demo_credentials
                  ? `Signs in as ${config.demo_credentials.email}`
                  : 'Fetches the configured demo account and signs in'
              }
            >
              Use the demo account
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
