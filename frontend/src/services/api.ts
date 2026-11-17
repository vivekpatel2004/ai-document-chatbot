
import axios, { AxiosError } from 'axios';

import type {
  ChatRequest,
  ChatResponse,
  ConversationSummary,
  DocInfo,
  ExportFormat,
  ID,
  Message,
  Source,
  SourceReference,
  ToolName,
  ToolResult,
  UploadResponse,
  UploadedDocument,
} from '../types';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

export { API_BASE_URL };

const TOKEN_KEY = 'auth_token';

export const getToken = (): string | null =>
  localStorage.getItem(TOKEN_KEY);

export const setToken = (token: string | null): void => {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
};

export async function signup(name: string, email: string, password: string) {
  const { data } = await http.post<{ token: string; user: import('../types').User }>('/api/auth/signup', { name, email, password });
  setToken(data.token);
  return data.user;
}

export async function login(email: string, password: string) {
  const { data } = await http.post<{ token: string; user: import('../types').User }>('/api/auth/login', { email, password });
  setToken(data.token);
  return data.user;
}

export async function fetchCurrentUser() {
  const { data } = await http.get<{ user: import('../types').User }>('/api/auth/me');
  return data.user;
}

export async function logout() {
  try { await http.post('/api/auth/logout'); } finally { setToken(null); }
}

const http = axios.create({
  baseURL: API_BASE_URL,
});

http.interceptors.request.use((config) => {
  const token = getToken();

  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }

  return config;
});

/* ---------------- Errors ---------------- */

export class StreamUnavailableError extends Error {
  constructor(message = 'Streaming endpoint is unavailable') {
    super(message);
    this.name = 'StreamUnavailableError';
  }
}

export const isAbortError = (error: unknown): boolean => {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    axios.isCancel(error)
  );
};

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<{
      detail?: string;
      message?: string;
      error?: string;
    }>;

    if (axiosError.code === 'ECONNABORTED') {
      return 'Request timed out. The server took too long to respond.';
    }

    if (axiosError.response) {
      const data = axiosError.response.data;

      return (
        data?.detail ||
        data?.message ||
        data?.error ||
        `Server error: ${axiosError.response.status}`
      );
    }

    if (axiosError.request) {
      return `Cannot connect to backend at ${API_BASE_URL}. Check whether the backend is running.`;
    }
  }

  if (error instanceof TypeError) {
    return error.message;
  }

  return error instanceof Error
    ? error.message
    : 'Unknown error occurred';
}

/* ---------------- Source normalization ---------------- */

export function normalizeSources(
  sources: Array<Source | SourceReference> = []
): Source[] {
  const normalized = sources.map((source, index) => {
    if ('filename' in source) {
      return {
        n: index + 1,
        document_id: source.document_id,
        document: source.filename,
        page: source.page_number ?? null,
        snippet: source.excerpt ?? '',
        chunk_id: source.chunk_id ?? null,
        relevance_score: source.relevance_score ?? null,
      };
    }

    return {
      ...source,
      n: source.n ?? index + 1,
    };
  });
  const seen = new Set<string>();
  return normalized.filter((source) => {
    const key = `${source.document_id || source.document}:${source.page ?? 'unknown'}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map((source, index) => ({ ...source, n: index + 1 }));
}

/* ---------------- Documents ---------------- */

export async function uploadDocument(
  file: File,
  chatId: ID | null
): Promise<UploadResponse> {
  const form = new FormData();

  form.append('files', file);

  if (chatId !== null) {
    form.append('conversation_id', String(chatId));
  }

  const { data } = await http.post<UploadResponse>(
    '/api/documents/upload',
    form,
    {
      timeout: 20 * 60 * 1000,
    }
  );

  return data;
}

export async function deleteDocument(id: ID): Promise<void> {
  await http.delete(`/api/documents/${id}`);
}

export async function fetchDocuments(
  chatId: ID
): Promise<DocInfo[]> {
  const { data } = await http.get<{
    documents: UploadedDocument[];
    total: number;
  }>(`/api/conversations/${chatId}/documents`);

  return (data.documents ?? []).map((document) => ({
    id: document.id,
    filename: document.filename,
    pages: document.total_pages ?? 0,
  }));
}

export async function fetchAllDocuments(): Promise<UploadedDocument[]> {
  const { data } = await http.get<{ documents: UploadedDocument[]; total: number }>('/api/documents/');
  return data.documents ?? [];
}

/* ---------------- Chat ---------------- */

export async function chatOnce(
  payload: ChatRequest,
  signal?: AbortSignal
): Promise<ChatResponse> {
  const { data } = await http.post<ChatResponse>(
    '/api/chat/',
    {
      ...payload,
      stream: false,
    },
    {
      timeout: 20 * 60 * 1000,
      signal,
    }
  );

  return data;
}

/* ---------------- Streaming chat ---------------- */

interface StreamHandlers {
  signal: AbortSignal;
  onToken: (text: string) => void;
  onConversationId: (id: ID) => void;
  onSources?: (sources: SourceReference[]) => void;
}

interface StreamEvent {
  type: 'token' | 'sources' | 'done' | 'error' | string;
  content?: string;
  sources?: SourceReference[];
  conversation_id?: string;
  message?: string;
}

export async function streamChat(
  payload: ChatRequest,
  handlers: StreamHandlers
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/chat/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(getToken()
        ? { Authorization: `Bearer ${getToken()}` }
        : {}),
    },
    body: JSON.stringify({
      ...payload,
      stream: true,
    }),
    signal: handlers.signal,
  });

  if (response.status === 404 || response.status === 405) {
    throw new StreamUnavailableError();
  }

  if (!response.ok || !response.body) {
    let message = `Server error: ${response.status}`;

    try {
      const data = await response.json();
      message = data.detail || data.message || message;
    } catch {
      // Keep the default message.
    }

    throw new Error(message);
  }

  const headerConversationId = response.headers.get(
    'X-Conversation-Id'
  );

  if (headerConversationId) {
    handlers.onConversationId(headerConversationId);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = '';

  const processEvent = (rawEvent: string): void => {
    const lines = rawEvent.split(/\r?\n/);

    const dataLines = lines
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim());

    if (dataLines.length === 0) return;

    const data = dataLines.join('\n');

    if (!data || data === '[DONE]') return;

    let event: StreamEvent;

    try {
      event = JSON.parse(data) as StreamEvent;
    } catch {
      return;
    }

    switch (event.type) {
      case 'token':
        if (event.content) {
          handlers.onToken(event.content);
        }
        break;

      case 'sources':
        handlers.onSources?.(event.sources ?? []);
        break;

      case 'done':
        if (event.conversation_id) {
          handlers.onConversationId(event.conversation_id);
        }
        break;

      case 'error':
        throw new Error(event.message || 'Streaming failed');
    }
  };

  while (true) {
    const { done, value } = await reader.read();

    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.search(/\r?\n\r?\n/);

    while (boundary !== -1) {
      const eventText = buffer.slice(0, boundary);

      const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0];

      buffer = buffer.slice(boundary + (separator?.length ?? 2));

      processEvent(eventText);

      boundary = buffer.search(/\r?\n\r?\n/);
    }
  }

  buffer += decoder.decode();

  if (buffer.trim()) {
    processEvent(buffer);
  }
}

/* ---------------- Conversations / History ---------------- */

export async function fetchConversations(): Promise<
  ConversationSummary[]
> {
  const { data } = await http.get<{
    conversations: ConversationSummary[];
    total: number;
  }>('/api/conversations');

  return data.conversations ?? [];
}

export async function fetchConversationMessages(
  id: ID
): Promise<Message[]> {
  const { data } = await http.get<{
    messages: Array<{
      id: string;
      conversation_id: string;
      role: 'user' | 'assistant' | 'system';
      content: string;
      sources?: SourceReference[];
      created_at?: string | null;
    }>;
    total: number;
  }>(`/api/conversations/${id}/messages`);

  return (data.messages ?? []).map((message, index) => ({
    id: message.id || `${id}-${index}`,
    role: message.role,
    content: message.content,
    timestamp: message.created_at
      ? new Date(message.created_at).getTime()
      : Date.now(),
    sources: normalizeSources(message.sources ?? []),
  }));
}

export async function renameConversation(
  id: ID,
  title: string
): Promise<void> {
  await http.patch(`/api/conversations/${id}`, { title });
}

export async function deleteConversation(id: ID): Promise<void> {
  await http.delete(`/api/conversations/${id}`);
}

export async function clearConversation(id: ID): Promise<void> {
  await http.delete(`/api/conversations/${id}/messages`);
}

/* ---------------- AI Tools ---------------- */

export async function runTool(
  documentIds: ID[],
  tool: ToolName
): Promise<ToolResult> {
  const body = tool === 'extract'
    ? { document_ids: documentIds, query: 'Extract the key names, dates, amounts, deadlines, and important details.' }
    : { document_ids: documentIds };
  const { data } = await http.post<ToolResult>(`/api/tools/${tool}`, body, { timeout: 5 * 60 * 1000 });

  return data;
}

export async function fetchSuggestions(
  chatId: ID,
  selectedDocumentIds?: ID[],
): Promise<string[]> {
  try {
    const { data: docsData } = await http.get<{ documents: UploadedDocument[] }>(`/api/conversations/${chatId}/documents`);
    const documentIds = selectedDocumentIds ?? (docsData.documents ?? []).map((document) => document.id);
    if (!documentIds.length) return [];
    const { data } = await http.post<{ questions?: string[] }>('/api/tools/suggestions', {
      conversation_id: chatId,
      document_ids: documentIds,
    }, { timeout: 90000 });

    return data.questions ?? [];
  } catch {
    return [];
  }
}

/* ---------------- Export ---------------- */

export async function downloadExport(
  format: ExportFormat,
  conversationId: ID
): Promise<void> {
  const { data, headers } = await http.post<Blob>(`/api/export/${format}`, {
    conversation_id: conversationId,
    format,
    include_sources: true,
  }, { responseType: 'blob' });
  const url = URL.createObjectURL(data);
  const link = document.createElement('a');
  link.href = url;
  const disposition = String(headers['content-disposition'] ?? '');
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const regularName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  let filename = encodedName ? decodeURIComponent(encodedName) : regularName;
  if (!filename) filename = `conversation.${format}`;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
