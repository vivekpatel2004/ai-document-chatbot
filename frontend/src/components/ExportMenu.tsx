import { useEffect, useRef, useState } from 'react';
import type { ExportFormat } from '../types';
import { DownloadIcon } from './Icons';

interface Props {
  onExport: (format: ExportFormat) => void;
  disabled?: boolean;
  label?: string;
  compact?: boolean;
}

export default function ExportMenu({ onExport, disabled, label = 'Export', compact }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div className="export" ref={ref}>
      <button
        className={compact ? 'chip-btn' : 'top-btn'}
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Export"
      >
        <DownloadIcon /> {label}
      </button>
      {open && (
        <div className="menu" role="menu">
          <button role="menuitem" onClick={() => { setOpen(false); onExport('pdf'); }}>Download PDF</button>
          <button role="menuitem" onClick={() => { setOpen(false); onExport('md'); }}>Download Markdown</button>
          <button role="menuitem" onClick={() => { setOpen(false); onExport('txt'); }}>Download TXT</button>
        </div>
      )}
    </div>
  );
}
