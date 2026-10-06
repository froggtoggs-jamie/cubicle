'use client';

import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { FiX, FiChevronRight } from 'react-icons/fi';

function formatMsgTime(createdAt) {
  if (!createdAt) return '';
  const d = new Date(createdAt);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
}

// Collapsible "Thinking" block shown above an assistant reply. While the model
// is still thinking (no answer text yet) it stays open and follows the stream;
// once the answer starts it collapses unless the user has opened it.
function ThinkingBlock({ reasoning, answerStarted, isStreaming }) {
  const [userToggled, setUserToggled] = useState(null);
  const bodyRef = useRef(null);

  const liveThinking = isStreaming && !answerStarted;
  const open = userToggled !== null ? userToggled : liveThinking;

  useEffect(() => {
    if (open && liveThinking && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [reasoning, open, liveThinking]);

  const words = reasoning.trim() ? reasoning.trim().split(/\s+/).length : 0;
  const label = liveThinking ? 'Thinking…' : 'Thinking';

  return (
    <div className="mb-2.5 rounded-xl border border-[#2b2b32] bg-[#141416]/70 overflow-hidden">
      <button
        suppressHydrationWarning={true}
        type="button"
        onClick={() => setUserToggled(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] text-zinc-400 hover:text-zinc-200 hover:bg-[#1a1a1e] transition text-left"
      >
        <FiChevronRight className={`text-xs transition-transform duration-200 ${open ? 'rotate-90' : ''}`} />
        <span className="font-semibold tracking-wide">{label}</span>
        {liveThinking && <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse" />}
        <span className="ml-auto font-mono text-[10px] text-zinc-500">{words} words</span>
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="px-3.5 pb-3 pt-1 max-h-64 overflow-y-auto text-[11px] leading-relaxed text-zinc-400 whitespace-pre-wrap break-words font-sans border-t border-[#1f1f24]"
          style={{ scrollbarWidth: 'thin', scrollbarColor: '#27272a transparent' }}
        >
          {reasoning}
        </div>
      )}
    </div>
  );
}

export default function MessageItem({ message }) {
  const isUser = message.sender === 'user';
  const isError = message.isError || message.text?.toLowerCase().startsWith('error:');
  const formattedTime = formatMsgTime(message.created_at);
  // Live streams carry `reasoning`; persisted history carries it in raw_payload.
  const reasoning = message.reasoning || message.raw_payload?.reasoning || '';

  if (isUser) {
    return (
      <div className="flex justify-end my-1.5">
        <div className="dark-bubble-user px-3.5 py-2 text-xs font-sans max-w-md shadow-md">
          {message.image_url && (
            <img
              src={message.image_url}
              alt="Uploaded image attachment"
              className="max-w-full max-h-56 rounded-lg object-cover border border-white/10 mb-1.5"
            />
          )}
          <div className="flex justify-end gap-3">
            <span className="break-words">{message.text}</span>
            {formattedTime && (
              <span className="text-[10px] text-zinc-300/70 font-mono tracking-tight select-none flex-shrink-0 self-end ml-auto">
                {formattedTime}
              </span>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex justify-start my-2">
        <div className="dark-bubble-error px-4 py-1.5 text-xs font-mono max-w-md flex items-center justify-between gap-3 shadow-sm">
          <div className="flex items-center gap-2 min-w-0">
            <FiX className="text-red-500 text-sm flex-shrink-0" />
            <span className="truncate">{message.text}</span>
          </div>
          {formattedTime && (
            <span className="text-[10px] text-zinc-500 font-mono flex-shrink-0">{formattedTime}</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start my-2">
      <div className="dark-bubble-bot px-5 py-3 text-xs font-sans max-w-2xl w-full shadow-md text-zinc-100 leading-relaxed overflow-hidden">
        {reasoning && (
          <ThinkingBlock
            reasoning={reasoning}
            answerStarted={Boolean(message.text)}
            isStreaming={Boolean(message.isStreaming)}
          />
        )}
        <ReactMarkdown
          components={{
            p: ({ node, ...props }) => <div className="mb-2 last:mb-0 leading-relaxed" {...props} />,
            strong: ({ node, ...props }) => <strong className="font-bold text-white" {...props} />,
            code: ({ node, inline, className, children, ...props }) => {
              const isInline = inline || (!className && typeof children === 'string' && !children.includes('\n'));
              if (isInline) {
                return (
                  <code className="bg-[#2a2a30] text-cyan-300 px-1.5 py-0.5 rounded font-mono text-[11px]" {...props}>
                    {children}
                  </code>
                );
              }
              return (
                <pre className="bg-[#141416] p-3 rounded-xl border border-[#2b2b32] text-zinc-300 font-mono text-[11px] overflow-x-auto my-2">
                  <code {...props}>{children}</code>
                </pre>
              );
            },
            ul: ({ node, ...props }) => <ul className="list-disc list-inside space-y-1 my-1 text-zinc-300" {...props} />,
            ol: ({ node, ...props }) => <ol className="list-decimal list-inside space-y-1 my-1 text-zinc-300" {...props} />,
          }}
        >
          {message.text}
        </ReactMarkdown>
        {formattedTime && (
          <div className="text-[10px] text-zinc-400 text-right mt-1 font-mono tracking-tight select-none">
            {formattedTime}
          </div>
        )}
      </div>
    </div>
  );
}
