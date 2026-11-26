// The shapes /api/ws returns (src/dispute_agent/workspace/views.py and router.py).

export type Tone = "settled" | "done" | "working" | "waiting" | "attention" | "closed";
export type TagTone = "proven" | "neutral" | "you" | "agent" | "hub" | "failed" | "caution";
export type Kind =
  | "credit"
  | "email"
  | "handover"
  | "unverified"
  | "reopened"
  | "waiting"
  | "investigating"
  | "done";

export interface Persona {
  seller_id: string;
  business: string;
  owner: string;
  category: string;
  contact_tag: string;
  [key: string]: unknown;
}

export interface Session {
  approvers: string[];
  kill_switch: boolean;
  needs_token: boolean;
  ask: boolean;
  ask_unavailable: string | null;
  sellers: Persona[];
  evidence_items: string[];
  store: boolean;
}

export interface Due {
  text: string;
  at_risk: boolean;
  at: string | null;
}

export interface ViewCount {
  section: string;
  key: string;
  label: string;
  count: number;
}

export interface WorkItem {
  case_id: string;
  shipment_ref: string;
  seller_ref: string;
  seller: string;
  state: string;
  status: string;
  tone: Tone;
  summary: string;
  tag: string;
  tag_tone: TagTone;
  kind: Kind;
  views: string[];
  credit: string | null;
  due: Due;
  opened_at: string;
  updated_at: string;
  reopen_count: number;
  is_open: boolean;
}

export interface WorkList {
  views: ViewCount[];
  items: WorkItem[];
  no_store?: string;
}

export interface LabelValue {
  label: string;
  value: string;
}

export interface Approval {
  approval_id: string;
  title: string;
  what: string;
  approve_label: string;
  is_credit: boolean;
  requested_at: string;
  expires_at: string;
  matches_approval: boolean;
  numbers: { value: string; source: string }[];
  amount?: string;
  reason?: string;
  reason_words?: string;
  equation?: LabelValue[] | null;
  already_credited?: string;
  duplicate_debit?: string | null;
  remeasured_charge?: string | null;
  figures?: LabelValue[];
  to?: string;
  subject?: string;
  body?: string;
}

export interface Verdict {
  action: string;
  label: string;
  effect: string;
  reason_code: string | null;
}

export interface Handover {
  reason: string;
  reason_detail: string;
  rule: string;
  task: string;
  checks: string[];
  refused: string;
  no_verdict: string;
  verdicts: Verdict[];
  can_return: boolean;
  credit_verified: boolean;
  can_withdraw: boolean;
  unknown: { action_id: string; what: string; where: string }[];
}

export interface Step {
  at: string;
  actor: "Code" | "AI" | "Rules" | "Person" | "Seller" | "Hub" | "System";
  title: string;
  summary: string;
  lines: string[];
  quoted: string[];
  quote_caption: string;
  proposer: string;
  round: number;
  minor: boolean;
  mark: string;
  tag: string;
}

export interface Dims {
  length_cm: number;
  width_cm: number;
  height_cm: number;
  grams: number | null;
}

export interface Shipment {
  ref: string;
  claim_ref: string;
  seller_ref: string;
  status: string;
  zone: string;
  declared: string;
  measured: string;
  charged: string;
  expected: string;
  over_card: string;
  billing_error: boolean;
  verdict: string;
  breakdown: { step: string; declared: string; measured: string }[];
  disputed: boolean;
}

export interface CaseDetail {
  case_id: string;
  shipment_ref: string;
  claim_ref: string;
  seller_ref: string;
  seller: string;
  state: string;
  status: string;
  tone: Tone;
  tag: string;
  tag_tone: TagTone;
  kind: Kind;
  is_open: boolean;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  reopen_count: number;
  rejection_count: number;
  round: number;
  due: Due;
  journey: {
    headline: string;
    next_step: string;
    why: string;
    why_code: string;
    phases: { label: string; mark: string }[];
    figures: LabelValue[];
    lead: string;
    figure_note: string;
  };
  recommendation: {
    action: string;
    confidence: string;
    by: string;
    rationale: string;
    findings: { kind: string; statement: string; cites: string[] }[];
  } | null;
  approvals: Approval[];
  handover: Handover | null;
  came_back: { who: string; at: string; summary: string; words: string[] }[];
  outside: { source: string; reference: string; text: string; flags: string[] }[];
  statement: string;
  evidence: {
    declared: Dims | null;
    measured: Dims | null;
    scanner: string | null;
    remeasured: Dims | null;
    items: { key: string; name: string; status: string }[];
    history: {
      headline: string;
      verdict: string | null;
      this_parcel: string;
      usual: string | null;
      usual_volume_cm3: [string, string] | null;
      references: number;
      reference_refs: string[];
      outlier_refs: string[];
    } | null;
    photo: boolean;
    photo_check: {
      ok: boolean;
      summary: string;
      rows: LabelValue[];
      observations: string[];
      by: string;
    } | null;
  };
  record: {
    ai_calls: number;
    ai_cost_usd: string;
    round_titles: Record<string, string>;
    steps: Step[];
  };
  shipment: Shipment | null;
  persona: Persona | null;
  other_open: WorkItem[];
}

export interface SellerPage {
  persona: Persona;
  can_raise: boolean;
  cannot_raise: string | null;
  shipments: Shipment[];
  tickets: {
    case_id: string;
    shipment_ref: string;
    status: string;
    tone: Tone;
    is_open: boolean;
    opened_at: string;
    updated_at: string;
    headline: string;
  }[];
}

export interface Ledger {
  rows: {
    posted_at: string;
    shipment_ref: string;
    kind: "Credit" | "Charge";
    amount: string;
    reason: string;
    transaction_id: string;
    case_id: string;
    approved_by: string;
    proved: string;
  }[];
  totals: { charged: string; credited: string; net: string };
  proposed: { case_id: string; shipment_ref: string; amount: string | null; stage: string }[];
}

export interface RunSummary {
  kind: string;
  decider: string | null;
  model: string | null;
  prompt_version: string | null;
  created_at: string;
  n: number;
  acceptable?: number;
  prohibited?: number;
  not_acceptable?: number;
  agree?: number;
  critical?: number;
  critical_caught?: number;
  misses?: number;
  false_alarms?: number;
  cost_usd?: string | number | null;
  git_commit?: string | null;
}

export interface Evals {
  ai: RunSummary | null;
  rules: RunSummary | null;
  judge: RunSummary | null;
  misses: {
    id: string;
    shipment: string | null;
    situation: string;
    chose: string | null;
    grade: string | null;
    acceptable: string[];
  }[];
  runs: RunSummary[];
  online: {
    on: boolean;
    spent_usd: string;
    cap_usd: string;
    judged: number;
    flagged: number;
    waiting: number;
    rows: number;
  };
}

export interface Team {
  flow: ViewCount[];
  stuck: { label: string; count: number }[];
  approvers: { name: string; decided_today: number }[];
}
