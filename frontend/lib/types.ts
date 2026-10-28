export type Institution = {
  id: string;
  name: string;
  aicte_pid: string | null;
  institution_type: string | null;
  trust_society_name: string | null;
  address_line: string | null;
  city: string | null;
  district: string | null;
  state: string | null;
  pincode: string | null;
  contact_email: string | null;
  is_synthetic: boolean;
  created_at: string;
};

export type ApplicationListItem = {
  id: string;
  reference_no: string;
  title: string;
  institution_id: string;
  institution_name: string;
  category_code: string;
  academic_year: string;
  status: ApplicationStatus;
  priority: "low" | "normal" | "high";
  updated_at: string;
};

export type ApplicationStatus =
 | "draft"
 | "submitted"
 | "under_review"
 | "additional_info_required"
 | "forwarded_to_authority"
 | "approved"
 | "rejected"
 | "withdrawn";

export type Application = {
  id: string;
  reference_no: string;
  institution: Institution | null;
  institution_id?: string;
  category_code: string;
  academic_year: string;
  title: string;
  description: string | null;
  status: ApplicationStatus;
  declared_data: Record<string, string>;
  priority: "low" | "normal" | "high";
  submitted_at: string | null;
  last_processed_at: string | null;
  last_validated_at: string | null;
  risk_summary: {
    open?: number;
    by_severity?: Record<string, number>;
    by_category?: Record<string, number>;
    error?: string | null;
  };
  created_at: string;
};

export type DocumentRecord = {
  id: string;
  title: string;
  status: string;
  status_detail: string | null;
  declared_category_code: string | null;
  detected_category_code: string | null;
  classification_method: string | null;
  classification_confidence: number | null;
  version_no: number;
  updated_at: string;
  page_count?: number;
  size_bytes?: number;
  storage_sha256?: string;
  version?: DocumentVersion;
};

export type DocumentVersion = {
  id: string;
  version_no: number;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  text_layer_chars: number | null;
  ocr_used: boolean;
  mean_ocr_confidence: string | null;
  processing_status: string;
  forensics: Record<string, unknown> | null;
  phash: string | null;
};

export type ExtractedField = {
  id: string;
  document_id: string;
  field_name: string;
  label: string;
  value_type: string;
  raw_value: string | null;
  normalized_value: unknown;
  confidence: string | null;
  extraction_method: string | null;
  status: string;
  corrected_value: string | null;
};

export type Finding = {
  id: string;
  application_id: string;
  reference_no: string | null;
  finding_code: string;
  category: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  status: string;
  title: string;
  description: string;
  expected_value: string | null;
  observed_value: string | null;
  recommended_action: string | null;
  detection_method: string;
  confidence: number | null;
  is_simulated: boolean;
  is_advisory: boolean;
  visible_to_institution: boolean;
  first_detected_at: string;
  last_detected_at: string;
  document_id: string | null;
  evidence?: Evidence[];
};

export type Evidence = {
  id: string;
  role: string;
  document_id: string | null;
  page_no: number | null;
  excerpt: string | null;
  bbox: number[] | null;
};

export type ValidationRun = {
  id: string;
  status: string;
  rules_evaluated: number;
  triggered_by: string | null;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown> | null;
};

export type VerificationAttempt = {
  id: string;
  application_id: string;
  document_id: string | null;
  provider_code: string;
  reference_no: string | null;
  status: string;
  is_simulated: boolean;
  compared_fields: Record<string, string>;
  response: Record<string, unknown> | null;
  requested_at: string;
};

export type VerificationProvider = {
  code: string;
  name: string;
  type: string;
  availability: string;
  is_enabled: boolean;
  supported_document_categories: string[];
  description: string | null;
};

export type ReportRecord = {
  id: string;
  application_id: string;
  reference_code: string;
  report_type: string;
  generated_at: string;
  size_bytes: number | null;
  includes_simulated: boolean;
};

export type AuditEventRecord = {
  id: number;
  event_id: string;
  occurred_at: string;
  actor_email: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  application_id: string | null;
  meta: Record<string, unknown> | null;
  event_hash: string;
};

export type RuleRuleCurrentVersion = {
  id: string;
  version: number;
  name: string;
  description: string | null;
  definition: Record<string, unknown>;
  severity: string;
  effective_from: string | null;
  effective_to: string | null;
  source_document: string | null;
  source_clause: string | null;
  source_quote: string | null;
  is_demo_rule: boolean;
  applies_to: string[];
};

export type RuleRecord = {
  id: string;
  rule_code: string;
  name: string;
  category: string | null;
  is_active: boolean;
  current_version: RuleRuleCurrentVersion | null;
  version_count: number;
};

export type UserRecord = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  is_demo_account: boolean;
};

export type HistoryEntry = {
  from: string | null;
  to: string;
  reason: string | null;
  at: string;
  actor: string | null;
};

export type Summary = {
  applications_total: number;
  applications_by_status: Record<string, number>;
  applications_by_priority: Record<string, number>;
  documents_total: number;
  documents_processed: number;
  documents_unreadable: number;
  findings_open: number;
  findings_by_status: Record<string, number>;
  open_findings_by_severity: Record<string, number>;
  recent_validation_runs: {
    id: string;
    application_id: string;
    status: string;
    started_at: string;
    summary: Record<string, unknown> | null;
  }[];
};
