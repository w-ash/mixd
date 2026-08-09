import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FileUpload } from "./FileUpload";

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

function jsonFile(name: string, content = "{}"): File {
  return new File([content], name, { type: "application/json" });
}

describe("FileUpload", () => {
  it("renders choose files button", () => {
    render(<FileUpload onFilesSelect={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: /choose files/i }),
    ).toBeInTheDocument();
  });

  it("accepts multiple files", () => {
    render(<FileUpload onFilesSelect={vi.fn()} />);

    expect(fileInput()).toHaveAttribute("multiple");
  });

  it("calls onFilesSelect with every chosen file", async () => {
    const onFilesSelect = vi.fn();
    const user = userEvent.setup();
    render(<FileUpload onFilesSelect={onFilesSelect} />);

    const files = [jsonFile("part-1.json"), jsonFile("part-2.json")];
    await user.upload(fileInput(), files);

    expect(onFilesSelect).toHaveBeenCalledWith(files);
  });

  it("lists every chosen filename after selection", async () => {
    const user = userEvent.setup();
    render(<FileUpload onFilesSelect={vi.fn()} />);

    await user.upload(fileInput(), [
      jsonFile("history-2019.json"),
      jsonFile("history-2020.json"),
    ]);

    expect(screen.getByText(/history-2019\.json/)).toBeInTheDocument();
    expect(screen.getByText(/history-2020\.json/)).toBeInTheDocument();
  });

  it("a single file is the degenerate case", async () => {
    const onFilesSelect = vi.fn();
    const user = userEvent.setup();
    render(<FileUpload onFilesSelect={onFilesSelect} />);

    const file = jsonFile("history.json");
    await user.upload(fileInput(), file);

    expect(onFilesSelect).toHaveBeenCalledWith([file]);
    expect(screen.getByText(/history\.json/)).toBeInTheDocument();
  });

  it("rejects an oversized file, naming it", async () => {
    const onFilesSelect = vi.fn();
    const user = userEvent.setup();
    render(<FileUpload onFilesSelect={onFilesSelect} maxSize={100} />);

    await user.upload(fileInput(), [
      jsonFile("small.json"),
      jsonFile("big.json", "x".repeat(200)),
    ]);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/file too large/i);
    expect(alert).toHaveTextContent(/big\.json/);
    expect(onFilesSelect).toHaveBeenCalledWith([]);
  });

  it("rejects when the selection exceeds the aggregate size cap", async () => {
    const user = userEvent.setup();
    render(
      <FileUpload onFilesSelect={vi.fn()} maxSize={100} maxTotalSize={150} />,
    );

    await user.upload(fileInput(), [
      jsonFile("a.json", "x".repeat(90)),
      jsonFile("b.json", "y".repeat(90)),
    ]);

    expect(screen.getByRole("alert")).toHaveTextContent(/selection too large/i);
  });

  it("rejects when more files are chosen than the count cap", async () => {
    const user = userEvent.setup();
    render(<FileUpload onFilesSelect={vi.fn()} maxFiles={2} />);

    await user.upload(fileInput(), [
      jsonFile("a.json"),
      jsonFile("b.json"),
      jsonFile("c.json"),
    ]);

    expect(screen.getByRole("alert")).toHaveTextContent(/too many files/i);
  });

  it("disables button when disabled prop is true", () => {
    render(<FileUpload onFilesSelect={vi.fn()} disabled />);

    expect(
      screen.getByRole("button", { name: /choose files/i }),
    ).toBeDisabled();
  });
});
