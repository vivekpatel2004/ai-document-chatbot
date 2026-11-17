import { useEffect } from 'react';

export interface Confirmation {
  title: string;
  description: string;
  actionLabel: string;
  onConfirm: () => void | Promise<void>;
}

export default function ConfirmDialog({ confirmation, onClose }: { confirmation: Confirmation | null; onClose: () => void }) {
  useEffect(() => {
    if (!confirmation) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [confirmation, onClose]);

  if (!confirmation) return null;
  return <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
      <h2 id="confirm-title">{confirmation.title}</h2>
      <p id="confirm-description">{confirmation.description}</p>
      <div className="dialog-actions"><button className="secondary-action" onClick={onClose}>Cancel</button><button className="dialog-danger" onClick={() => { void confirmation.onConfirm(); onClose(); }}>{confirmation.actionLabel}</button></div>
    </section>
  </div>;
}
