import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";
import type { DepGraph, RiskLabel } from "../api";

const RISK_COLOR: Record<RiskLabel, string> = {
  rotten: "#ef5350",
  aging: "#f0b93c",
  healthy: "#35d39a",
};

interface DependencyGraphProps {
  graph: DepGraph;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}

function elementId(nodeId: string): string {
  return nodeId.startsWith("pkg:") ? nodeId.slice(4) : nodeId;
}

export function DependencyGraph({ graph, selected, onSelect }: DependencyGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            "border-width": 2,
            "border-color": "data(border)",
            label: "data(label)",
            color: "#c7d1db",
            "font-size": 11,
            "font-family": "ui-monospace, monospace",
            "text-valign": "bottom",
            "text-margin-y": 6,
            width: 34,
            height: 34,
            "text-max-width": "110px",
            "text-wrap": "ellipsis",
          },
        },
        {
          selector: 'node[kind = "project"]',
          style: {
            width: 58,
            height: 58,
            "font-size": 12,
            "font-weight": 600,
            color: "#e7edf3",
          },
        },
        {
          selector: "node[alerts > 0]",
          style: {
            "border-style": "double",
            "border-width": 4,
          },
        },
        {
          selector: "node.unobserved",
          style: {
            "border-style": "dashed",
            opacity: 0.85,
          },
        },
        {
          selector: "node.selected",
          style: {
            "border-color": "#ffffff",
            "border-width": 3,
            "overlay-color": "#ffffff",
            "overlay-opacity": 0.12,
            "overlay-padding": 10,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#2c3540",
            "target-arrow-color": "#2c3540",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "preset" },
      wheelSensitivity: 0.2,
      minZoom: 0.4,
      maxZoom: 2.2,
    });

    cy.on("tap", "node", (event) => {
      const id = event.target.id();
      // Hand back the bare package name: the graph's node ids carry a prefix,
      // but the rest of the UI keys on dependency names.
      onSelect(id === "__project__" ? "" : elementId(id));
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [onSelect]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const elements: ElementDefinition[] = graph.nodes.map((node) => {
      const isProject = node.kind === "project";
      return {
        group: "nodes",
        data: {
          id: node.id,
          label: node.label,
          kind: node.kind,
          alerts: node.alerts,
        },
        classes: [
          isProject ? "" : node.observed ? "" : "unobserved",
        ]
          .filter(Boolean)
          .join(" "),
        style: {
          "background-color": isProject ? "#7aa2f7" : RISK_COLOR[node.label_kind],
          "border-color": isProject ? "#a9c2ff" : `${RISK_COLOR[node.label_kind]}66`,
        },
      } as ElementDefinition;
    });

    const edges: ElementDefinition[] = graph.edges.map((edge, index) => ({
      group: "edges",
      data: {
        id: `e-${index}`,
        source: edge.source,
        target: edge.target,
      },
    }));

    cy.elements().remove();
    cy.add([...elements, ...edges]);
    cy.layout({
      name: "concentric",
      concentric: (node) => (node.id() === "__project__" ? 10 : 1),
      levelWidth: () => 2,
      minNodeSpacing: 46,
      padding: 30,
      animate: false,
    }).run();
  }, [graph]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().removeClass("selected");
    if (selected) {
      cy.getElementById(selected).addClass("selected");
    }
  }, [selected]);

  return (
    <div className="panel relative overflow-hidden">
      <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
        <p className="text-sm font-medium">Dependency graph</p>
        <div className="flex items-center gap-3 text-[10px] text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: RISK_COLOR.rotten }} />
            rotten
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: RISK_COLOR.aging }} />
            aging
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: RISK_COLOR.healthy }} />
            healthy
          </span>
        </div>
      </div>
      <div
        ref={containerRef}
        className="h-[380px] w-full"
        role="img"
        aria-label={`Dependency graph with ${graph.nodes.length - 1} dependencies colour-coded by rot score`}
      />
      <p className="border-t border-line-soft px-4 py-2 text-[10px] text-muted">
        Solid fill is the rot score. A double ring means advisories; a dashed node means no import was observed.
      </p>
    </div>
  );
}

export { elementId };