import { useEffect, useRef } from 'react';
import type { Message } from '../types';
import MessageBubble from './MessageBubble';

export default function ChatWindow({ messages, title }: { messages: Message[]; title: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // auto-scroll only if the user is already near the bottom

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div className="messages" ref={ref} onScroll={onScroll}>
      <div className="messages-inner">
        {messages.map((m) => (
          <MessageBubble key={m.id} msg={m} title={title} />
        ))}
      </div>
    </div>
  );
}
