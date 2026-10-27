import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DepGraph } from "../api";

/** Records what the component asks cytoscape to draw. */
const captured = {
  elements: [] as { group: string; data: Record<string, unknown>; style?: Record<string, unknown>; classes?: string }[],
  selected: [] as string[],
  handlers: {} as Record<string, (event: { target: { id: () => string } }) => void>,
  destroyed: 0,
};

vi.mock("cytoscape", () => {
  const core = {
    on: (event: string, selector: string, handler: (event: { target: { id: () => string } }) => void) => {
      captured.handlers[`${event}:${selector}`] = handler;
    },
    elements: () => ({
      remove: () => {
        captured.elements = [];
      },
    }),
    add: (elements: typeof captured.elements) => {
      captured.elements.push(...elements);
    },
    layout: () => ({ run: () => undefined }),
    nodes: () => ({
      removeClass: () => undefined,
    }),
    getElementById: (id: string) => ({
      addClass: () => {
        captured.selected.push(id);
      },
    }),
    destroy: () => {
      captured.destroyed += 1;
    },
  };
  return { default: () => core };
});

import { DependencyGraph } from "./DependencyGraph";

const GRAPH: DepGraph = {
  nodes: [
    { id: "__project__", label: "demo-app", kind: "project", rot: 0, label_kind: "healthy", is_dev: false, alerts: 0, observed: false },
    { id: "pkg:lodash", label: "lodash", kind: "dependency", rot: 48, label_kind: "aging", is_dev: false, alerts: 6, observed: true },
    { id: "pkg:jsonwebtoken", label: "jsonwebtoken", kind: "dependency", rot: 90, label_kind: "rotten", is_dev: false, alerts: 4, observed: false },
    { id: "pkg:semver", label: "semver", kind: "dependency", rot: 0, label_kind: "healthy", is_dev: true, alerts: 0, observed: true },
  ],
  edges: [
    { source: "__project__", target: "pkg:lodash" },
    { source: "__project__", target: "pkg:jsonwebtoken" },
    { source: "__project__", target: "pkg:semver" },
  ],
};

describe("dependency graph", () => {
  beforeEach(() => {
    captured.elements = [];
    captured.selected = [];
    captured.handlers = {};
    captured.destroyed = 0;
  });

  it("draws every node and edge from the scan", () => {
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    const nodes = captured.elements.filter((element) => element.group === "nodes");
    const edges = captured.elements.filter((element) => element.group === "edges");

    expect(nodes).toHaveLength(4);
    expect(edges).toHaveLength(3);
  });

  it("colours nodes by their rot label", () => {
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    const byId = Object.fromEntries(
      captured.elements
        .filter((element) => element.group === "nodes")
        .map((element) => [element.data.id as string, element.style?.["background-color"]]),
    );

    expect(byId["pkg:jsonwebtoken"]).toBe("#ef5350");
    expect(byId["pkg:lodash"]).toBe("#f0b93c");
    expect(byId["pkg:semver"]).toBe("#35d39a");
  });

  it("marks a package with no observed import as dashed", () => {
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    const byId = Object.fromEntries(
      captured.elements
        .filter((element) => element.group === "nodes")
        .map((element) => [element.data.id as string, element]),
    );

    expect(byId["pkg:jsonwebtoken"].classes ?? "").toContain("unobserved");
    expect(byId["pkg:lodash"].classes ?? "").not.toContain("unobserved");
  });

  it("emits the package name when a node is clicked", () => {
    const onSelect = vi.fn();
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={onSelect} />);

    captured.handlers["tap:node"]?.({ target: { id: () => "pkg:lodash" } });

    expect(onSelect).toHaveBeenCalledWith("lodash");
  });

  it("emits an empty selection for the project node", () => {
    const onSelect = vi.fn();
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={onSelect} />);

    captured.handlers["tap:node"]?.({ target: { id: () => "__project__" } });

    expect(onSelect).toHaveBeenCalledWith("");
  });

  it("highlights the selected node", () => {
    render(<DependencyGraph graph={GRAPH} selected="pkg:lodash" onSelect={() => undefined} />);

    expect(captured.selected).toContain("pkg:lodash");
  });

  it("explains what the visual encodings mean", () => {
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    expect(screen.getByText(/solid fill is the rot score/i)).toBeInTheDocument();
    expect(screen.getByText(/dashed node means no import was observed/i)).toBeInTheDocument();
  });

  it("exposes an accessible description of the graph", () => {
    render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    expect(
      screen.getByRole("img", { name: /dependency graph with 3 dependencies/i }),
    ).toBeInTheDocument();
  });

  it("destroys the cytoscape instance on unmount", () => {
    const { unmount } = render(<DependencyGraph graph={GRAPH} selected={null} onSelect={() => undefined} />);

    unmount();

    expect(captured.destroyed).toBeGreaterThan(0);
  });
});