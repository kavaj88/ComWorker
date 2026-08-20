import { useEffect, useReducer, useRef } from 'react'
import { Navigate } from 'react-router-dom'
import { getMe, getSetupStatus, type AuthUser, type SetupStatus } from '../lib/api.ts'

/**
 * Redirect admin users to /setup when the platform's empty-product
 * onboarding is unfinished. Non-admin users pass through.
 *
 * Polls once a minute so the user does not get stuck on a stale
 * "setup_required" state when, for example, they finish setup in a
 * different browser tab.
 */
export default function RequireSetupGuard({ children }: { children: React.ReactNode }) {
  const [, forceTick] = useReducer((x: number) => x + 1, 0)
  const userRef = useRef<AuthUser | null>(null)
  const statusRef = useRef<SetupStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const me = await getMe()
        if (cancelled) return
        userRef.current = me
        if (me.role !== 'admin') {
          forceTick()
          return
        }
        const s = await getSetupStatus()
        if (cancelled) return
        statusRef.current = s
        forceTick()
      } catch {
        // best-effort; if /api/setup/status is unreachable we don't gate
        // the app. Production deployments should monitor the underlying
        // gateway health instead.
      }
    }
    void run()
    const handle = window.setInterval(run, 60_000)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [])

  const user = userRef.current
  const status = statusRef.current
  if (user && user.role === 'admin' && status && !status.is_complete && !status.is_dismissed) {
    return <Navigate to="/setup" replace />
  }
  return <>{children}</>
}