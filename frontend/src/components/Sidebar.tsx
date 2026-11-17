import type { ChatApi } from '../hooks/useChat';
import type { User } from '../types';
import ChatList from './ChatList';
import DocumentsPanel from './DocumentsPanel';
import { CloseIcon, PlusIcon } from './Icons';

interface Props {
  open: boolean;
  onClose: () => void;
  chat: ChatApi;
  user?: User | null; // Future: logged-in user
}

export default function Sidebar({ open, onClose, chat, user }: Props) {
  return (
    <>
      <div className={`backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-top">
          <button className="btn-primary" onClick={() => { chat.newChat(); onClose(); }}>
            <PlusIcon /> New chat
          </button>
          <button className="icon-btn only-mobile" onClick={onClose} aria-label="Close menu">
            <CloseIcon />
          </button>
        </div>

        <div className="sidebar-body">
          <DocumentsPanel chat={chat} onDone={onClose} />
          <ChatList chat={chat} onDone={onClose} />
        </div>

        <div className="sidebar-bottom">
          {user ? (
            <div className="user-row">
              <span className="avatar">{user.name[0]}</span>
              <span>{user.name}</span>
            </div>
          ) : (
            <button className="btn-ghost" disabled title="Coming soon">Sign in (coming soon)</button>
          )}
        </div>
      </aside>
    </>
  );
}
