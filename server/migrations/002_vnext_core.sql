-- Core immutable inputs, facts, results and coordination records.
-- All DDL is additive and restart-safe. Do not apply from application startup.
CREATE TABLE IF NOT EXISTS security_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  security_id VARCHAR(32) NOT NULL,
  ts_code VARCHAR(16) NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  name VARCHAR(80) NOT NULL,
  exchange_code VARCHAR(8) NOT NULL,
  board_scope VARCHAR(32) NOT NULL,
  attributes JSON NOT NULL,
  valid_from CHAR(8) NOT NULL,
  valid_to CHAR(8) NOT NULL DEFAULT '',
  source_ref VARCHAR(255) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  KEY ix_security_validity (security_id, valid_from, valid_to),
  KEY ix_security_ts_code (ts_code, valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS mapping_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  scope_key VARCHAR(64) NOT NULL,
  security_id VARCHAR(32) NOT NULL DEFAULT '',
  board_scope VARCHAR(32) NOT NULL DEFAULT '',
  index_code VARCHAR(16) NOT NULL,
  return_policy_id BIGINT NOT NULL,
  valid_from CHAR(8) NOT NULL,
  valid_to CHAR(8) NOT NULL DEFAULT '',
  evidence_ids JSON NOT NULL,
  content_hash CHAR(64) NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  KEY ix_mapping_scope (scope_key, valid_from, valid_to),
  KEY ix_mapping_security (security_id, valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS calendar_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  exchange_code VARCHAR(8) NOT NULL,
  trade_date CHAR(8) NOT NULL,
  is_open TINYINT NOT NULL,
  session_segments JSON NOT NULL,
  source_ref VARCHAR(255) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  UNIQUE KEY uq_calendar_revision (exchange_code, trade_date, content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_bar_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  instrument_id VARCHAR(32) NOT NULL,
  instrument_kind VARCHAR(8) NOT NULL,
  trade_date CHAR(8) NOT NULL,
  close_price DECIMAL(18,6) NULL,
  pre_close DECIMAL(18,6) NULL,
  pct_change DECIMAL(18,8) NULL,
  adj_factor DECIMAL(24,12) NULL,
  source_name VARCHAR(32) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  fetched_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  UNIQUE KEY uq_market_bar_content (instrument_id, trade_date, source_name, content_hash),
  KEY ix_market_bar_date (instrument_id, trade_date, revision_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trading_event_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  security_id VARCHAR(32) NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  event_reason VARCHAR(64) NOT NULL DEFAULT 'unknown',
  effective_date CHAR(8) NOT NULL,
  effective_to CHAR(8) NOT NULL DEFAULT '',
  evidence_ids JSON NOT NULL,
  confirmation_status VARCHAR(32) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  KEY ix_trading_event_scope (security_id, effective_date, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_bundle (
  rule_version VARCHAR(128) NOT NULL,
  schema_version VARCHAR(32) NOT NULL,
  config_json JSON NOT NULL,
  evidence_ids JSON NOT NULL,
  content_hash CHAR(64) NOT NULL,
  review_status VARCHAR(32) NOT NULL,
  reviewed_by VARCHAR(128) NOT NULL DEFAULT '',
  reviewed_at DATETIME(3) NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (rule_version),
  UNIQUE KEY uq_rule_bundle_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_batch (
  batch_id BIGINT NOT NULL AUTO_INCREMENT,
  source_name VARCHAR(32) NOT NULL,
  batch_kind VARCHAR(32) NOT NULL,
  scope_hash CHAR(64) NOT NULL,
  request_fingerprint CHAR(64) NOT NULL,
  run_revision INT NOT NULL,
  watermark VARCHAR(128) NOT NULL DEFAULT '',
  page_state JSON NOT NULL,
  coverage_status VARCHAR(32) NOT NULL,
  input_hash CHAR(64) NOT NULL,
  started_at DATETIME(3) NOT NULL,
  finished_at DATETIME(3) NULL,
  error_code VARCHAR(64) NOT NULL DEFAULT '',
  PRIMARY KEY (batch_id),
  UNIQUE KEY uq_source_batch_run (source_name, request_fingerprint, run_revision),
  KEY ix_source_batch_scope (source_name, batch_kind, coverage_status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS announcement_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  source_name VARCHAR(32) NOT NULL,
  source_ann_id VARCHAR(128) NOT NULL,
  publisher VARCHAR(128) NOT NULL DEFAULT '',
  security_id VARCHAR(32) NOT NULL DEFAULT '',
  published_at DATETIME(3) NULL,
  source_url TEXT NOT NULL,
  title VARCHAR(512) NOT NULL,
  body_text MEDIUMTEXT NOT NULL,
  body_hash CHAR(64) NOT NULL,
  parser_version VARCHAR(64) NOT NULL DEFAULT '',
  source_batch_id BIGINT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  UNIQUE KEY uq_announcement_revision (source_name, source_ann_id, body_hash),
  KEY ix_announcement_security_date (security_id, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fact_revision (
  revision_id BIGINT NOT NULL AUTO_INCREMENT,
  fact_id CHAR(36) NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  fact_type VARCHAR(48) NOT NULL,
  fact_start_date CHAR(8) NOT NULL DEFAULT '',
  fact_end_date CHAR(8) NOT NULL DEFAULT '',
  direction VARCHAR(8) NOT NULL DEFAULT 'unknown',
  valid_from CHAR(8) NOT NULL DEFAULT '',
  valid_to CHAR(8) NOT NULL DEFAULT '',
  fact_status VARCHAR(32) NOT NULL,
  supersedes_revision_id BIGINT NULL,
  identity_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (revision_id),
  KEY ix_fact_identity (fact_id, revision_id),
  KEY ix_fact_window (security_id, fact_end_date, fact_type),
  KEY ix_fact_validity (security_id, valid_from, valid_to, fact_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fact_evidence (
  fact_revision_id BIGINT NOT NULL,
  announcement_revision_id BIGINT NOT NULL,
  locator VARCHAR(255) NOT NULL DEFAULT '',
  locator_hash CHAR(64) NOT NULL,
  excerpt_hash CHAR(64) NOT NULL,
  PRIMARY KEY (fact_revision_id, announcement_revision_id, locator_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS input_manifest (
  manifest_id BIGINT NOT NULL AUTO_INCREMENT,
  root_hash CHAR(64) NOT NULL,
  knowledge_cutoff DATETIME(3) NOT NULL,
  component_refs JSON NOT NULL,
  sealed_at DATETIME(3) NOT NULL,
  PRIMARY KEY (manifest_id),
  UNIQUE KEY uq_manifest_root (root_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset (
  dataset_id BIGINT NOT NULL AUTO_INCREMENT,
  dataset_version VARCHAR(128) NOT NULL,
  target_date CHAR(8) NOT NULL,
  rule_version VARCHAR(128) NOT NULL,
  code_revision VARCHAR(64) NOT NULL,
  parser_revision VARCHAR(64) NOT NULL,
  manifest_id BIGINT NOT NULL,
  lifecycle VARCHAR(24) NOT NULL,
  pool_version VARCHAR(64) NOT NULL DEFAULT '',
  pool_hash CHAR(64) NOT NULL DEFAULT '',
  result_hash CHAR(64) NOT NULL DEFAULT '',
  validation_summary JSON NOT NULL,
  created_at DATETIME(3) NOT NULL,
  PRIMARY KEY (dataset_id),
  UNIQUE KEY uq_dataset_version (dataset_version),
  KEY ix_dataset_target (target_date, lifecycle, dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset_fact_ref (
  dataset_id BIGINT NOT NULL,
  fact_revision_id BIGINT NOT NULL,
  purpose VARCHAR(32) NOT NULL,
  selection_status VARCHAR(24) NOT NULL,
  PRIMARY KEY (dataset_id, fact_revision_id, purpose)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS same_direction_event (
  dataset_id BIGINT NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  fact_id CHAR(36) NOT NULL,
  fact_revision_id BIGINT NOT NULL,
  fact_end_date CHAR(8) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  direction_status VARCHAR(24) NOT NULL,
  direction_basis VARCHAR(64) NOT NULL DEFAULT '',
  direction_reasons JSON NOT NULL,
  market_check_status VARCHAR(24) NOT NULL,
  calculation_input_hash CHAR(64) NOT NULL DEFAULT '',
  PRIMARY KEY (dataset_id, fact_id),
  KEY ix_same_direction_window (dataset_id, security_id, fact_end_date, direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS reset_boundary (
  reset_boundary_id BIGINT NOT NULL AUTO_INCREMENT,
  dataset_id BIGINT NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  metric_keys JSON NOT NULL,
  boundary_key CHAR(64) NOT NULL,
  trigger_fact_revision_ids JSON NOT NULL,
  source_evidence_ids JSON NOT NULL,
  policy_id BIGINT NULL,
  policy_version VARCHAR(128) NOT NULL,
  trigger_trading_date CHAR(8) NOT NULL DEFAULT '',
  source_published_at DATETIME(3) NULL,
  effective_date CHAR(8) NOT NULL,
  effective_date_basis VARCHAR(64) NOT NULL,
  first_return_date CHAR(8) NOT NULL DEFAULT '',
  baseline_date CHAR(8) NOT NULL DEFAULT '',
  baseline_price_ref VARCHAR(64) NOT NULL DEFAULT '',
  baseline_index_ref VARCHAR(64) NOT NULL DEFAULT '',
  clear_events_before TINYINT NOT NULL DEFAULT 0,
  market_check_status VARCHAR(24) NOT NULL,
  market_input_hash CHAR(64) NOT NULL DEFAULT '',
  decision_status VARCHAR(24) NOT NULL,
  supersedes_boundary_id BIGINT NULL,
  decision_hash CHAR(64) NOT NULL,
  recorded_at DATETIME(3) NOT NULL,
  PRIMARY KEY (reset_boundary_id),
  UNIQUE KEY uq_reset_boundary (dataset_id, security_id, boundary_key),
  KEY ix_reset_effective_date (dataset_id, security_id, effective_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS metric_result (
  dataset_id BIGINT NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  rule_key VARCHAR(48) NOT NULL,
  date_role VARCHAR(8) NOT NULL,
  target_date CHAR(8) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  metric_value DECIMAL(20,8) NULL,
  threshold_value DECIMAL(20,8) NULL,
  result_status VARCHAR(24) NOT NULL,
  availability VARCHAR(16) NOT NULL,
  reasons JSON NOT NULL,
  evidence_refs JSON NOT NULL,
  PRIMARY KEY (dataset_id, security_id, rule_key, date_role, direction),
  KEY ix_metric_query (dataset_id, date_role, rule_key, result_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stock_result (
  dataset_id BIGINT NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  trade_state VARCHAR(24) NOT NULL,
  regulatory_state VARCHAR(24) NOT NULL,
  current_summary JSON NOT NULL,
  next_summary JSON NOT NULL,
  monitor_summary JSON NOT NULL,
  simulation_basis JSON NOT NULL,
  PRIMARY KEY (dataset_id, security_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_run (
  job_id CHAR(36) NOT NULL,
  job_kind VARCHAR(32) NOT NULL,
  dedupe_key CHAR(64) NOT NULL,
  scope_json JSON NOT NULL,
  stage VARCHAR(32) NOT NULL,
  state VARCHAR(24) NOT NULL,
  owner_id VARCHAR(64) NOT NULL DEFAULT '',
  fence BIGINT NOT NULL DEFAULT 0,
  lease_until DATETIME(3) NULL,
  attempt INT NOT NULL DEFAULT 0,
  next_retry_at DATETIME(3) NULL,
  error_code VARCHAR(64) NOT NULL DEFAULT '',
  result_ref VARCHAR(128) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  PRIMARY KEY (job_id),
  UNIQUE KEY uq_job_dedupe (job_kind, dedupe_key),
  KEY ix_job_claim (state, next_retry_at, lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS realtime_scope_fence (
  view_scope_key CHAR(64) NOT NULL,
  monotonic_fence BIGINT NOT NULL,
  owner_job_id CHAR(36) NOT NULL,
  active_realtime_view_id CHAR(36) NOT NULL DEFAULT '',
  active_generation BIGINT NOT NULL DEFAULT 0,
  context_json JSON NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  PRIMARY KEY (view_scope_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS idempotency_record (
  scope_key CHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  request_fingerprint CHAR(64) NOT NULL,
  first_context JSON NULL,
  job_id CHAR(36) NOT NULL DEFAULT '',
  response_ref VARCHAR(128) NOT NULL DEFAULT '',
  record_state VARCHAR(24) NOT NULL,
  created_at DATETIME(3) NOT NULL,
  expires_at DATETIME(3) NOT NULL,
  PRIMARY KEY (scope_key, idempotency_key),
  KEY ix_idempotency_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS realtime_view (
  realtime_view_id CHAR(36) NOT NULL,
  view_scope_key CHAR(64) NOT NULL,
  generation BIGINT NOT NULL,
  dataset_id BIGINT NOT NULL,
  context_json JSON NOT NULL,
  coverage_json JSON NOT NULL,
  results_json JSON NOT NULL,
  quote_as_of DATETIME(3) NULL,
  fresh_until DATETIME(3) NOT NULL,
  stale_until DATETIME(3) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  created_at DATETIME(3) NOT NULL,
  PRIMARY KEY (realtime_view_id),
  UNIQUE KEY uq_realtime_view_generation (view_scope_key, generation),
  KEY ix_realtime_view_expiry (stale_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS quality_result (
  quality_result_id BIGINT NOT NULL AUTO_INCREMENT,
  dataset_id BIGINT NOT NULL,
  validation_run_id CHAR(36) NOT NULL,
  gate_id VARCHAR(16) NOT NULL,
  gate_config_hash CHAR(64) NOT NULL,
  scope_hash CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  measured JSON NOT NULL,
  threshold JSON NOT NULL,
  artifact_ref VARCHAR(255) NOT NULL DEFAULT '',
  reviewed_by VARCHAR(128) NOT NULL DEFAULT '',
  reviewed_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL,
  PRIMARY KEY (quality_result_id),
  UNIQUE KEY uq_quality_gate (dataset_id, validation_run_id, gate_id),
  KEY ix_quality_status (dataset_id, status, gate_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS outbox_event (
  event_id CHAR(36) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  payload_ref VARCHAR(255) NOT NULL,
  created_at DATETIME(3) NOT NULL,
  delivered_at DATETIME(3) NULL,
  attempt INT NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id),
  KEY ix_outbox_delivery (delivered_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_event (
  audit_id BIGINT NOT NULL AUTO_INCREMENT,
  action VARCHAR(64) NOT NULL,
  actor_role VARCHAR(32) NOT NULL,
  target_ref VARCHAR(255) NOT NULL,
  reason VARCHAR(512) NOT NULL DEFAULT '',
  before_hash CHAR(64) NOT NULL DEFAULT '',
  after_hash CHAR(64) NOT NULL DEFAULT '',
  external_ref VARCHAR(255) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL,
  PRIMARY KEY (audit_id),
  KEY ix_audit_target (target_ref, created_at),
  KEY ix_audit_action (action, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
