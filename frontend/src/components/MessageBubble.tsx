import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message } from '../types';
import { BotIcon, CopyIcon, UserIcon } from './Icons';
import SourceList from './SourceList';

function MessageBubble({ msg }: { msg: Message; title: string }) {
  const [copied, setCopied] = useState(false);
  const waiting = msg.streaming && !msg.content;
  const showActions = msg.role === 'assistant' && !msg.streaming && !msg.error && msg.content.length > 0;

  const copy = async () => {
    try {
      const sources = (msg.sources ?? []).map((source) => {
        const marker = source.n == null ? '' : `[${source.n}] `;
        const page = source.page != null && source.page > 0 ? ` (p. ${source.page})` : '';
        return `${marker}${source.document}${page}`;
      });
      const answer = sources.length ? `${msg.content}\n\nSources:\n${sources.join('\n')}` : msg.content;
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className={`msg ${msg.role}`}>
      <div className="avatar-icon">{msg.role === 'user' ? <UserIcon /> : <BotIcon />}</div>
      <div className={`bubble ${msg.error ? 'error' : ''}`}>
        {waiting ? (
          <div className="typing"><span /><span /><span /></div>
        ) : msg.role === 'assistant' ? (
          <div className={`md ${msg.streaming ? 'caret' : ''}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        ) : (
          <div className="plain">{msg.content}</div>
        )}
        {!msg.streaming && msg.sources && msg.sources.length > 0 && (
          <SourceList sources={msg.sources} content={msg.content} />
        )}
        {showActions && (
          <div className="msg-actions"><button className="chip-btn" onClick={copy} aria-label="Copy response with citations"><CopyIcon /> {copied ? 'Copied' : 'Copy response'}</button></div>
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
