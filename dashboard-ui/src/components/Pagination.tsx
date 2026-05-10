interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange?: (limit: number) => void;
}

export function Pagination({ total, limit, offset, onPageChange, onPageSizeChange }: PaginationProps) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);

  if (totalPages <= 1) {
    return null;
  }

  const handlePrev = () => {
    if (offset > 0) {
      onPageChange(Math.max(0, offset - limit));
    }
  };

  const handleNext = () => {
    if (offset + limit < total) {
      onPageChange(offset + limit);
    }
  };

  return (
    <div className="flex items-center justify-between bg-gray-800 rounded-lg px-4 py-3">
      <div className="flex items-center gap-4">
        <span className="text-gray-400 text-sm">
          Showing {offset + 1}–{Math.min(offset + limit, total)} of {total}
        </span>
        {onPageSizeChange && (
          <select
            value={limit}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
            aria-label="Page size"
          >
            <option value={10}>10 / page</option>
            <option value={25}>25 / page</option>
            <option value={50}>50 / page</option>
            <option value={100}>100 / page</option>
          </select>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={handlePrev}
          disabled={currentPage === 1}
          className="px-3 py-1 rounded text-sm bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Previous
        </button>
        <span className="text-gray-400 text-sm">
          Page {currentPage} of {totalPages}
        </span>
        <button
          onClick={handleNext}
          disabled={currentPage === totalPages}
          className="px-3 py-1 rounded text-sm bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    </div>
  );
}
