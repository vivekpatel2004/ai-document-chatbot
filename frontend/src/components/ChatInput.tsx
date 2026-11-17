import { useEffect, useRef, useState } from 'react';
import { PaperclipIcon, SendIcon, StopIcon } from './Icons';

interface Props {
  streaming: boolean;
  uploading: boolean;
  canSend: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  onAttach: () => void;
}

export default function ChatInput({ streaming, uploading, canSend, onSend, onStop, onAttach }: Props) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 180) + 'px';
  }, [value]);

  const submit = () => {
    if (!value.trim() || !canSend || streaming || uploading) return;
    onSend(value);
    setValue('');
  };

  return (
    <div className="input-box">
      <button className="icon-btn" onClick={onAttach} disabled={uploading || streaming} title="Add documents" aria-label="Add documents">
        <PaperclipIcon />
      </button>
      <textarea
        ref={ref}
        rows={1}
        value={value}
        placeholder={canSend ? 'Ask about your documents…' : 'Attach a document, then ask a question…'}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      {streaming ? (
        <button className="send stop" onClick={onStop} aria-label="Stop generating" title="Stop">
          <StopIcon />
        </button>
      ) : (
        <button className="send" onClick={submit} disabled={!value.trim() || !canSend || uploading} aria-label="Send message">
          <SendIcon />
        </button>
      )}
    </div>
  );
}
