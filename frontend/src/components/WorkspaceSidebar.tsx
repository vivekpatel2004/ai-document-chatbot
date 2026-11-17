import { FileIcon } from './Icons';
import type { ConversationSummary, ID, User } from '../types';

type Page = 'dashboard' | 'chat' | 'documents' | 'history' | 'settings';

interface Props {
  page: Page;
  user: User;
  open: boolean;
  history: ConversationSummary[];
  historyError: string;
  historyLoading: boolean;
  conversationId: ID | null;
  onNavigate: (page: Page) => void;
  onNewChat: () => void;
  onOpenConversation: (id: ID) => void;
  onSignOut: () => void;
}

export default function WorkspaceSidebar({ page, user, open, history, historyError, historyLoading, conversationId, onNavigate, onNewChat, onOpenConversation, onSignOut }: Props) {
  const item = (target: Page, label: string, glyph: string) => <button className={`nav-item ${page === target ? 'active' : ''}`} onClick={() => onNavigate(target)}><span className="nav-glyph">{glyph}</span>{label}</button>;
  return <>
    <div className={`drawer-scrim ${open ? 'visible' : ''}`} onClick={() => onNavigate(page)} />
    <aside className={`app-sidebar ${open ? 'drawer-open' : ''}`}>
      <div className="sidebar-brand"><div className="brand"><span className="brand-mark"><FileIcon /></span><span>AI Document Chatbot</span></div><span className="workspace-label">WORKSPACE</span></div>
      <button className="new-chat-button" onClick={onNewChat}><span>＋</span> New chat</button>
      <nav className="primary-nav" aria-label="Main navigation">{item('dashboard', 'Overview', '⌂')}{item('documents', 'Documents', '▤')}{item('history', 'Chat history', '◷')}</nav>
      <div className="sidebar-divider" />
      <div className="sidebar-section-head"><span>RECENT CHATS</span>{history.length > 5 && <button onClick={() => onNavigate('history')}>View all</button>}</div>
      <div className="recent-list" aria-label="Recent conversations">
        {history.slice(0, 8).map((conversation) => <button className={`recent-chat ${conversation.id === conversationId ? 'selected' : ''}`} key={conversation.id} title={conversation.title} onClick={() => { onOpenConversation(conversation.id); }}><span className="recent-chat-icon">◷</span><span>{conversation.title}</span></button>)}
        {!history.length && <span className="sidebar-empty">{historyError ? 'Could not load chats' : historyLoading ? 'Loading chats…' : 'No conversations yet'}</span>}
      </div>
      <div className="sidebar-spacer" />
      <div className="sidebar-bottom">{item('settings', 'Settings', '⚙')}<div className="profile-row"><span className="profile-avatar">{user.name.slice(0, 1).toUpperCase()}</span><span className="profile-meta"><b>{user.name}</b><small>{user.email}</small></span><button className="logout-button" onClick={onSignOut}>Log out</button></div></div>
    </aside>
  </>;
}
