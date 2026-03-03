import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

interface TablePaginationProps {
  page: number;
  totalPages: number;
  total: number;
  limit: number;
  onPageChange: (page: number) => void;
}

export function TablePagination({
  page,
  totalPages,
  total,
  limit,
  onPageChange,
}: TablePaginationProps) {
  if (totalPages <= 1) return null;

  const pageNumbers = buildPageNumbers(page, totalPages);
  const start = (page - 1) * limit + 1;
  const end = Math.min(page * limit, total);

  return (
    <div className="mt-4 flex items-center justify-between">
      <p className="text-sm tabular-nums text-text-muted">
        {start}&ndash;{end} of {total}
      </p>

      <Pagination className="mx-0 w-auto justify-end">
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href="#"
              onClick={(e) => {
                e.preventDefault();
                if (page > 1) onPageChange(page - 1);
              }}
              aria-disabled={page <= 1}
              className={page <= 1 ? "pointer-events-none opacity-50" : ""}
            />
          </PaginationItem>

          {pageNumbers.map((entry, i) =>
            entry === "ellipsis" ? (
              <PaginationItem
                // biome-ignore lint/suspicious/noArrayIndexKey: ellipsis positions are stable
                key={`ellipsis-${i}`}
              >
                <PaginationEllipsis />
              </PaginationItem>
            ) : (
              <PaginationItem key={entry}>
                <PaginationLink
                  href="#"
                  isActive={entry === page}
                  onClick={(e) => {
                    e.preventDefault();
                    onPageChange(entry);
                  }}
                >
                  {entry}
                </PaginationLink>
              </PaginationItem>
            ),
          )}

          <PaginationItem>
            <PaginationNext
              href="#"
              onClick={(e) => {
                e.preventDefault();
                if (page < totalPages) onPageChange(page + 1);
              }}
              aria-disabled={page >= totalPages}
              className={
                page >= totalPages ? "pointer-events-none opacity-50" : ""
              }
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  );
}

/** Build page numbers with ellipsis gaps. Shows all when <= 7 pages. */
function buildPageNumbers(
  current: number,
  total: number,
): (number | "ellipsis")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  // Always show: first, second, current ± 1, second-to-last, last
  const show = new Set([
    1,
    2,
    current - 1,
    current,
    current + 1,
    total - 1,
    total,
  ]);

  const sorted = Array.from(show)
    .filter((p) => p >= 1 && p <= total)
    .sort((a, b) => a - b);

  const result: (number | "ellipsis")[] = [];
  let prev: number | null = null;

  for (const p of sorted) {
    if (prev !== null && p - prev > 1) {
      result.push("ellipsis");
    }
    result.push(p);
    prev = p;
  }

  return result;
}
