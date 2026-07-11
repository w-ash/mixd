import { Button } from "#/components/ui/button";

const DEFAULT_SUGGESTIONS = [
  "Build me a Friday-night dinner playlist",
  "Find tracks I loved last year but haven't played recently",
  "Make an upbeat workout mix from my starred tracks",
];

export function SuggestedQuestions({
  onSelect,
}: {
  onSelect: (question: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 text-center animate-fade-up">
      <p className="max-w-xs font-body text-sm text-text-muted">
        Ask the assistant to shape a playlist from your listening history.
      </p>
      <div className="flex flex-col items-stretch gap-2">
        {DEFAULT_SUGGESTIONS.map((q) => (
          <Button
            key={q}
            type="button"
            variant="outline"
            size="sm"
            className="h-auto whitespace-normal rounded-full px-4 py-2 text-left font-normal"
            onClick={() => onSelect(q)}
          >
            {q}
          </Button>
        ))}
      </div>
    </div>
  );
}
