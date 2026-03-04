import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

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

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

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

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        disabled={disabled}
        className="hidden"
        aria-label="Upload file"
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        {selectedFile ? "Change file" : "Choose file"}
      </Button>

      {selectedFile && !error && (
        <p className="text-xs text-text-muted font-mono">
          {selectedFile.name}{" "}
          <span className="text-text-faint">
            ({(selectedFile.size / 1024 / 1024).toFixed(1)}MB)
          </span>
        </p>
      )}

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
