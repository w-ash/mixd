import { Check, Copy, Loader2 } from "lucide-react";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";

import { cn } from "#/lib/utils";
import type { ChatMessage as ChatMessageType } from "#/stores/chat-store";

import { CodeExecutionCard } from "./CodeExecutionCard";
import { ToolCallIndicator } from "./ToolCallIndicator";
import { ToolResultCard } from "./ToolResultCard";

const markdownComponents = {
  p: ({ children, ...props }: React.HTMLAttributes<HTMLParagraphElement>) => (
    <p className="mb-2 font-body text-sm leading-relaxed last:mb-0" {...props}>
      {children}
    </p>
  ),
  h1: ({ children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h1
      className="mb-2 mt-4 font-display text-base font-medium first:mt-0"
      {...props}
    >
      {children}
    </h1>
  ),
  h2: ({ children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h2
      className="mb-2 mt-3 font-display text-sm font-medium first:mt-0"
      {...props}
    >
      {children}
    </h2>
  ),
  h3: ({ children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h3
      className="mb-1.5 mt-2.5 font-display text-sm font-medium first:mt-0"
      {...props}
    >
      {children}
    </h3>
  ),
  ul: ({ children, ...props }: React.HTMLAttributes<HTMLUListElement>) => (
    <ul className="mb-2 list-disc pl-5 font-body last:mb-0" {...props}>
      {children}
    </ul>
  ),
  ol: ({ children, ...props }: React.OlHTMLAttributes<HTMLOListElement>) => (
    <ol className="mb-2 list-decimal pl-5 font-body last:mb-0" {...props}>
      {children}
    </ol>
  ),
  li: ({ children, ...props }: React.LiHTMLAttributes<HTMLLIElement>) => (
    <li className="mb-0.5 text-sm leading-relaxed" {...props}>
      {children}
    </li>
  ),
  blockquote: ({
    children,
    ...props
  }: React.BlockquoteHTMLAttributes<HTMLQuoteElement>) => (
    <blockquote
      className="mb-2 border-l-2 border-primary/40 pl-3 font-body italic last:mb-0"
      {...props}
    >
      {children}
    </blockquote>
  ),
  pre: ({ children, ...props }: React.HTMLAttributes<HTMLPreElement>) => (
    <pre
      className="mb-2 overflow-x-auto rounded-lg bg-surface-sunken p-3 font-mono text-xs leading-normal last:mb-0"
      {...props}
    >
      {children}
    </pre>
  ),
  code: ({
    children,
    className,
    ...props
  }: React.HTMLAttributes<HTMLElement>) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code
          className="rounded bg-surface-sunken px-1 py-0.5 font-mono text-[0.85em]"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  a: ({
    children,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      className="text-primary underline underline-offset-2 hover:text-primary-hover"
      target="_blank"
      rel="noreferrer noopener"
      {...props}
    >
      {children}
    </a>
  ),
  strong: ({ children, ...props }: React.HTMLAttributes<HTMLElement>) => (
    <strong className="font-medium" {...props}>
      {children}
    </strong>
  ),
  table: ({
    children,
    ...props
  }: React.TableHTMLAttributes<HTMLTableElement>) => (
    <table className="mb-2 w-full text-sm last:mb-0" {...props}>
      {children}
    </table>
  ),
  tr: ({ children, ...props }: React.HTMLAttributes<HTMLTableRowElement>) => (
    <tr className="border-b border-border text-left" {...props}>
      {children}
    </tr>
  ),
  th: ({
    children,
    ...props
  }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
    <th
      className="py-1 pr-3 font-display text-xs font-medium text-text-muted"
      {...props}
    >
      {children}
    </th>
  ),
  td: ({
    children,
    ...props
  }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
    <td className="py-1 pr-3 tabular-nums" {...props}>
      {children}
    </td>
  ),
};

/** Local copy-to-clipboard feedback: flips to "copied" for 1.5s after a copy. */
function useCopyFeedback() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const markCopied = useCallback(() => {
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1500);
  }, []);

  return { copied, markCopied };
}

function CopyButton({ content }: { content: string }) {
  const { copied, markCopied } = useCopyFeedback();

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(content);
    markCopied();
  }, [content, markCopied]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? "Copied" : "Copy"}
      aria-label={copied ? "Copied" : "Copy message"}
      className={cn(
        "flex size-7 items-center justify-center rounded-md text-text-muted transition-opacity hover:text-text",
        "opacity-0 focus-visible:opacity-100 group-hover:opacity-100 max-md:size-11 max-md:opacity-100",
      )}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </button>
  );
}

// Memoized: the store's updateMessage preserves the identity of untouched
// messages, so streaming one message doesn't re-render the whole list.
export const ChatMessage = memo(function ChatMessage({
  message,
  onConfirm,
  onCancel,
  onSendMessage,
}: {
  message: ChatMessageType;
  onConfirm?: (actionId: string) => void;
  onCancel?: (actionId: string) => void;
  onSendMessage?: (text: string) => void;
}) {
  const isUser = message.role === "user";
  const content = message.content;
  const showCopy =
    !isUser && !message.isStreaming && !message.error && !!message.content;

  return (
    <div
      className={cn(
        "group flex flex-col gap-1",
        isUser ? "items-end" : "items-start",
      )}
    >
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm text-text",
          isUser ? "bg-primary-muted" : "bg-surface-elevated",
        )}
      >
        {message.isStreaming && !message.content && (
          <output aria-label="Thinking">
            <Loader2 className="size-4 animate-spin text-text-muted" />
          </output>
        )}
        {content &&
          (isUser ? (
            <p className="whitespace-pre-wrap font-body">{content}</p>
          ) : (
            <Streamdown
              isAnimating={message.isStreaming}
              animated={false}
              components={markdownComponents}
            >
              {content}
            </Streamdown>
          ))}
        {message.error && (
          <p className="font-body text-destructive">{message.error.message}</p>
        )}
      </div>
      {message.codeExecutions && message.codeExecutions.length > 0 && (
        <div className="flex w-full max-w-[85%] flex-col gap-2 px-1">
          {message.codeExecutions.map((ce) => (
            <CodeExecutionCard key={ce.id} execution={ce} />
          ))}
        </div>
      )}
      {message.toolCalls && message.toolCalls.length > 0 && (
        <div className="flex max-w-[85%] flex-col gap-2 px-1">
          <div className="flex flex-wrap gap-1.5">
            {message.toolCalls.map((tc) => (
              <ToolCallIndicator key={tc.id} toolCall={tc} />
            ))}
          </div>
          {message.toolCalls.map((tc) => (
            <ToolResultCard
              key={`result-${tc.id}`}
              toolCall={tc}
              messageId={message.id}
              siblingToolCalls={message.toolCalls}
              onConfirm={onConfirm}
              onCancel={onCancel}
              onSendMessage={onSendMessage}
            />
          ))}
        </div>
      )}
      {showCopy && <CopyButton content={content} />}
    </div>
  );
});
