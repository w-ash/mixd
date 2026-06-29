import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type Mock,
  vi,
} from "vitest";

// loadImportedWorkflowDef → store.loadWorkflow runs an async ELK layout and
// buildEdges; mock the layout module so the store seeds synchronously and
// deterministically (mirrors EditorToolbar.test.tsx).
vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue({ flowEdges: [] }),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  getNodeCategoryName: vi.fn().mockReturnValue("source"),
}));

import type { WorkflowDefSchemaInput } from "#/api/generated/model";
import { useEditorStore } from "#/stores/editor-store";
import {
  downloadWorkflowDef,
  loadImportedWorkflowDef,
  parseWorkflowFile,
} from "./workflow-file";

const VALID_DEF: WorkflowDefSchemaInput = {
  id: "current_obsessions",
  name: "Current Obsessions",
  description: "8+ plays in 30 days",
  version: "1.0",
  tasks: [
    { id: "src", type: "source.liked_tracks", config: {}, upstream: [] },
    {
      id: "dst",
      type: "destination.create_playlist",
      config: { name: "X" },
      upstream: ["src"],
    },
  ],
};

describe("parseWorkflowFile", () => {
  it("round-trips a serialized WorkflowDef", () => {
    const parsed = parseWorkflowFile(JSON.stringify(VALID_DEF));
    expect(parsed.name).toBe("Current Obsessions");
    expect(parsed.description).toBe("8+ plays in 30 days");
    expect(parsed.version).toBe("1.0");
    expect(parsed.tasks).toHaveLength(2);
    expect(parsed.tasks?.[1].upstream).toEqual(["src"]);
  });

  it("projects away the file's id and server-minted fields", () => {
    // A raw row-export carries id + server fields; none must survive into the
    // draft — the server is the sole authority on identity.
    const rowExport = {
      ...VALID_DEF,
      id: "smuggled-slug",
      user_id: "someone-else",
      created_at: "2020-01-01T00:00:00Z",
      version_number: 7,
    };
    const parsed = parseWorkflowFile(JSON.stringify(rowExport));

    expect(parsed.id).not.toBe("smuggled-slug"); // derived placeholder, not the file's
    expect(parsed).not.toHaveProperty("user_id");
    expect(parsed).not.toHaveProperty("created_at");
    expect(parsed).not.toHaveProperty("version_number");
    expect(Object.keys(parsed).sort()).toEqual([
      "description",
      "id",
      "name",
      "tasks",
      "version",
    ]);
  });

  it("defaults missing tasks to an empty list", () => {
    const parsed = parseWorkflowFile(JSON.stringify({ name: "Bare" }));
    expect(parsed.tasks).toEqual([]);
  });

  it("rejects non-JSON with a clear message", () => {
    expect(() => parseWorkflowFile("not json {")).toThrow(/isn't valid JSON/);
  });

  it("rejects JSON that isn't an object", () => {
    expect(() => parseWorkflowFile("[1, 2, 3]")).toThrow(
      /isn't a Mixd workflow/,
    );
    expect(() => parseWorkflowFile('"just a string"')).toThrow(
      /isn't a Mixd workflow/,
    );
  });

  it("rejects an object with no name", () => {
    expect(() => parseWorkflowFile(JSON.stringify({ tasks: [] }))).toThrow(
      /no name/,
    );
    expect(() => parseWorkflowFile(JSON.stringify({ name: "  " }))).toThrow(
      /no name/,
    );
  });

  it("rejects a non-list tasks field", () => {
    expect(() =>
      parseWorkflowFile(JSON.stringify({ name: "X", tasks: "nope" })),
    ).toThrow(/tasks aren't a list/);
  });

  it("rejects malformed task entries (missing id/type, wrong shape)", () => {
    // A valid type but no id would otherwise seed a node with id=undefined.
    expect(() =>
      parseWorkflowFile(
        JSON.stringify({ name: "X", tasks: [{ type: "source.liked_tracks" }] }),
      ),
    ).toThrow(/every task needs a string id and type/);
    expect(() =>
      parseWorkflowFile(JSON.stringify({ name: "X", tasks: [42] })),
    ).toThrow(/every task needs a string id and type/);
    expect(() =>
      parseWorkflowFile(
        JSON.stringify({
          name: "X",
          tasks: [{ id: "a", type: "source.x", upstream: "b" }],
        }),
      ),
    ).toThrow(/every task needs a string id and type/);
  });
});

describe("downloadWorkflowDef", () => {
  let createObjectURL: Mock<(obj: Blob | MediaSource) => string>;
  let revokeObjectURL: Mock<(url: string) => void>;
  let origCreate: typeof URL.createObjectURL;
  let origRevoke: typeof URL.revokeObjectURL;
  const anchors: HTMLAnchorElement[] = [];

  beforeEach(() => {
    createObjectURL = vi.fn<(obj: Blob | MediaSource) => string>(
      () => "blob:mock-url",
    );
    revokeObjectURL = vi.fn<(url: string) => void>();
    origCreate = URL.createObjectURL;
    origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    anchors.length = 0;
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") anchors.push(el as HTMLAnchorElement);
      return el;
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    URL.createObjectURL = origCreate;
    URL.revokeObjectURL = origRevoke;
    vi.restoreAllMocks();
  });

  it("creates a JSON blob, names it via toWorkflowId, and revokes the url", async () => {
    downloadWorkflowDef({ id: "x", name: "My Cool Mix", tasks: [] });

    expect(createObjectURL).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("application/json");
    expect(JSON.parse(await blob.text())).toMatchObject({
      name: "My Cool Mix",
    });

    expect(anchors[0].download).toBe("my_cool_mix.json");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});

describe("loadImportedWorkflowDef", () => {
  beforeEach(() => {
    useEditorStore.setState({
      workflowId: "existing-id",
      workflowName: "Old",
      isDirty: false,
    });
  });

  it("seeds the editor store as an unsaved draft with no workflowId", () => {
    loadImportedWorkflowDef(JSON.stringify(VALID_DEF));

    const s = useEditorStore.getState();
    expect(s.workflowName).toBe("Current Obsessions");
    expect(s.workflowId).toBeNull(); // create path mints the id on Save
    expect(s.isDirty).toBe(true);
  });

  it("propagates parse errors to the caller", () => {
    expect(() => loadImportedWorkflowDef("garbage")).toThrow(
      /isn't valid JSON/,
    );
  });
});
