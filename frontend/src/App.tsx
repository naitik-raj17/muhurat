import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { ApiError } from './api/client'
import { AuthProvider, useAuth } from './auth'
import { Layout } from './components/Layout'
import { Loading } from './components/ui'
import { ActivityPage } from './pages/ActivityPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { BudgetPage } from './pages/BudgetPage'
import { GuestsPage } from './pages/GuestsPage'
import { LoginPage } from './pages/LoginPage'
import { OverviewPage } from './pages/OverviewPage'
import { TimelinePage } from './pages/TimelinePage'
import { VendorsPage } from './pages/VendorsPage'
import { WeddingsPage } from './pages/WeddingsPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      // Don't retry what will fail again (auth, permissions, validation).
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
    },
  },
})

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Loading />
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route path="/weddings" element={<WeddingsPage />} />
              <Route path="/weddings/:weddingId" element={<OverviewPage />} />
              <Route path="/weddings/:weddingId/vendors" element={<VendorsPage />} />
              <Route path="/weddings/:weddingId/guests" element={<GuestsPage />} />
              <Route path="/weddings/:weddingId/timeline" element={<TimelinePage />} />
              <Route path="/weddings/:weddingId/budget" element={<BudgetPage />} />
              <Route path="/weddings/:weddingId/approvals" element={<ApprovalsPage />} />
              <Route path="/weddings/:weddingId/activity" element={<ActivityPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/activity" element={<ActivityPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/weddings" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
