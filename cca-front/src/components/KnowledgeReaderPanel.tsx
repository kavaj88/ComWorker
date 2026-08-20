import { useEffect, useState, useRef, useCallback } from 'react'
import { X, Loader2, FileText, AlertCircle } from 'lucide-react'
import MarkdownContent from './MarkdownContent.tsx'
import { readKnowledge } from '../lib/api.ts'

interface KnowledgeReaderPanelProps {
  agentId: string
  path: string
  onClose: () => void
}

function fileNameFromPath(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1] || path
}

const DEFAULT_WIDTH = 460
const MIN_WIDTH = 320
const MAX_WIDTH_RATIO = 0.8

export default function KnowledgeReaderPanel({ agentId, path, onClose }: KnowledgeReaderPanelProps) {
  const [content, setContent] = useState<string>('')
  const [title, setTitle] = useState<string>(fileNameFromPath(path))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const [isDragging, setIsDragging] = useState(false)
  const dragStartXRef = useRef(0)
  const dragStartWidthRef = useRef(DEFAULT_WIDTH)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setContent('')
    setTitle(fileNameFromPath(path))
    readKnowledge(agentId, path)
      .then(result => {
        if (cancelled) return
        setContent(result.content || '')
        if (result.page?.title) setTitle(result.page.title)
        setLoading(false)
      })
      .catch(err => {
        if (cancelled) return
        setError(err?.message || '加载失败')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [agentId, path])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)
    dragStartXRef.current = e.clientX
    dragStartWidthRef.current = width
  }, [width])

  useEffect(() => {
    if (!isDragging) return
    const handleMouseMove = (e: MouseEvent) => {
      const delta = dragStartXRef.current - e.clientX
      const maxWidth = window.innerWidth * MAX_WIDTH_RATIO
      const newWidth = Math.min(Math.max(dragStartWidthRef.current + delta, MIN_WIDTH), maxWidth)
      setWidth(newWidth)
    }
    const handleMouseUp = () => setIsDragging(false)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isDragging])

  return (
    <div className="fixed inset-0 z-[70] flex justify-end bg-slate-950/35 backdrop-blur-[1px]">
      <button
        type="button"
        aria-label="关闭阅读面板"
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <aside
        className="relative flex h-full flex-col border-l border-light-border bg-light-bg shadow-2xl shadow-slate-950/20"
        style={{ width: `${width}px` }}
      >
        {/* Resize handle */}
        <div
          className={`absolute left-0 top-0 bottom-0 z-20 w-1 ${isDragging ? 'bg-accent-blue' : 'bg-transparent hover:bg-accent-blue/30'} cursor-col-resize transition-colors`}
          onMouseDown={handleMouseDown}
        />

        <header className="flex items-center justify-between border-b border-light-border px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-blue/10 text-accent-blue">
              <FileText size={20} />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-light-text">{title}</h2>
              <p className="mt-0.5 truncate text-xs text-light-text-secondary">{path}</p>
            </div>
          </div>
          <button
            type="button"
            aria-label="关闭阅读面板"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-light-text-secondary hover:bg-light-card-hover hover:text-light-text transition-colors"
          >
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-light-text-secondary">
              <Loader2 size={14} className="animate-spin" />
              正在加载...
            </div>
          ) : error ? (
            <div className="flex items-start gap-2 text-sm text-accent-red">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <div className="break-words">{error}</div>
            </div>
          ) : (
            <MarkdownContent content={content} />
          )}
        </div>
      </aside>
    </div>
  )
}
