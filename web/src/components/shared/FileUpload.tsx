import { Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";

// Client-side mirrors of the server caps (BusinessLimits.MAX_UPLOAD_BYTES /
// MAX_QUEUED_UPLOAD_BYTES / MAX_QUEUE_ENTRIES) — a friendlier rejection than
// uploading 500MB just to receive the server's 413.
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB per file
const MAX_TOTAL_SIZE = 500 * 1024 * 1024; // 500MB per selection
const MAX_FILES = 25;

interface FileUploadProps {
  accept?: string;
  maxSize?: number;
  maxTotalSize?: number;
  maxFiles?: number;
  onFilesSelect: (files: File[]) => void;
  disabled?: boolean;
}

function mb(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

export function FileUpload({
  accept = ".json",
  maxSize = MAX_FILE_SIZE,
  maxTotalSize = MAX_TOTAL_SIZE,
  maxFiles = MAX_FILES,
  onFilesSelect,
  disabled = false,
}: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  // Each file carries its selection position as the row key: the selection is
  // replaced atomically (never spliced), so the position is stable — and two
  // same-named files must not collide the way file.name keys did, which
  // rendered duplicates as one row.
  const [selectedFiles, setSelectedFiles] = useState<
    { position: number; file: File }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const processFiles = useCallback(
    (files: File[]) => {
      setError(null);
      const reject = (message: string) => {
        setError(message);
        setSelectedFiles([]);
        onFilesSelect([]);
      };

      if (files.length > maxFiles) {
        reject(`Too many files (${files.length}). Maximum: ${maxFiles}.`);
        return;
      }
      const oversized = files.find((file) => file.size > maxSize);
      if (oversized) {
        reject(
          `File too large: ${oversized.name} (${mb(oversized.size)}). Maximum: ${mb(maxSize)} per file.`,
        );
        return;
      }
      const totalSize = files.reduce((sum, file) => sum + file.size, 0);
      if (totalSize > maxTotalSize) {
        reject(
          `Selection too large (${mb(totalSize)}). Maximum: ${mb(maxTotalSize)} in total.`,
        );
        return;
      }

      setSelectedFiles(files.map((file, position) => ({ position, file })));
      onFilesSelect(files);
    },
    [maxFiles, maxSize, maxTotalSize, onFilesSelect],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (files.length > 0) processFiles(files);
    },
    [processFiles],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) processFiles(files);
    },
    [processFiles],
  );

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: drop zone uses native drag events; keyboard path is the Choose files button inside
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
        multiple
        onChange={handleChange}
        disabled={disabled}
        className="hidden"
        aria-label="Upload files"
      />

      {selectedFiles.length > 0 && !error ? (
        <div className="flex flex-col items-center gap-1.5">
          <ul className="max-h-40 w-full overflow-y-auto">
            {selectedFiles.map(({ position, file }) => (
              <li
                key={position}
                className="truncate text-xs text-text-muted font-mono"
              >
                {file.name}{" "}
                <span className="text-text-faint">({mb(file.size)})</span>
              </li>
            ))}
          </ul>
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
              Choose files
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
