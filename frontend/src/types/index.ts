
export type ID = string;

export type Role = 'user' | 'assistant' | 'system';

// Exact source structure returned by the backend
export interface SourceReference {
  document_id: ID;
  filename: string;
  page_number: number | null;
  chunk_id: ID | null;
  relevance_score: number | null;
  excerpt: string | null;
}

// Normalized source structure used by the frontend UI
export interface Source {
  n?: number;
  document_id: ID;
  document: string;
  page: number | null;
  snippet: string;
  chunk_id?: ID | null;
  relevance_score?: number | null;
}

export interface Message {
  id: ID;
  role: Role;
  content: string;
  timestamp: number | string;
  error?: boolean;
  streaming?: boolean;
  sources?: Source[];
}

export interface ChatRequest {
  message: string;
  conversation_id: ID | null;
  document_ids: ID[];
  provider?: 'gemini' | 'openai' | null;
  model?: string | null;
  temperature?: number;
  stream: boolean;
}

export interface ChatResponse {
  success?: boolean;
  conversation_id: ID;
  message: string;
  sources: SourceReference[];
  created_at?: string | null;
}

export interface UploadedDocument {
  id: ID;
  conversation_id?: ID | null;
  filename: string;
  file_type: string;
  file_size: number;
  status: string;
  total_pages: number;
  total_chunks: number;
  created_at?: string | null;
}

export interface UploadError {
  filename: string;
  error: string;
}

export interface UploadResponse {
  success: boolean;
  message: string;
  documents: UploadedDocument[];
  errors: UploadError[];
  total_uploaded: number;
  total_failed: number;
}

export interface DocInfo {
  id: ID;
  filename: string;
  pages: number | null;
}

export interface ConversationSummary {
  id: ID;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
  document_count?: number;
  message_count?: number;
}

export interface ConversationMessage {
  id: ID;
  conversation_id: ID;
  role: Role;
  content: string;
  sources: SourceReference[];
  created_at?: string | null;
}

export interface ConversationMessagesResponse {
  messages: ConversationMessage[];
  total: number;
}

export interface ToolResult {
  success: boolean;
  message?: string;
  result?: unknown;
  [key: string]: unknown;
}

export type ToolName = 'summary' | 'extract' | 'compare';

export type ExportFormat = 'pdf' | 'docx' | 'txt' | 'md';

export interface User {
  id: ID;
  name: string;
  email: string;
}
