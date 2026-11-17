
import {
  useCallback,
  useRef,
  useState,
} from 'react';

import type {
  Dispatch,
  SetStateAction,
} from 'react';

import {
  StreamUnavailableError,
  chatOnce,
  clearConversation,
  deleteConversation,
  deleteDocument,
  fetchConversationMessages,
  fetchConversations,
  fetchDocuments,
  fetchSuggestions,
  getErrorMessage,
  isAbortError,
  normalizeSources,
  renameConversation,
  runTool as runToolApi,
  streamChat,
  uploadDocument,
} from '../services/api';

import type {
  ConversationSummary,
  DocInfo,
  ID,
  Message,
  Source,
  SourceReference,
  ToolName,
  UploadError,
} from '../types';

/* ---------------- Helpers ---------------- */

const uid = (): string => {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

const ALLOWED_EXTENSIONS = [
  '.pdf',
  '.docx',
  '.txt',
];

const TOOL_LABELS: Record<ToolName, string> = {
  summary: 'Summarize the documents and give key insights',
  extract: 'Extract names, dates, amounts and deadlines',
  compare: 'Compare the documents',
};

function normalizeBackendSources(
  sources: SourceReference[]
): Source[] {
  return normalizeSources(sources);
}

/* ---------------- Stream writer ---------------- */

function createWriter(
  botId: string,
  setMessages: Dispatch<SetStateAction<Message[]>>
) {
  let buffer = '';
  let frame = 0;

  const flush = () => {
    frame = 0;

    if (!buffer) return;

    const chunk = buffer;
    buffer = '';

    setMessages((previous) =>
      previous.map((message) =>
        message.id === botId
          ? {
              ...message,
              content: message.content + chunk,
            }
          : message
      )
    );
  };

  return {
    push(text: string) {
      buffer += text;

      if (!frame) {
        frame = requestAnimationFrame(flush);
      }
    },

    done() {
      if (frame) {
        cancelAnimationFrame(frame);
      }

      flush();
    },
  };
}

/* ---------------- Hook ---------------- */

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] =
    useState<ID | null>(null);

  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<ID | null>(null);
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);

  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const activeRequestRef = useRef<string | null>(null);
  const conversationLoadToken = useRef(0);
  const historyRequestToken = useRef(0);
  const suggestToken = useRef(0);

  /* ---------------- Message helper ---------------- */

  const addMessage = useCallback(
    (
      role: Message['role'],
      content: string,
      error = false
    ) => {
      setMessages((previous) => [
        ...previous,
        {
          id: uid(),
          role,
          content,
          timestamp: Date.now(),
          error,
        },
      ]);
    },
    []
  );

  /* ---------------- History ---------------- */

  const refreshHistory = useCallback(async () => {
    const requestToken = ++historyRequestToken.current;
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const conversations = await fetchConversations();
      if (requestToken !== historyRequestToken.current) return;
      setHistory(conversations);
    } catch (error) {
      if (requestToken !== historyRequestToken.current) return;
      setHistoryError(getErrorMessage(error));
    } finally {
      if (requestToken === historyRequestToken.current) setHistoryLoading(false);
    }
  }, []);

  /* ---------------- Suggestions ---------------- */

  const loadSuggestions = useCallback(async (chatId: ID, documentIds?: ID[]) => {
    const token = ++suggestToken.current;

    setSuggestions([]);

    try {
      const questions = await fetchSuggestions(chatId, documentIds);

      if (token === suggestToken.current) {
        setSuggestions(questions);
      }
    } catch (error) {
      console.error('Could not load suggestions:', error);
    }
  }, []);

  /* ---------------- Upload documents ---------------- */

  const upload = useCallback(
    async (files: File[]) => {
      const validFiles: File[] = [];
      const outcome: { uploaded: number; errors: UploadError[] } = { uploaded: 0, errors: [] };
      const uploadScope = conversationLoadToken.current;
      let lastUploadedId: ID | null = null;

      for (const file of files) {
        const extension =
          '.' + (file.name.split('.').pop() ?? '').toLowerCase();

        if (ALLOWED_EXTENSIONS.includes(extension)) {
          validFiles.push(file);
        } else {
          outcome.errors.push({ filename: file.name, error: 'Unsupported file type. Upload a PDF, DOCX, or TXT file.' });
        }
      }

      if (validFiles.length === 0) return outcome;

      setUploading(true);

      let currentConversationId = conversationId;
      try {
        for (const file of validFiles) {
          try {
            const response = await uploadDocument(
              file,
              currentConversationId
            );

            const uploadedDocuments = response.documents ?? [];

            outcome.errors.push(...(response.errors ?? []));
            if (uploadedDocuments.length === 0 && !response.errors?.length) {
              outcome.errors.push({ filename: file.name, error: response.message || 'The server did not return an uploaded document.' });
            }
            outcome.uploaded += uploadedDocuments.length;

            for (const document of uploadedDocuments) {
              lastUploadedId = document.id;
              if (uploadScope !== conversationLoadToken.current) continue;

              if (document.conversation_id) {
                currentConversationId = document.conversation_id;
                setConversationId(document.conversation_id);
              }

              setDocs((previous) => {
                const alreadyExists = previous.some(
                  (item) => item.id === document.id
                );

                if (alreadyExists) return previous;

                return [
                  ...previous,
                  {
                    id: document.id,
                    filename: document.filename,
                    pages: document.total_pages ?? 0,
                  },
                ];
              });
              setSelectedDocumentId(document.id);

            }
          } catch (error) {
            outcome.errors.push({ filename: file.name, error: getErrorMessage(error) });
          }
        }

        if (outcome.uploaded > 0) {
          if (currentConversationId && uploadScope === conversationLoadToken.current) {
            void loadSuggestions(currentConversationId, lastUploadedId ? [lastUploadedId] : undefined);
          }

          if (currentConversationId) void refreshHistory();
        }
      } finally {
        setUploading(false);
      }
      return outcome;
    },
    [
      conversationId,
      loadSuggestions,
      refreshHistory,
    ]
  );

  /* ---------------- Remove document ---------------- */

  const removeDoc = useCallback(
    async (document: DocInfo) => {
      try {
        await deleteDocument(document.id);

        const remainingDocuments = docs.filter((item) => item.id !== document.id);
        const nextSelectedId = selectedDocumentId === document.id
          ? remainingDocuments[0]?.id ?? null
          : selectedDocumentId;
        setDocs(remainingDocuments);
        setSelectedDocumentId(nextSelectedId);

        addMessage(
          'assistant',
          `🗑️ Removed "${document.filename}".`
        );

        if (conversationId) {
          if (nextSelectedId) {
            void loadSuggestions(conversationId, [nextSelectedId]);
          } else {
            setSuggestions([]);
          }
        }

        void refreshHistory();
      } catch (error) {
        addMessage(
          'assistant',
          `❌ Could not remove document: ${getErrorMessage(error)}`,
          true
        );
      }
    },
    [
      addMessage,
      conversationId,
      docs.length,
      docs,
      loadSuggestions,
      selectedDocumentId,
      refreshHistory,
    ]
  );

  const forgetDoc = useCallback((documentId: ID) => {
    setDocs((previous) => {
      const remaining = previous.filter((document) => document.id !== documentId);
      setSelectedDocumentId((selected) => selected === documentId ? remaining[0]?.id ?? null : selected);
      return remaining;
    });
    setSuggestions([]);
  }, []);

  /* ---------------- Bot error handling ---------------- */

  const failBot = useCallback(
    (botId: string, error: unknown) => {
      if (isAbortError(error)) return;

      const errorMessage = `❌ Error: ${getErrorMessage(error)}`;

      setMessages((previous) =>
        previous.map((message) => {
          if (message.id !== botId) return message;

          return {
            ...message,
            content: message.content
              ? `${message.content}\n\n${errorMessage}`
              : errorMessage,
            error: true,
          };
        })
      );
    },
    []
  );

  const endBot = useCallback((botId: string) => {
    setMessages((previous) =>
      previous.map((message) =>
        message.id === botId
          ? { ...message, streaming: false }
          : message
      )
    );

    if (activeRequestRef.current === botId) {
      activeRequestRef.current = null;
      setStreaming(false);
      abortRef.current = null;
    }
  }, []);

  /* ---------------- Send message ---------------- */

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();

      if (!content || streaming || uploading || activeRequestRef.current) return;

      const botId = uid();
      activeRequestRef.current = botId;

      setMessages((previous) => [
        ...previous,
        {
          id: uid(),
          role: 'user',
          content,
          timestamp: Date.now(),
        },
        {
          id: botId,
          role: 'assistant',
          content: '',
          timestamp: Date.now(),
          streaming: true,
        },
      ]);

      setStreaming(true);
      setSuggestions([]);

      const controller = new AbortController();
      abortRef.current = controller;

      const payload = {
        message: content,
        conversation_id: conversationId,
        document_ids: selectedDocumentId ? [selectedDocumentId] : [],
        stream: true,
      };

      const writer = createWriter(botId, setMessages);

      const setSources = (sources: SourceReference[]) => {
        const normalized = normalizeBackendSources(sources);

        setMessages((previous) =>
          previous.map((message) =>
            message.id === botId
              ? { ...message, sources: normalized }
              : message
          )
        );
      };

      try {
        try {
          await streamChat(payload, {
            signal: controller.signal,

            onToken: writer.push,

            onConversationId: (id: ID) => {
              if (activeRequestRef.current === botId) setConversationId(id);
            },

            onSources: setSources,
          });
        } catch (error) {
          if (!(error instanceof StreamUnavailableError)) {
            throw error;
          }

          const response = await chatOnce(
            {
              ...payload,
              stream: false,
            },
            controller.signal
          );

          if (response.conversation_id) {
            setConversationId(response.conversation_id);
          }

          if (response.message) {
            writer.push(response.message);
          }

          setSources(response.sources ?? []);
        }
      } catch (error) {
        failBot(botId, error);
      } finally {
        writer.done();
        endBot(botId);
        void refreshHistory();
      }
    },
    [
      conversationId,
      docs,
      selectedDocumentId,
      endBot,
      failBot,
      refreshHistory,
      streaming,
      uploading,
    ]
  );

  /* ---------------- AI tools ---------------- */

  const runTool = useCallback(
    async (tool: ToolName) => {
      if (streaming || uploading || !conversationId) return;

      if (docs.length === 0) {
        addMessage(
          'assistant',
          'Please upload at least one document first.',
          true
        );
        return;
      }

      if (tool === 'compare' && docs.length < 2) {
        addMessage(
          'assistant',
          'Add at least two documents to compare them.',
          true
        );
        return;
      }

      const botId = uid();

      setMessages((previous) => [
        ...previous,
        {
          id: uid(),
          role: 'user',
          content: TOOL_LABELS[tool],
          timestamp: Date.now(),
        },
        {
          id: botId,
          role: 'assistant',
          content: '',
          timestamp: Date.now(),
          streaming: true,
        },
      ]);

      setStreaming(true);

      const writer = createWriter(botId, setMessages);

      try {
        const result = await runToolApi(docs.map((document) => document.id), tool);

        const resultValue = result.result ?? result.summary ?? result.comparison ?? result.extracted;
        const resultText = typeof resultValue === 'string'
          ? resultValue
          : result.message ?? JSON.stringify(resultValue ?? result, null, 2);

        if (result.success === false) {
          throw new Error(result.message || 'The tool request failed.');
        }

        writer.push(resultText || 'The tool completed without returning text.');
      } catch (error) {
        failBot(botId, error);
      } finally {
        writer.done();
        endBot(botId);
        void refreshHistory();
      }
    },
    [
      addMessage,
      conversationId,
      docs.length,
      endBot,
      failBot,
      refreshHistory,
      streaming,
      uploading,
    ]
  );

  /* ---------------- Stop generation ---------------- */

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  /* ---------------- New chat ---------------- */

  const newChat = useCallback(() => {
    conversationLoadToken.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    activeRequestRef.current = null;

    suggestToken.current += 1;

    setMessages([]);
    setConversationId(null);
    setDocs([]);
    setSelectedDocumentId(null);
    setSuggestions([]);
    setStreaming(false);
  }, []);

  const resetWorkspace = useCallback(() => {
    conversationLoadToken.current += 1;
    historyRequestToken.current += 1;
    suggestToken.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    activeRequestRef.current = null;
    setMessages([]);
    setConversationId(null);
    setDocs([]);
    setHistory([]);
    setHistoryError('');
    setSelectedDocumentId(null);
    setHistoryLoading(false);
    setSuggestions([]);
    setStreaming(false);
    setUploading(false);
  }, []);

  const startDocumentChat = useCallback((document: DocInfo) => {
    conversationLoadToken.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    activeRequestRef.current = null;
    setMessages([]);
    setConversationId(null);
    setDocs([document]);
    setSelectedDocumentId(document.id);
    setSuggestions([]);
    setStreaming(false);
  }, []);

  const selectDocument = useCallback((document: DocInfo) => {
    setDocs((previous) => previous.some((item) => item.id === document.id) ? previous : [...previous, document]);
    setSelectedDocumentId(document.id);
    if (conversationId) void loadSuggestions(conversationId, [document.id]);
    else setSuggestions([]);
  }, [conversationId, loadSuggestions]);

  /* ---------------- Clear chat ---------------- */

  const clearChat = useCallback(async () => {
    abortRef.current?.abort();

    try {
      if (conversationId) {
        await clearConversation(conversationId);
      }

      setMessages([]);
      setSuggestions([]);

      void refreshHistory();
    } catch (error) {
      addMessage(
        'assistant',
        `❌ Could not clear chat: ${getErrorMessage(error)}`,
        true
      );
    }
  }, [addMessage, conversationId, refreshHistory]);

  /* ---------------- Open conversation ---------------- */

  const openConversation = useCallback(
    async (id: ID) => {
      const loadToken = ++conversationLoadToken.current;
      abortRef.current?.abort();
      abortRef.current = null;
      activeRequestRef.current = null;

      suggestToken.current += 1;
      setMessages([]);
      setDocs([]);
      setSelectedDocumentId(null);
      setConversationId(id);
      setSuggestions([]);
      setStreaming(false);

      try {
        const [conversationMessages, documents] = await Promise.all([
          fetchConversationMessages(id),
          fetchDocuments(id),
        ]);

        if (loadToken !== conversationLoadToken.current) return;

        setMessages(conversationMessages);
        setDocs(documents);
        setSelectedDocumentId(documents[0]?.id ?? null);
        setConversationId(id);
        setSuggestions([]);

        if (documents.length > 0) {
          void loadSuggestions(id, [documents[0].id]);
        }
      } catch (error) {
        if (loadToken !== conversationLoadToken.current) return;
        addMessage(
          'assistant',
          `❌ Could not load conversation: ${getErrorMessage(error)}`,
          true
        );
      }
    },
    [addMessage, loadSuggestions]
  );

  /* ---------------- Rename conversation ---------------- */

  const renameChat = useCallback(
    async (id: ID, title: string) => {
      const trimmedTitle = title.trim();

      if (!trimmedTitle) return;

      try {
        await renameConversation(id, trimmedTitle);
        await refreshHistory();
      } catch (error) {
        addMessage(
          'assistant',
          `❌ Could not rename conversation: ${getErrorMessage(error)}`,
          true
        );
      }
    },
    [addMessage, refreshHistory]
  );

  /* ---------------- Delete conversation ---------------- */

  const deleteChat = useCallback(
    async (id: ID) => {
      try {
        await deleteConversation(id);

        if (id === conversationId) {
          newChat();
        }

        await refreshHistory();
      } catch (error) {
        addMessage(
          'assistant',
          `❌ Could not delete conversation: ${getErrorMessage(error)}`,
          true
        );
      }
    },
    [
      addMessage,
      conversationId,
      newChat,
      refreshHistory,
    ]
  );

  /* ---------------- Return API ---------------- */

  return {
    messages,
    docs,
    selectedDocumentId,
    history,
    historyError,
    historyLoading,
    suggestions,
    conversationId,
    streaming,
    uploading,

    send,
    stop,
    upload,
    removeDoc,
    forgetDoc,
    runTool,

    newChat,
    resetWorkspace,
    startDocumentChat,
    selectDocument,
    clearChat,
    openConversation,
    renameChat,
    deleteChat,
    refreshHistory,
  };
}

export type ChatApi = ReturnType<typeof useChat>;
