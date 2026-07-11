import { describe, expect, it } from "vitest";

import { renderWithProviders, screen, userEvent } from "#/test/test-utils";

import { CodeExecutionCard } from "./CodeExecutionCard";

describe("CodeExecutionCard", () => {
  it("shows a running state before the result arrives", () => {
    renderWithProviders(
      <CodeExecutionCard
        execution={{ id: "srvtoolu_1", command: "print(total)" }}
      />,
    );
    expect(screen.getByText("Running code…")).toBeInTheDocument();
    expect(screen.getByText("print(total)")).toBeInTheDocument();
    expect(screen.queryByText("Show output")).not.toBeInTheDocument();
  });

  it("shows success and reveals output on toggle", async () => {
    renderWithProviders(
      <CodeExecutionCard
        execution={{
          id: "srvtoolu_1",
          command: "print(total)",
          stdout: "412",
          stderr: "",
          returnCode: 0,
        }}
      />,
    );
    expect(screen.getByText("Ran code")).toBeInTheDocument();
    expect(screen.queryByText("412")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("Show output"));
    expect(screen.getByText("412")).toBeInTheDocument();
    expect(screen.getByText("Hide output")).toBeInTheDocument();
  });

  it("shows a failure state with the exit code and stderr", async () => {
    renderWithProviders(
      <CodeExecutionCard
        execution={{
          id: "srvtoolu_1",
          command: "1/0",
          stdout: "",
          stderr: "ZeroDivisionError",
          returnCode: 1,
        }}
      />,
    );
    expect(screen.getByText("Code failed (exit 1)")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Show output"));
    expect(screen.getByText("ZeroDivisionError")).toBeInTheDocument();
  });
});
