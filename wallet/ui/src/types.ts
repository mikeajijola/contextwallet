export type SourceStatus = "available" | "connected";

export interface Proposal {
  field: string;
  status: string;
}

export interface SchemaFieldDTO {
  name: string;
  band: string;
  node?: string;
  status: string;
}

export interface OnboardReportDTO {
  auto: number;
  flagged: number;
  deferred: number;
  proposals: Proposal[];
  schema?: SchemaFieldDTO[];
}

export interface ConnectorDTO {
  source: string;
  label: string;
  status: SourceStatus;
  report?: OnboardReportDTO;
}

export interface ConsumerSourceDTO {
  source: string;
  label: string;
  enabled: boolean;
  note?: string;
}

export interface ConsumerDTO {
  consumer_id: string;
  label: string;
  owner: boolean;
  active: boolean;
  cap_id: string | null;
  sources: ConsumerSourceDTO[];
}

export type NodeKind = "source" | "cell" | "ontology" | "principal";
export type EdgeKind = "sourced_from" | "classified_as" | "belongs_to";

export interface GraphNode {
  id: string;
  label: string;
  kind: NodeKind;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface GraphDTO {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type CardKind = "agreed" | "conflict_ordered" | "conflict_unordered" | "signal" | "refusal";
export type AnswerKind = CardKind | "absent";

export interface ConflictValueDTO {
  value: string;
  source: string;
  date: string | null;
}

export interface CardDTO {
  kind: CardKind;
  value?: string;
  source?: string;
  date?: string | null;
  ontology_node?: string;
  values?: ConflictValueDTO[];
  default_selection?: number | null;
  participants?: string;
  channel?: string;
  topic?: string;
  follow_up?: string | null;
  transcript_cell_id?: string | null;
  message?: string;
}

export interface AskResponse {
  answer_kind: AnswerKind;
  cards: CardDTO[];
}

export interface FetchResponse {
  value?: string;
  refusal?: string;
}
