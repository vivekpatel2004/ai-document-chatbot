import { useState } from 'react';
import type { ChatApi } from '../hooks/useChat';
import type { ID } from '../types';
import { EditIcon, TrashIcon } from './Icons';

export default function ChatList({ chat, onDone }: { chat: ChatApi; onDone: () => void }) {
  const [editing, setEditing] = useState<ID | null>(null);
  const [draft, setDraft] = useState('');

  const save = () => {
    if (editing != null) void chat.renameChat(editing, draft);
    setEditing(null);
  };

  return (
    <section className="panel grow">
      <div className="panel-head"><span>Chats</span></div>
      {chat.history.length === 0 ? (
        <p className="muted small">Your saved chats will appear here.</p>
      ) : (
        <ul className="chat-list">
          {chat.history.map((c) => (
            <li key={c.id} className={`chat-row ${c.id === chat.conversationId ? 'active' : ''}`}>
              {editing === c.id ? (
                <input
                  className="rename-input"
                  autoFocus
                  value={draft}
                  maxLength={120}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={save}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') save();
                    if (e.key === 'Escape') setEditing(null);
                  }}
                />
              ) : (
                <>
                  <button className="chat-title" onClick={() => { void chat.openConversation(c.id); onDone(); }} title={c.title}>
                    {c.title || 'Untitled chat'}
                  </button>
                  <button
                    className="row-btn"
                    aria-label="Rename chat"
                    title="Rename"
                    onClick={() => { setEditing(c.id); setDraft(c.title); }}
                  >
                    <EditIcon />
                  </button>
                  <button
                    className="row-btn"
                    aria-label="Delete chat"
                    title="Delete"
                    onClick={() => {
                      if (window.confirm(`Delete "${c.title}"? Its messages and documents will be removed.`)) void chat.deleteChat(c.id);
                    }}
                  >
                    <TrashIcon />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
