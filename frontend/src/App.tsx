import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import Layout from './components/Layout'
import { LoadingBlock } from './components/ui'
import { useAuth } from './hooks/useAuth'
import DashboardPage from './pages/DashboardPage'
import FindingsPage from './pages/FindingsPage'
import LoginPage from './pages/LoginPage'
import PullRequestsPage from './pages/PullRequestsPage'
import RepositoriesPage from './pages/RepositoriesPage'
import RepositoryDetailPage from './pages/RepositoryDetailPage'
import ReviewDetailPage from './pages/ReviewDetailPage'
import ReviewsPage from './pages/ReviewsPage'
import SettingsPage from './pages/SettingsPage'

// Analytics is the only view that pulls in the charting library; loading it on
// demand keeps that weight out of the initial bundle.
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'))

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <LoadingBlock label="Restoring your session…" />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/repositories" element={<RepositoriesPage />} />
        <Route path="/repositories/:repositoryId" element={<RepositoryDetailPage />} />
        <Route path="/pull-requests" element={<PullRequestsPage />} />
        <Route path="/reviews" element={<ReviewsPage />} />
        <Route path="/reviews/:reviewId" element={<ReviewDetailPage />} />
        <Route path="/findings" element={<FindingsPage />} />
        <Route
          path="/analytics"
          element={
            <Suspense fallback={<LoadingBlock label="Loading charts…" />}>
              <AnalyticsPage />
            </Suspense>
          }
        />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
