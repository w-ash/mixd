import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCallIndicator } from "./ToolCallIndicator";

describe("ToolCallIndicator", () => {
  it("shows Proposing for in-flight writes", () => {
    render(
      <ToolCallIndicator
        toolCall={{ id: "1", name: "create_playlist", kind: "write" }}
      />,
    );
    expect(screen.getByText("Proposing playlist…")).toBeInTheDocument();
  });

  it("shows Looking up for in-flight reads", () => {
    render(
      <ToolCallIndicator
        toolCall={{ id: "1", name: "get_liked_tracks", kind: "read" }}
      />,
    );
    expect(screen.getByText("Looking up liked tracks…")).toBeInTheDocument();
  });

  it("shows Running for in-flight agentic tools (never Proposing)", () => {
    render(
      <ToolCallIndicator
        toolCall={{ id: "1", name: "delegate_analysis", kind: "agentic" }}
      />,
    );
    expect(screen.getByText("Running deep analysis…")).toBeInTheDocument();
  });

  it("shows Ran once an agentic tool completes", () => {
    render(
      <ToolCallIndicator
        toolCall={{
          id: "1",
          name: "delegate_analysis",
          kind: "agentic",
          result: { summary: "…" },
        }}
      />,
    );
    expect(screen.getByText("Ran deep analysis")).toBeInTheDocument();
  });

  it("humanizes an unknown tool name", () => {
    render(
      <ToolCallIndicator
        toolCall={{ id: "1", name: "get_recent_scrobbles", kind: "read" }}
      />,
    );
    expect(
      screen.getByText("Looking up recent scrobbles…"),
    ).toBeInTheDocument();
  });

  it("shows Checked once the result arrives", () => {
    render(
      <ToolCallIndicator
        toolCall={{
          id: "1",
          name: "create_playlist",
          kind: "write",
          result: {},
        }}
      />,
    );
    expect(screen.getByText("Checked playlist")).toBeInTheDocument();
  });
});
