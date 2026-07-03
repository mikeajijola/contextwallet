import { useEffect, useRef } from "react";
import cytoscape, { type Core, type StylesheetJson } from "cytoscape";
// @ts-expect-error -- no bundled types for cytoscape-cose-bilkent
import coseBilkent from "cytoscape-cose-bilkent";
import type { GraphDTO } from "../types";

cytoscape.use(coseBilkent);

// deterministic hash -> [0,1) so the same node id always seeds the same starting position.
// Combined with `randomize: false` on the layout, this keeps runs visually identical.
function hash01(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return (h % 100000) / 100000;
}

function seededPosition(id: string, width: number, height: number) {
  return { x: hash01(id) * width, y: hash01(id + ":y") * height };
}

const STYLE: StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "font-size": 8,
      "font-family": "IBM Plex Mono, monospace",
      color: "#16223A",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "overlay-opacity": 0,
    },
  },
  {
    selector: 'node[kind = "source"]',
    style: { shape: "round-rectangle", "background-color": "#16223A", width: 34, height: 24 },
  },
  {
    selector: 'node[kind = "cell"]',
    style: { shape: "ellipse", "background-color": "#14857C", width: 10, height: 10 },
  },
  {
    selector: 'node[kind = "ontology"]',
    style: { shape: "diamond", "background-color": "#8895A7", width: 16, height: 16 },
  },
  {
    selector: 'node[kind = "principal"]',
    style: { shape: "ellipse", "background-color": "#16223A", width: 26, height: 26, "border-width": 2, "border-color": "#14857C" },
  },
  {
    selector: 'edge[kind = "sourced_from"]',
    style: { "line-style": "solid", "line-color": "#c7ced7", width: 1, "curve-style": "bezier" },
  },
  {
    selector: 'edge[kind = "classified_as"]',
    style: { "line-style": "dashed", "line-color": "#c7ced7", width: 1, "curve-style": "bezier" },
  },
  {
    selector: 'edge[kind = "belongs_to"]',
    style: { "line-style": "solid", "line-color": "#e3e7ec", width: 0.6, "curve-style": "bezier" },
  },
];

export function GraphView({ graph }: { graph: GraphDTO }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const reduceMotion = useRef(
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: STYLE,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const width = containerRef.current?.clientWidth || 800;
    const height = containerRef.current?.clientHeight || 600;

    const nextIds = new Set(graph.nodes.map((n) => n.id));
    const currentIds = new Set(cy.nodes().map((n) => n.id()));

    // exiting nodes: fade + shrink, then remove (instant under reduced motion)
    cy.nodes().forEach((n) => {
      if (nextIds.has(n.id())) return;
      if (reduceMotion.current) {
        n.remove();
      } else {
        n.animate(
          { style: { opacity: 0, width: 1, height: 1 } },
          { duration: 220, easing: "ease-in", complete: () => n.remove() },
        );
      }
    });

    // entering nodes: add at a deterministic seeded position, fade + grow in
    const entering = graph.nodes.filter((n) => !currentIds.has(n.id));
    if (entering.length) {
      const added = cy.add(
        entering.map((n) => ({
          data: { id: n.id, label: n.label, kind: n.kind },
          position: seededPosition(n.id, width, height),
        })),
      );
      added.style("opacity", reduceMotion.current ? 1 : 0);
      if (!reduceMotion.current) {
        added.forEach((n) => {
          n.animate({ style: { opacity: 1 } }, { duration: 220, easing: "ease-out" });
        });
      }
    }

    // labels can change on an already-present node (rare); keep them in sync
    graph.nodes.forEach((n) => {
      const el = cy.getElementById(n.id);
      if (el.nonempty() && el.data("label") !== n.label) el.data("label", n.label);
    });

    // edges have no identity worth diffing — rebuild the set against the current nodes
    cy.edges().remove();
    const edgeElements = graph.edges
      .filter((e) => nextIds.has(e.source) && nextIds.has(e.target))
      .map((e, i) => ({
        data: { id: `edge:${i}:${e.source}->${e.target}`, source: e.source, target: e.target, kind: e.kind },
      }));
    cy.add(edgeElements);

    const layout = cy.layout({
      name: "cose-bilkent",
      randomize: false,
      animate: !reduceMotion.current,
      animationDuration: 300,
      fit: true,
      padding: 40,
      nodeRepulsion: 5500,
      idealEdgeLength: 60,
    } as cytoscape.LayoutOptions);
    layout.run();
  }, [graph]);

  return <div className="map__canvas" ref={containerRef} />;
}
