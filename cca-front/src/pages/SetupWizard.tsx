import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  KeyRound,
  Loader2,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import {
  completeSetup,
  dismissSetup,
  fetchJSON,
  getMe,
  getSetupStatus,
  type SetupStatus,
} from '../lib/api.ts'
import ClearableInput from '../components/ui/ClearableInput.tsx'
import { useToast } from '../components/ui/Toast.tsx'

type StepId = 'password' | 'model' | 'finish'

interface StepMeta {
  id: StepId
  title: string
  blurb: string
  optional?: boolean
}

const STEPS: StepMeta[] = [
  {
    id: 'password',
    title: '修改初始管理员密码',
    blurb: '平台首次启动时会自动生成一个强随机密码并在日志中打印。为了避免遗忘，请立即替换为你能记住的密码。',
  },
  {
    id: 'model',
    title: '配置模型 API Key（可选）',
    blurb: '配置后即可使用 AI 对话能力。暂时没有 Key 也没关系——可以先完成初始化，之后随时在「模型设置」中补充。所有 Key 在数据库中以 Fernet 加密存储，不会以明文落盘。',
    optional: true,
  },
  {
    id: 'finish',
    title: '完成初始化',
    blurb: '修改初始密码后即可完成初始化。模型 Key 未配置时不影响管理后台使用，AI 对话功能将在配置后自动启用。',
  },
]

interface ProviderDraft {
  id: string
  name: string
  apiBase: string
  apiKey: string
  modelId: string
  modelName: string
}

const PROVIDER_PRESETS: ProviderDraft[] = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    apiBase: 'https://api.deepseek.com/v1',
    apiKey: '',
    modelId: 'deepseek-chat',
    modelName: 'DeepSeek Chat',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    apiBase: '',
    apiKey: '',
    modelId: 'gpt-4o-mini',
    modelName: 'GPT-4o mini',
  },
  {
    id: 'anthropic',
    name: 'Anthropic Claude',
    apiBase: '',
    apiKey: '',
    modelId: 'claude-sonnet-4-5',
    modelName: 'Claude Sonnet 4.5',
  },
]

export default function SetupWizard() {
  const navigate = useNavigate()
  const toast = useToast()
  const shellRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [activeStep, setActiveStep] = useState<StepId>('password')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  // Step 1 — change password
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  // Step 2 — model config
  const [draftProvider, setDraftProvider] = useState<ProviderDraft>(PROVIDER_PRESETS[0])

  // Hydrate on mount
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const s = await getSetupStatus()
        if (cancelled) return
        setStatus(s)
        setActiveStep(nextStepFor(s))
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '加载初始化状态失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [toast])

  // Pointer-light background effect (matches Login shell)
  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width) * 100
    const y = ((event.clientY - rect.top) / rect.height) * 100
    shellRef.current?.style.setProperty('--login-pointer-x', `${x}%`)
    shellRef.current?.style.setProperty('--login-pointer-y', `${y}%`)
  }
  const resetPointer = () => {
    shellRef.current?.style.setProperty('--login-pointer-x', '50%')
    shellRef.current?.style.setProperty('--login-pointer-y', '42%')
  }

  const refreshStatus = async () => {
    const fresh = await getSetupStatus()
    setStatus(fresh)
    setActiveStep(nextStepFor(fresh))
    return fresh
  }

  const handleChangePassword = async () => {
    if (!oldPassword || !newPassword) {
      toast.error('请填写旧密码和新密码')
      return
    }
    if (newPassword !== confirmPassword) {
      toast.error('两次输入的新密码不一致')
      return
    }
    if (newPassword.length < 6) {
      toast.error('新密码至少需要 6 个字符')
      return
    }
    setSubmitting(true)
    try {
      await fetchJSON('/api/auth/change-password', {
        method: 'PUT',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
      })
      toast.success('密码已更新')
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
      const fresh = await refreshStatus()
      if (fresh.missing_steps.length === 0) {
        setActiveStep('finish')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '修改密码失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleSaveProvider = async () => {
    if (!draftProvider.apiKey.trim()) {
      toast.error('请填写 API Key')
      return
    }
    setSubmitting(true)
    try {
      await fetchJSON('/api/admin/models', {
        method: 'PUT',
        body: JSON.stringify({
          defaultModel: `${draftProvider.id}/${draftProvider.modelId}`,
          providers: {
            [draftProvider.id]: {
              name: draftProvider.name,
              providerType: draftProvider.id,
              api: 'chat_completions',
              baseUrl: draftProvider.apiBase || null,
              apiKey: draftProvider.apiKey.trim(),
              enabled: true,
              models: [{ id: draftProvider.modelId, name: draftProvider.modelName, enabled: true }],
            },
          },
        }),
      })
      toast.success('模型 Key 已保存并加密入库')
      setDraftProvider(prev => ({ ...prev, apiKey: '' }))
      const fresh = await refreshStatus()
      if (fresh.missing_steps.length === 0) {
        setActiveStep('finish')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存模型 Key 失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleFinish = async () => {
    setSubmitting(true)
    try {
      const me = await getMe()
      if (me.must_change_password) {
        toast.error('请先修改初始密码')
        setActiveStep('password')
        return
      }
      const fresh = await completeSetup()
      setStatus(fresh)
      toast.success('初始化已完成')
      navigate('/dashboard', { replace: true })
    } catch (err) {
      const detail = err instanceof Error ? err.message : '完成初始化失败'
      toast.error(detail)
      // If the server says a REQUIRED step is still missing, jump there.
      if (detail.includes('change_admin_password')) setActiveStep('password')
      await refreshStatus()
    } finally {
      setSubmitting(false)
    }
  }

  const handleDismiss = async () => {
    if (!confirm('确定要跳过初始化向导吗？跳过之后系统将不会再次提醒。初始密码仍建议立即修改，模型 Key 可稍后在「模型设置」中配置。')) {
      return
    }
    setSubmitting(true)
    try {
      await dismissSetup()
      toast.success('已跳过初始化向导')
      navigate('/dashboard', { replace: true })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading || !status) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-light-bg text-light-text">
        <Loader2 className="h-6 w-6 animate-spin text-accent-blue" />
      </div>
    )
  }

  const stepIndex = STEPS.findIndex(s => s.id === activeStep)
  const progressPct = ((stepIndex + 1) / STEPS.length) * 100

  return (
    <div
      ref={shellRef}
      onPointerMove={handlePointerMove}
      onPointerLeave={resetPointer}
      className="login-shell relative min-h-screen overflow-hidden bg-[#f7fbff] text-light-text"
    >
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,#ffffff_0%,#eef9fb_42%,#f8fbff_100%)]" />
      <div className="login-bg-grid pointer-events-none absolute inset-0 opacity-[0.34]" />
      <div className="login-bg-flow pointer-events-none absolute -inset-x-32 -inset-y-24 opacity-80 blur-3xl" />
      <div className="login-pointer-light pointer-events-none absolute inset-0" />
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent-blue/40 to-transparent" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-4xl flex-col px-5 py-8">
        <header className="mb-8 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-blue/10 text-accent-blue">
            <Sparkles size={18} />
          </div>
          <div>
            <h1 className="text-xl font-semibold">平台初始化</h1>
            <p className="text-xs text-light-text-secondary">
              企业级部署首次启动时，需要完成以下 {STEPS.length} 步配置
            </p>
          </div>
          <button
            type="button"
            onClick={() => void handleDismiss()}
            disabled={submitting}
            className="ml-auto text-xs text-light-text-secondary underline-offset-4 hover:text-light-text hover:underline disabled:opacity-50"
          >
            跳过向导（仅脚本化部署）
          </button>
        </header>

        <section className="mb-8 rounded-2xl border border-light-border bg-light-card/80 px-4 py-3 shadow-sm shadow-slate-200/40 backdrop-blur-sm">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-medium text-light-text">进度 {stepIndex + 1} / {STEPS.length}</span>
            <span className="text-light-text-secondary">
              {status.missing_steps.length === 0
                ? '所有必要步骤均已完成'
                : `还需 ${status.missing_steps.length} 步`}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-light-border">
            <div
              className="h-full rounded-full bg-accent-blue transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <ol className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
            {STEPS.map(step => {
              const stepKey =
                step.id === 'password'
                  ? 'change_admin_password'
                  : step.id === 'model'
                    ? 'add_model_key'
                    : 'finalize'
              const isCompleted =
                step.id === 'finish'
                  ? status.is_complete
                  : !status.missing_steps.includes(stepKey) &&
                    !status.missing_recommended_steps.includes(stepKey)
              const isActive = step.id === activeStep
              return (
                <li
                  key={step.id}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors ${
                    isActive
                      ? 'border-accent-blue/40 bg-accent-blue/5 text-light-text'
                      : isCompleted
                        ? 'border-emerald-200 bg-emerald-50/60 text-emerald-700'
                        : 'border-light-border bg-light-card text-light-text-secondary'
                  }`}
                >
                  {isCompleted ? (
                    <CheckCircle2 size={14} />
                  ) : step.id === 'model' ? (
                    <Sparkles size={14} />
                  ) : (
                    <ShieldCheck size={14} />
                  )}
                  <span className="truncate">{step.title}</span>
                  {step.optional && (
                    <span className="ml-auto shrink-0 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
                      可选
                    </span>
                  )}
                </li>
              )
            })}
          </ol>
        </section>

        <section className="flex-1 rounded-2xl border border-light-border bg-light-card/85 p-6 shadow-sm shadow-slate-200/40 backdrop-blur-sm">
          {STEPS.filter(s => s.id === activeStep).map(step => (
            <div key={step.id}>
              <header className="mb-5 flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-blue/10 text-accent-blue">
                  {step.id === 'password' ? <KeyRound size={20} /> : step.id === 'model' ? <Bot size={20} /> : <LockKeyhole size={20} />}
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-light-text">{step.title}</h2>
                  <p className="mt-1 text-sm text-light-text-secondary">{step.blurb}</p>
                </div>
              </header>

              {step.id === 'password' && (
                <div className="space-y-3">
                  <ClearableInput
                    type="password"
                    value={oldPassword}
                    onValueChange={setOldPassword}
                    placeholder="当前密码（首次启动时打印在网关容器日志中）"
                    clearLabel="清空"
                  />
                  <ClearableInput
                    type="password"
                    value={newPassword}
                    onValueChange={setNewPassword}
                    placeholder="新密码（至少 6 个字符）"
                    clearLabel="清空"
                  />
                  <ClearableInput
                    type="password"
                    value={confirmPassword}
                    onValueChange={setConfirmPassword}
                    placeholder="再次输入新密码"
                    clearLabel="清空"
                  />
                  <button
                    type="button"
                    onClick={() => void handleChangePassword()}
                    disabled={submitting}
                    className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-accent-blue px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
                    修改密码并继续
                  </button>
                </div>
              )}

              {step.id === 'model' && (
                <div className="space-y-4">
                  <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-xs leading-relaxed text-amber-700">
                    此步骤可选。未配置模型 Key 时，AI 对话功能暂不可用，但管理后台的其它功能不受影响——可以先完成初始化，之后随时在「模型设置」中补充。
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-light-text-secondary">提供方</label>
                    <select
                      value={draftProvider.id}
                      onChange={e => {
                        const next = PROVIDER_PRESETS.find(p => p.id === e.target.value)
                        if (next) setDraftProvider(next)
                      }}
                      className="w-full rounded-xl border border-light-border bg-light-card px-3 py-2 text-sm outline-none focus:border-accent-blue"
                    >
                      {PROVIDER_PRESETS.map(p => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-light-text-secondary">API Base URL</label>
                    <ClearableInput
                      value={draftProvider.apiBase}
                      onValueChange={v => setDraftProvider(prev => ({ ...prev, apiBase: v }))}
                      placeholder="https://api.example.com/v1"
                      clearLabel="清空"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-light-text-secondary">API Key</label>
                    <ClearableInput
                      type="password"
                      value={draftProvider.apiKey}
                      onValueChange={v => setDraftProvider(prev => ({ ...prev, apiKey: v }))}
                      placeholder="sk-..."
                      clearLabel="清空"
                    />
                    <p className="mt-1 text-[11px] text-light-text-secondary">
                      Key 在数据库中以 Fernet（AES-128-CBC）加密存储，密钥派生自 docker secret / PLATFORM_MODEL_KEYS_MASTER_KEY。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSaveProvider()}
                    disabled={submitting || !draftProvider.apiKey.trim()}
                    className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-accent-blue px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
                    保存 Key 并继续
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveStep('finish')}
                    disabled={submitting}
                    className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-light-border px-5 py-2 text-sm text-light-text-secondary transition-colors hover:border-accent-blue/40 hover:text-light-text disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    跳过此步骤，稍后配置
                  </button>
                </div>
              )}

              {step.id === 'finish' && (
                <div className="space-y-4">
                  <ul className="space-y-2 rounded-xl border border-emerald-200 bg-emerald-50/70 p-4 text-sm">
                    <li className="flex items-center gap-2 text-emerald-700">
                      <CheckCircle2 size={16} />
                      修改初始管理员密码
                    </li>
                    {status.missing_recommended_steps.includes('add_model_key') ? (
                      <li className="flex items-start gap-2 text-amber-700">
                        <Sparkles size={16} className="mt-0.5 shrink-0" />
                        <span>
                          配置模型 API Key —— <span className="font-medium">尚未配置（可选）</span>
                          。进入工作台后 AI 对话暂不可用，可在「模型设置」中随时补充。
                        </span>
                      </li>
                    ) : (
                      <li className="flex items-center gap-2 text-emerald-700">
                        <CheckCircle2 size={16} />
                        配置模型 API Key
                      </li>
                    )}
                  </ul>
                  <button
                    type="button"
                    onClick={() => void handleFinish()}
                    disabled={submitting}
                    className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
                    完成初始化并进入工作台
                  </button>
                </div>
              )}
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}

function nextStepFor(status: SetupStatus): StepId {
  // Only REQUIRED steps gate the wizard. Recommended steps (model key)
  // are surfaced as hints but never block completion.
  if (status.missing_steps.includes('change_admin_password')) return 'password'
  return 'finish'
}