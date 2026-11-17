import { useRef } from 'react';
import type { ChatApi } from '../hooks/useChat';
import { CompareIcon, FileIcon, ListIcon, SparkIcon, TrashIcon } from './Icons';

export default function DocumentsPanel({ chat, onDone }: { chat: ChatApi; onDone: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const none = chat.docs.length === 0;
  const busy = chat.streaming || chat.uploading;

  const tool = (t: 'summary' | 'extract' | 'compare') => {
    void chat.runTool(t);
    onDone();
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span>Documents{chat.docs.length ? ` (${chat.docs.length})` : ''}</span>
        <button className="link-btn" onClick={() => ref.current?.click()} disabled={chat.uploading}>
          {chat.uploading ? 'Uploading…' : '+ Add'}
        </button>
      </div>
      <input
        ref={ref}
        type="file"
        hidden
        multiple
        accept=".pdf,.docx,.doc,.txt"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) void chat.upload(files);
          e.target.value = '';
        }}
      />

      {none ? (
        <p className="muted small">Add one or more files to ask questions across them.</p>
      ) : (
        <ul className="doc-list">
          {chat.docs.map((d) => (
            <li key={d.id} className="doc-row" title={d.filename}>
              <FileIcon />
              <span className="doc-name">{d.filename}</span>
              {d.pages ? <span className="muted tiny">{d.pages}p</span> : null}
              <button
                className="row-btn"
                onClick={() => void chat.removeDoc(d)}
                disabled={busy}
                aria-label={`Remove ${d.filename}`}
                title="Remove document"
              >
                <TrashIcon />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="tools">
        <button className="tool-btn" disabled={none || busy} onClick={() => tool('summary')}>
          <SparkIcon /> Summary &amp; insights
        </button>
        <button className="tool-btn" disabled={none || busy} onClick={() => tool('extract')}>
          <ListIcon /> Extract names, dates, amounts
        </button>
        <button
          className="tool-btn"
          disabled={chat.docs.length < 2 || busy}
          onClick={() => tool('compare')}
          title={chat.docs.length < 2 ? 'Add at least two documents' : 'Compare documents'}
        >
          <CompareIcon /> Compare documents
        </button>
      </div>
    </section>
  );
}
