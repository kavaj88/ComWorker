import React, { useState, useEffect } from 'react';
import { Brain, ChevronRight } from 'lucide-react';

interface ThinkingCardProps {
  content: string;
  streaming?: boolean;
}

export function ThinkingCard({ content, streaming = false }: ThinkingCardProps) {
  const [expanded, setExpanded] = useState(false);

  // Persist expanded state to sessionStorage
  useEffect(() => {
    const saved = sessionStorage.getItem('thinking-card-expanded');
    if (saved === 'true') {
      setExpanded(true);
    }
  }, []);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    sessionStorage.setItem('thinking-card-expanded', String(next));
  };

  if (!content.trim()) return null;

  return (
    <div className="my-1.5 w-full">
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-purple-500/5 border border-purple-500/20 hover:bg-purple-500/10 transition-colors text-xs text-purple-300/80 w-full text-left"
      >
        <ChevronRight
          size={12}
          className={`transition-transform shrink-0 ${expanded ? 'rotate-90' : ''}`}
        />
        <Brain size={12} className="shrink-0" />
        <span>{streaming ? '思考中...' : '思考过程'}</span>
      </button>
      {expanded && (
        <div className="mt-1 ml-4 pl-3 border-l-2 border-purple-500/20">
          <pre className="text-[11px] text-dark-text-secondary whitespace-pre-wrap break-words max-h-80 overflow-y-auto p-2">
            {content}
          </pre>
        </div>
      )}
    </div>
  );
}
