import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TablePagination } from "./TablePagination";

const defaultProps = {
  page: 1,
  totalPages: 3,
  total: 150,
  limit: 50,
  onPageChange: vi.fn(),
};

describe("TablePagination", () => {
  it("renders null when totalPages is 1", () => {
    const { container } = render(
      <TablePagination {...defaultProps} totalPages={1} total={30} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders count summary with correct range", () => {
    render(<TablePagination {...defaultProps} />);
    expect(screen.getByText(/1–50 of 150/)).toBeInTheDocument();
  });

  it("renders count summary for middle page", () => {
    render(<TablePagination {...defaultProps} page={2} />);
    expect(screen.getByText(/51–100 of 150/)).toBeInTheDocument();
  });

  it("renders count summary for last page with partial items", () => {
    render(<TablePagination {...defaultProps} page={3} total={120} />);
    // Page 3: start=101, end=min(150, 120)=120
    expect(screen.getByText(/101–120 of 120/)).toBeInTheDocument();
  });

  it("disables Previous on page 1", () => {
    render(<TablePagination {...defaultProps} page={1} />);
    const prev = screen.getByLabelText("Go to previous page");
    expect(prev).toHaveAttribute("aria-disabled", "true");
  });

  it("disables Next on last page", () => {
    render(<TablePagination {...defaultProps} page={3} />);
    const next = screen.getByLabelText("Go to next page");
    expect(next).toHaveAttribute("aria-disabled", "true");
  });

  it("calls onPageChange when clicking a page number", async () => {
    const onPageChange = vi.fn();
    render(<TablePagination {...defaultProps} onPageChange={onPageChange} />);

    await userEvent.click(screen.getByText("2"));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("calls onPageChange with page-1 when clicking Previous", async () => {
    const onPageChange = vi.fn();
    render(
      <TablePagination
        {...defaultProps}
        page={2}
        onPageChange={onPageChange}
      />,
    );

    await userEvent.click(screen.getByLabelText("Go to previous page"));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("calls onPageChange with page+1 when clicking Next", async () => {
    const onPageChange = vi.fn();
    render(
      <TablePagination
        {...defaultProps}
        page={2}
        onPageChange={onPageChange}
      />,
    );

    await userEvent.click(screen.getByLabelText("Go to next page"));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it("does not call onPageChange when clicking disabled Previous", async () => {
    const onPageChange = vi.fn();
    render(
      <TablePagination
        {...defaultProps}
        page={1}
        onPageChange={onPageChange}
      />,
    );

    // pointer-events-none prevents the click, but fire it anyway
    await userEvent.click(screen.getByLabelText("Go to previous page"));
    expect(onPageChange).not.toHaveBeenCalled();
  });

  it("marks active page with aria-current", () => {
    render(<TablePagination {...defaultProps} page={2} />);
    const activeLink = screen.getByText("2").closest("a");
    expect(activeLink).toHaveAttribute("aria-current", "page");
  });

  it("shows ellipsis when totalPages exceeds 7", () => {
    render(
      <TablePagination
        {...defaultProps}
        page={5}
        totalPages={10}
        total={500}
      />,
    );

    // Should have page numbers and "More pages" for ellipsis
    expect(screen.getAllByText("More pages").length).toBeGreaterThan(0);
  });

  it("shows all page numbers without ellipsis for 7 or fewer pages", () => {
    render(
      <TablePagination {...defaultProps} page={1} totalPages={5} total={250} />,
    );

    // All 5 page numbers should be visible
    for (let i = 1; i <= 5; i++) {
      expect(screen.getByText(String(i))).toBeInTheDocument();
    }
    // No ellipsis
    expect(screen.queryByText("More pages")).not.toBeInTheDocument();
  });
});
