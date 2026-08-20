import { memo, useEffect, useState } from 'react';
import { Brain, ChevronRight, Wrench, CircleCheck, AlertCircle, Loader2, MessageSquare, FileText } from 'lucide-react';

export interface ProcessStep {
  type: 'thinking' | 'tool' | 'text';
  content?: string;
  toolName?: string;
  toolArgs?: string;
  status?: 'running' | 'completed' | 'failed';
  duration?: number;
  error?: string;
}

interface ProcessCardProps {
  steps: ProcessStep[];
  streaming?: boolean;
  defaultExpanded?: boolean;
  onOpenKnowledgeFile?: (kbPath: string) => void;
}

function summarizeToolArgs(raw?: string): string {
  if (!raw) return '';
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null) {
      const entries = Object.entries(parsed as Record<string, unknown>);
      const parts = entries.slice(0, 3).map(([k, v]) => {
        const val = typeof v === 'string' ? v : JSON.stringify(v);
        const trimmed = val.length > 40 ? val.slice(0, 40) + '…' : val;
        return k + ': ' + trimmed;
      });
      const more = entries.length > 3 ? ' +' + (entries.length - 3) : '';
      return parts.join(', ') + more;
    }
    return String(parsed);
  } catch {
    return raw.length > 80 ? raw.slice(0, 80) + '…' : raw;
  }
}

function extractKnowledgePath(toolArgs?: string): { fullPath: string; kbPath: string } | null {
  if (!toolArgs) return null;
  // Try JSON first (persisted steps from buildProcessStepsForTurn carry proper
  // JSON toolArgs). Streaming steps carry ev.detail which is a human-readable
  // preview string, so we fall back to a regex search for /knowledge/<path>.
  try {
    const parsed = JSON.parse(toolArgs);
    const p = parsed?.path;
    if (typeof p === 'string') {
      const idx = p.indexOf('/knowledge/');
      if (idx !== -1) {
        const kbPath = p.slice(idx + '/knowledge/'.length);
        if (kbPath) return { fullPath: p, kbPath };
      }
    }
  } catch {
    // Not JSON - fall through to regex
  }
  const idx = toolArgs.indexOf('/knowledge/');
  if (idx !== -1) {
    const after = toolArgs.slice(idx + '/knowledge/'.length);
    const match = after.match(/^([^\s"',}\]]+)/);
    if (match && match[1]) {
      return { fullPath: '/knowledge/' + match[1], kbPath: match[1] };
    }
  }
  return null;
}

function ProcessCardImpl({ steps, streaming = false, defaultExpanded, onOpenKnowledgeFile }: ProcessCardProps) {
  const [expanded, setExpanded] = useState(streaming || defaultExpanded);

  useEffect(() => {
    if (streaming) setExpanded(true);
  }, [streaming]);

  if (steps.length === 0) return null;

  const toolCount = steps.filter(s => s.type === 'tool').length;
  const hasThinking = steps.some(s => s.type === 'thinking' && s.content?.trim());
  const runningTool = steps.find(s => s.type === 'tool' && s.status === 'running');

  const headerLabel = streaming
    ? (runningTool ? '正在调用工具…' : (hasThinking ? '正在思考…' : '处理中…'))
    : '处理过程';

  const summary = streaming
    ? (runningTool ? (runningTool.toolName || '工具') : (hasThinking ? '思考' : '处理'))
    : (toolCount > 0 ? (hasThinking ? '思考 + ' + toolCount + ' 次工具调用' : toolCount + ' 次工具调用') : '思考过程');

  return (
    <div className="my-1.5 w-full rounded-lg border border-light-border bg-light-card-hover/40">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-light-text-secondary hover:text-light-text transition-colors text-left"
      >
        <ChevronRight
          size={12}
          className={'shrink-0 transition-transform ' + (expanded ? 'rotate-90' : '')}
        />
        {streaming ? (
          <Loader2 size={12} className="shrink-0 animate-spin text-accent-blue" />
        ) : (
          <CircleCheck size={12} className="shrink-0 text-accent-green/70" />
        )}
        <span className="font-medium">{headerLabel}</span>
        <span className="text-light-text-secondary/70">·</span>
        <span className="truncate">{summary}</span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-light-border/60 px-3 py-2">
          {steps.map((step, idx) => {
            if (step.type === 'thinking') {
              const text = (step.content || '').trim();
              if (!text) return null;
              return (
                <div key={idx} className="flex items-start gap-2 text-xs">
                  <Brain size={12} className="mt-0.5 shrink-0 text-purple-400/80" />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-light-text-secondary">思考</div>
                    <pre className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-light-text-secondary max-h-60 overflow-y-auto font-sans leading-relaxed">{text}</pre>
                  </div>
                </div>
              );
            }
            if (step.type === 'text') {
              const text = (step.content || '').trim();
              if (!text) return null;
              return (
                <div key={idx} className="flex items-start gap-2 text-xs">
                  <MessageSquare size={12} className="mt-0.5 shrink-0 text-accent-blue/80" />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-light-text-secondary">输出中</div>
                    <div className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-light-text max-h-60 overflow-y-auto leading-relaxed">
                      {text}
                      {streaming && <span className="inline-block w-1 h-3 ml-0.5 bg-accent-blue rounded-sm animate-pulse align-text-bottom" />}
                    </div>
                  </div>
                </div>
              );
            }
            const status = step.status || 'completed';
            const icon = status === 'failed' ? (
              <AlertCircle size={12} className="mt-0.5 shrink-0 text-accent-red" />
            ) : status === 'running' ? (
              <Loader2 size={12} className="mt-0.5 shrink-0 animate-spin text-accent-blue" />
            ) : (
              <Wrench size={12} className="mt-0.5 shrink-0 text-accent-green/80" />
            );
            const args = summarizeToolArgs(step.toolArgs);
            // formatToolName converts write_file -> "write file", so match
            // loosely. Both write_file and patch carry a `path` arg that may
            // point into the knowledge base.
            const isKbWriter = /^(write[ _]file|patch)$/i.test(step.toolName || '');
            const kb = isKbWriter ? extractKnowledgePath(step.toolArgs) : null;
            return (
              <div key={idx} className="flex items-start gap-2 text-xs">
                {icon}
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-light-text">
                    {step.toolName || '工具'}
                    {status === 'running' && <span className="ml-1 text-accent-blue">调用中…</span>}
                    {status === 'completed' && step.duration != null && (
                      <span className="ml-1 font-normal text-light-text-secondary">{step.duration.toFixed(1)}s</span>
                    )}
                    {status === 'failed' && <span className="ml-1 text-accent-red">失败</span>}
                  </div>
                  {kb && onOpenKnowledgeFile ? (
                    <div className="mt-0.5 flex items-center gap-1 text-[11px] font-mono text-light-text-secondary">
                      <FileText size={11} className="shrink-0" />
                      <button
                        type="button"
                        onClick={() => onOpenKnowledgeFile(kb.kbPath)}
                        className="text-accent-blue hover:underline truncate"
                        title={kb.fullPath}
                      >
                        {kb.kbPath}
                      </button>
                    </div>
                  ) : args ? (
                    <div className="mt-0.5 break-words text-[11px] text-light-text-secondary font-mono">{args}</div>
                  ) : null}
                  {status === 'failed' && step.error && (
                    <div className="mt-0.5 break-words text-[11px] text-accent-red">{step.error}</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const ProcessCard = memo(ProcessCardImpl);
