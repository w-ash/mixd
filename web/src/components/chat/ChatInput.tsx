import { ArrowUp, Square } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";

export function ChatInput({
  onSubmit,
  isStreaming,
  onStop,
}: {
  onSubmit: (text: string) => void;
  isStreaming: boolean;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || isStreaming) return;
    onSubmit(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, isStreaming, onSubmit]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const resize = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  };

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <div className="px-3 pb-3 pt-2">
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border border-input bg-surface-elevated px-3 py-2",
          "transition-[border-color,box-shadow] duration-150",
          "focus-within:border-ring focus-within:ring-1 focus-within:ring-ring",
        )}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            resize();
          }}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your music…"
          disabled={isStreaming}
          rows={1}
          className={cn(
            "flex-1 resize-none self-center bg-transparent py-1.5 font-body text-sm leading-relaxed",
            "text-text placeholder:text-text-faint",
            "focus:outline-none disabled:cursor-not-allowed disabled:opacity-50",
          )}
        />
        {isStreaming ? (
          <Button
            variant="secondary"
            size="icon-sm"
            onClick={onStop}
            aria-label="Stop generating"
            title="Stop generating"
          >
            <Square className="size-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            variant="default"
            size="icon-sm"
            onClick={handleSubmit}
            disabled={!canSend}
            aria-label="Send message"
            title="Send"
          >
            <ArrowUp className="size-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
