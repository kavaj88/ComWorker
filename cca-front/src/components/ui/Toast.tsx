import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'

type ToastType = 'success' | 'error' | 'info'

type ToastItem = {
  id: number
  type: ToastType
  text: string
}

type ToastInput = {
  text: string
  type?: ToastType
  duration?: number
}

type ToastContextValue = {
  show: (input: ToastInput) => void
  success: (text: string, duration?: number) => void
  error: (text: string, duration?: number) => void
  info: (text: string, duration?: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const toastStyles: Record<ToastType, { icon: typeof Info; className: string }> = {
  success: {
    icon: CheckCircle2,
    className: 'border-accent-green/25 bg-light-card text-light-text',
  },
  error: {
    icon: AlertCircle,
    className: 'border-accent-red/25 bg-light-card text-light-text',
  },
  info: {
    icon: Info,
    className: 'border-accent-blue/25 bg-light-card text-light-text',
  },
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timersRef = useRef<Record<number, number>>({})

  const close = useCallback((id: number) => {
    const timer = timersRef.current[id]
    if (timer) {
      window.clearTimeout(timer)
      delete timersRef.current[id]
    }
    setToasts(current => current.filter(toast => toast.id !== id))
  }, [])

  const show = useCallback((input: ToastInput) => {
    const text = input.text.trim()
    if (!text) return
    const id = Date.now() + Math.floor(Math.random() * 1000)
    const toast: ToastItem = {
      id,
      type: input.type || 'info',
      text,
    }
    setToasts(current => [...current.slice(-3), toast])
    timersRef.current[id] = window.setTimeout(() => close(id), input.duration ?? 3600)
  }, [close])

  const value = useMemo<ToastContextValue>(() => ({
    show,
    success: (text, duration) => show({ text, type: 'success', duration }),
    error: (text, duration) => show({ text, type: 'error', duration }),
    info: (text, duration) => show({ text, type: 'info', duration }),
  }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-4 z-[200] flex flex-col items-center gap-2 px-4">
        {toasts.map(toast => {
          const style = toastStyles[toast.type]
          const Icon = style.icon
          return (
            <div
              key={toast.id}
              role={toast.type === 'error' ? 'alert' : 'status'}
              aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
              className={`pointer-events-auto flex w-[min(520px,calc(100vw-2rem))] items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-xl shadow-slate-950/10 backdrop-blur animate-fade-in ${style.className}`}
            >
              <Icon
                size={17}
                className={`mt-0.5 shrink-0 ${
                  toast.type === 'success'
                    ? 'text-accent-green'
                    : toast.type === 'error'
                      ? 'text-accent-red'
                      : 'text-accent-blue'
                }`}
              />
              <span className="min-w-0 flex-1 leading-5">{toast.text}</span>
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() => close(toast.id)}
                className="inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-light-text-secondary transition-colors hover:bg-light-card-hover hover:text-light-text"
              >
                <X size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used within ToastProvider')
  return context
}
