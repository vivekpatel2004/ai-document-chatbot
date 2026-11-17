import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import ChatInput from './components/ChatInput';
import ChatWindow from './components/ChatWindow';
import ConfirmDialog from './components/ConfirmDialog';
import type { Confirmation } from './components/ConfirmDialog';
import { MenuIcon, FileIcon } from './components/Icons';
import SuggestionChips from './components/SuggestionChips';
import AuthPage from './pages/AuthPage';
import { useChat } from './hooks/useChat';
import WorkspaceSidebar from './components/WorkspaceSidebar';
import ExportMenu from './components/ExportMenu';
import { deleteDocument, downloadExport, fetchAllDocuments, fetchCurrentUser, getErrorMessage, getToken, login, logout, setToken, signup } from './services/api';
import type { ExportFormat, UploadedDocument, User } from './types';

type Page = 'dashboard' | 'chat' | 'documents' | 'history' | 'settings';

export default function App() {
  const chat = useChat();
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authMode, setAuthMode] = useState<'login' | 'signup'>('login');
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState('');
  const [page, setPage] = useState<Page>('chat');
  const [drawer, setDrawer] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => localStorage.getItem('docchat-theme') === 'light' ? 'light' : 'dark');
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const [docsBusy, setDocsBusy] = useState(true);
  const [documentsError, setDocumentsError] = useState('');
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; message: string } | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [authName, setAuthName] = useState('');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const pickerRef = useRef<HTMLInputElement>(null);
  const workspaceToken = useRef(0);
  const documentsRequestToken = useRef(0);

  const title = chat.history.find((item) => item.id === chat.conversationId)?.title ?? (chat.docs[0]?.filename ? `Chat about ${chat.docs[0].filename}` : 'New chat');
  const hasMessages = chat.messages.length > 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('docchat-theme', theme);
  }, [theme]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!getToken()) { setAuthChecked(true); return; }
    fetchCurrentUser().then(setUser).catch(() => setToken(null)).finally(() => setAuthChecked(true));
  }, []);

  const loadDocuments = useCallback(async () => {
    const requestToken = ++documentsRequestToken.current;
    const ownerToken = workspaceToken.current;
    setDocsBusy(true);
    setDocumentsError('');
    try {
      const result = await fetchAllDocuments();
      if (requestToken === documentsRequestToken.current && ownerToken === workspaceToken.current) setDocuments(result);
    } catch (error) {
      if (requestToken === documentsRequestToken.current && ownerToken === workspaceToken.current) setDocumentsError(getErrorMessage(error));
    } finally {
      if (requestToken === documentsRequestToken.current && ownerToken === workspaceToken.current) setDocsBusy(false);
    }
  }, []);

  useEffect(() => {
    if (user) void chat.refreshHistory();
  }, [user]);

  useEffect(() => {
    if (user) void loadDocuments();
  }, [user, chat.conversationId, loadDocuments]);

  const handleAuth = async (event: FormEvent) => {
    event.preventDefault();
    setAuthError(''); setAuthBusy(true);
    try {
      const signedIn = authMode === 'signup'
        ? await signup(authName, authEmail, authPassword)
        : await login(authEmail, authPassword);
      setUser(signedIn); setPage('chat');
    } catch (error) { setAuthError(getErrorMessage(error)); }
    finally { setAuthBusy(false); }
  };

  const signOut = async () => {
    await logout().catch(() => undefined);
    workspaceToken.current += 1;
    documentsRequestToken.current += 1;
    chat.resetWorkspace();
    setUser(null); setDocuments([]); setDocumentsError(''); setDocsBusy(false); setNotice(null); setPage('chat');
  };

  const handleUpload = async (files: File[]) => {
    const ownerToken = workspaceToken.current;
    setNotice(null);
    const result = await chat.upload(files);
    if (ownerToken !== workspaceToken.current) return;
    await loadDocuments();
    if (ownerToken !== workspaceToken.current) return;
    if (result.errors.length) setNotice({ kind: 'error', message: `${result.errors[0].filename}: ${result.errors[0].error}` });
    else if (result.uploaded) setNotice({ kind: 'success', message: 'Document uploaded successfully.' });
    else setNotice({ kind: 'error', message: 'No document could be uploaded.' });
  };

  const exportChat = async (format: ExportFormat) => {
    if (!chat.conversationId) return;
    setExportBusy(true);
    setNotice(null);
    try {
      await downloadExport(format, chat.conversationId);
      setNotice({ kind: 'success', message: `${format.toUpperCase()} downloaded successfully.` });
    } catch (error) {
      setNotice({ kind: 'error', message: getErrorMessage(error) });
    } finally {
      setExportBusy(false);
    }
  };

  const removeDocument = async (document: UploadedDocument) => {
    try { await deleteDocument(document.id); chat.forgetDoc(document.id); await loadDocuments(); setNotice({ kind: 'success', message: 'Document deleted.' }); }
    catch (error) { setNotice({ kind: 'error', message: getErrorMessage(error) }); }
  };

  if (!authChecked) return <div className="boot-screen"><div className="brand-mark"><FileIcon /></div><span>Preparing your workspace…</span></div>;

  if (!user) return <AuthPage mode={authMode} setMode={(mode) => { setAuthMode(mode); setAuthError(''); }} name={authName} setName={setAuthName} email={authEmail} setEmail={setAuthEmail} password={authPassword} setPassword={setAuthPassword} error={authError} busy={authBusy} onSubmit={handleAuth} />;

  const nav = (next: Page) => { setPage(next); setDrawer(false); };
  const openDocumentChat = (doc: UploadedDocument) => {
    chat.startDocumentChat({ id: doc.id, filename: doc.filename, pages: doc.total_pages });
    nav('chat');
  };

  const openHistoryConversation = (id: string) => {
    void chat.openConversation(id);
    nav('chat');
  };

  return <div className="app-shell">
    <WorkspaceSidebar page={page} user={user} open={drawer} history={chat.history} historyError={chat.historyError} historyLoading={chat.historyLoading} conversationId={chat.conversationId} onNavigate={nav} onNewChat={() => { chat.newChat(); nav('chat'); }} onOpenConversation={(id) => { void chat.openConversation(id); nav('chat'); }} onSignOut={() => void signOut()} />
    <main className="app-main">
      <header className="app-topbar"><button className="mobile-menu" onClick={() => setDrawer(true)} aria-label="Open navigation"><MenuIcon /></button><div className="breadcrumb"><span>AI Document Chatbot</span><b>/</b><strong>{page === 'chat' ? title : ({ dashboard: 'Overview', documents: 'Documents', history: 'Chat history', settings: 'Settings' } as Record<Page,string>)[page]}</strong></div><div className="topbar-tools"><button className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title="Toggle theme"><span>{theme === 'dark' ? '☼' : '☾'}</span></button><button className="top-avatar" onClick={() => nav('settings')} aria-label="Account settings">{user.name.slice(0,1).toUpperCase()}</button></div></header>
      <input ref={pickerRef} type="file" hidden multiple accept=".pdf,.docx,.txt" onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) void handleUpload(files); event.target.value = ''; }} />
      {notice && <div className={`toast ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.message}<button onClick={() => setNotice(null)} aria-label="Dismiss notification">×</button></div>}
      {page === 'dashboard' && <section className="page-content dashboard-page"><div className="page-title-row"><div><span className="eyebrow">OVERVIEW</span><h1>Your workspace</h1><p>Your documents and recent conversations.</p></div></div>
        <div className="overview-grid">
          <section className="overview-section"><div className="section-heading"><div><h2>Recent documents</h2><p>Open a document to continue its chat.</p></div><button className="text-action" onClick={() => nav('documents')}>All documents →</button></div>
            {documentsError ? <p className="form-error" role="alert">{documentsError}</p> : docsBusy ? <p className="loading-copy">Loading documents…</p> : documents.length ? <div className="overview-list">{documents.slice(0,5).map((doc) => <button className="overview-row" key={doc.id} onClick={() => openDocumentChat(doc)} disabled={doc.status !== 'processed'}><span className="file-badge">{doc.file_type.toUpperCase()}</span><span><b>{doc.filename}</b><small>{doc.status === 'processed' ? `${doc.total_pages ? `${doc.total_pages} pages` : 'Text document'} · ${formatDate(doc.created_at)}` : doc.status}</small></span><span className="card-arrow">→</span></button>)}</div> : <div className="quiet-empty">No documents uploaded yet.</div>}
          </section>
          <section className="overview-section"><div className="section-heading"><div><h2>Recent conversations</h2><p>Pick up where you left off.</p></div><button className="text-action" onClick={() => nav('history')}>All chats →</button></div>
            {chat.historyError ? <p className="form-error" role="alert">{chat.historyError}</p> : chat.historyLoading && !chat.history.length ? <p className="loading-copy">Loading conversations…</p> : chat.history.length ? <div className="overview-list">{chat.history.slice(0,5).map((conversation) => <button className="overview-row" key={conversation.id} onClick={() => openHistoryConversation(conversation.id)}><span className="conversation-icon">◷</span><span><b>{conversation.title}</b><small>{conversation.message_count ?? 0} messages · {formatDate(conversation.updated_at || conversation.created_at)}</small></span><span className="card-arrow">→</span></button>)}</div> : <div className="quiet-empty">No conversations yet.</div>}
          </section>
        </div>
      </section>}
      {page === 'documents' && <section className="page-content"><div className="page-title-row"><div><span className="eyebrow">YOUR LIBRARY</span><h1>Documents</h1><p>Choose a document to chat about it. Attach new files from New Chat.</p></div></div>{documentsError && <p className="form-error" role="alert">{documentsError}</p>}{docsBusy && <p className="loading-copy">Updating your library…</p>}{documents.length ? <div className="library-list">{documents.map((doc) => <div className="library-row" key={doc.id}><span className="file-badge">{doc.file_type.toUpperCase().slice(0,4)}</span><span className="library-meta"><b>{doc.filename}</b><small>{doc.file_size ? formatSize(doc.file_size) : '—'} · {formatDate(doc.created_at)} · {doc.status}</small></span><button className="secondary-action" onClick={() => openDocumentChat(doc)} disabled={doc.status !== 'processed'}>Chat with document</button><button className="delete-action" onClick={() => setConfirmation({ title: 'Delete document?', description: `“${doc.filename}” and its searchable content will be deleted.`, actionLabel: 'Delete document', onConfirm: () => removeDocument(doc) })} aria-label={`Delete ${doc.filename}`}>×</button></div>)}</div> : !docsBusy && !documentsError && <div className="quiet-empty">Your library is empty.</div>}</section>}
      {page === 'history' && <section className="page-content"><div className="page-title-row"><div><span className="eyebrow">YOUR WORK</span><h1>Chat history</h1><p>All your document conversations in one place.</p></div></div>{chat.historyError && <p className="form-error" role="alert">{chat.historyError}</p>}{chat.historyLoading && !chat.history.length ? <p className="loading-copy">Loading conversations…</p> : chat.history.length ? <div className="history-list">{chat.history.map((item) => <div className="history-row-wrap" key={item.id}><button className="history-row" onClick={() => openHistoryConversation(item.id)}><span className="conversation-icon">◷</span><span className="history-meta"><b>{item.title}</b><small>{item.message_count ?? 0} messages · {formatDate(item.updated_at || item.created_at)}</small></span><span className="row-count">{item.document_count ?? 0} docs</span><span className="card-arrow">→</span></button><button className="history-delete" aria-label={`Delete ${item.title}`} title="Delete conversation" onClick={() => setConfirmation({ title: 'Delete conversation?', description: `“${item.title}” and its messages will be deleted. Uploaded documents stay in your library.`, actionLabel: 'Delete conversation', onConfirm: async () => { await chat.deleteChat(item.id); await loadDocuments(); } })}>×</button></div>)}</div> : !chat.historyError && <div className="quiet-empty">No conversations yet.</div>}</section>}
      {page === 'settings' && <section className="page-content"><div className="page-title-row"><div><span className="eyebrow">PREFERENCES</span><h1>Settings</h1><p>Manage your account and workspace appearance.</p></div></div><div className="settings-card"><div className="settings-section"><span className="settings-icon">◉</span><div><h2>Profile</h2><p>Your account information</p></div></div><div className="settings-fields"><label>Full name<input readOnly value={user.name} /></label><label>Email address<input readOnly value={user.email} /></label></div><div className="settings-section setting-divider"><span className="settings-icon">◐</span><div><h2>Appearance</h2><p>Choose how this application looks.</p></div></div><div className="appearance-row"><span><b>Theme</b><small>Your preference is saved in this browser.</small></span><div className="segmented"><button className={theme === 'light' ? 'selected' : ''} onClick={() => setTheme('light')}>☼ Light</button><button className={theme === 'dark' ? 'selected' : ''} onClick={() => setTheme('dark')}>☾ Dark</button></div></div><div className="settings-footer"><button className="danger-button" onClick={() => void signOut()}>Sign out</button></div></div></section>}
      {page === 'chat' && <div className="chat-page"><div className="chat-toolbar"><label className="chat-select-label"><span>Document</span><select className="document-select" aria-label="Select a document" value={chat.selectedDocumentId ?? ''} onChange={(event) => { const next = documents.find((doc) => doc.id === event.target.value); if (next) chat.selectDocument({ id: next.id, filename: next.filename, pages: next.total_pages }); }}><option value="">Select document</option>{documents.filter((doc) => doc.status === 'processed').map((doc) => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select></label>{chat.conversationId && hasMessages && <ExportMenu compact onExport={(format) => void exportChat(format)} disabled={exportBusy} />}</div>
        {hasMessages ? <ChatWindow messages={chat.messages} title={title} /> : <div className="chat-welcome"><h2>{chat.selectedDocumentId ? `Ask about ${documents.find((doc) => doc.id === chat.selectedDocumentId)?.filename ?? chat.docs.find((doc) => doc.id === chat.selectedDocumentId)?.filename}` : 'Attach a document to start chatting'}</h2>{chat.docs.length > 0 && <SuggestionChips items={chat.suggestions.slice(0,4)} onPick={(question) => void chat.send(question)} />}</div>}
        <div className="chat-input-wrap">{hasMessages && !chat.streaming && <SuggestionChips items={chat.suggestions.slice(0,4)} onPick={(question) => void chat.send(question)} />}<ChatInput streaming={chat.streaming} uploading={chat.uploading} canSend={!!chat.selectedDocumentId} onSend={(message) => void chat.send(message)} onStop={chat.stop} onAttach={() => pickerRef.current?.click()} /><p className="footer-note">Answers use the selected document only.</p></div>
      </div>}
    </main>
    <ConfirmDialog confirmation={confirmation} onClose={() => setConfirmation(null)} />
  </div>;
}

function formatDate(value?: string | null) { if (!value) return 'Date unavailable'; const date = new Date(value); return Number.isNaN(date.getTime()) ? 'Date unavailable' : new Intl.DateTimeFormat(undefined,{ month:'short', day:'numeric', year:'numeric' }).format(date); }
function formatSize(bytes: number) { return bytes < 1024*1024 ? `${Math.max(1,Math.round(bytes/1024))} KB` : `${(bytes/(1024*1024)).toFixed(1)} MB`; }
