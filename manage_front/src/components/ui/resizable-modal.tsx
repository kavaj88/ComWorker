"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { X } from "lucide-react";

interface ResizableModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  initialWidth?: number;
  initialHeight?: number;
  minWidth?: number;
  minHeight?: number;
}

/**
 * 可拖动（标题栏）+ 可缩放（右下角）的模态框。默认尺寸较大，用户可自由调整。
 */
export function ResizableModal({
  open,
  onClose,
  title,
  children,
  footer,
  initialWidth = 920,
  initialHeight = 660,
  minWidth = 480,
  minHeight = 360,
}: ResizableModalProps) {
  const [size, setSize] = useState({ w: initialWidth, h: initialHeight });
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);
  const resizeRef = useRef<{ sx: number; sy: number; sw: number; sh: number } | null>(null);

  useEffect(() => {
    if (!open) return;
    function onMove(e: MouseEvent) {
      if (dragRef.current) {
        setPos((p) =>
          p ? { x: e.clientX - dragRef.current!.dx, y: e.clientY - dragRef.current!.dy } : p,
        );
      } else if (resizeRef.current) {
        const dw = e.clientX - resizeRef.current.sx;
        const dh = e.clientY - resizeRef.current.sy;
        setSize({
          w: Math.max(minWidth, resizeRef.current.sw + dw),
          h: Math.max(minHeight, resizeRef.current.sh + dh),
        });
      }
    }
    function onUp() {
      dragRef.current = null;
      resizeRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [open, minWidth, minHeight]);

  if (!open) return null;

  const style: CSSProperties =
    pos === null
      ? {
          width: size.w,
          height: size.h,
          left: "50%",
          top: "50%",
          transform: "translate(-50%, -50%)",
        }
      : { width: size.w, height: size.h, left: pos.x, top: pos.y };

  return (
    <div
      className="fixed inset-0 z-50 flex bg-black/40"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={modalRef}
        className="absolute flex flex-col overflow-hidden rounded-xl border bg-white shadow-2xl"
        style={style}
      >
        <div
          className="flex cursor-move select-none items-center justify-between border-b px-4 py-2"
          onMouseDown={(e) => {
            const el = modalRef.current;
            if (!el) return;
            const rect = el.getBoundingClientRect();
            setPos({ x: rect.left, y: rect.top });
            dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
          }}
        >
          <div className="text-sm font-semibold">{title}</div>
          <button onClick={onClose} className="rounded p-1 hover:bg-gray-100" aria-label="关闭">
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t px-4 py-3">{footer}</div>}
        <div
          className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize"
          onMouseDown={(e) => {
            e.stopPropagation();
            const el = modalRef.current;
            if (!el) return;
            const rect = el.getBoundingClientRect();
            resizeRef.current = {
              sx: e.clientX,
              sy: e.clientY,
              sw: rect.width,
              sh: rect.height,
            };
          }}
          title="拖动缩放"
        >
          <div className="h-full w-full border-b-2 border-r-2 border-gray-400" />
        </div>
      </div>
    </div>
  );
}
