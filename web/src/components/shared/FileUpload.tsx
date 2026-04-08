import { Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";

const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB

interface FileUploadProps {
  accept?: string;
  maxSize?: number;
  onFileSelect: (file: File) => void;
  disabled?: boolean;
}

export function FileUpload({
  accept = ".json",
  maxSize = MAX_FILE_SIZE,
  onFileSelect,
  disabled = false,
}: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const processFile = useCallback(
    (file: File) => {
      setError(null);

      if (file.size > maxSize) {
        setError(
          `File too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Maximum: ${(maxSize / 1024 / 1024).toFixed(0)}MB.`,
        );
        setSelectedFile(null);
        return;
      }

      setSelectedFile(file);
      onFileSelect(file);
    },
    [maxSize, onFileSelect],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) processFile(file);
    },
    [processFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) processFile(file);
    },
    [processFile],
  );

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: drop zone uses native drag events; keyboard path is the Choose file button inside
    <div
      className={cn(
        "rounded-lg border border-dashed p-4 text-center transition-colors",
        isDragging ? "border-primary/50 bg-primary/5" : "border-border-muted",
        disabled && "pointer-events-none opacity-50",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setIsDragging(false);
      }}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        disabled={disabled}
        className="hidden"
        aria-label="Upload file"
      />

      {selectedFile && !error ? (
        <div className="flex items-center justify-center gap-2">
          <p className="text-xs text-text-muted font-mono">
            {selectedFile.name}{" "}
            <span className="text-text-faint">
              ({(selectedFile.size / 1024 / 1024).toFixed(1)}MB)
            </span>
          </p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
          >
            Change
          </Button>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-1.5">
          <Upload className="size-4 text-text-faint" />
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => inputRef.current?.click()}
            >
              Choose file
            </Button>
            <p className="mt-1.5 text-xs text-text-faint">or drag and drop</p>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
