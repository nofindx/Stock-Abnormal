-- vNext foundation tables.  Run through the deployment migration tool; the
-- application must not execute DDL on startup.
CREATE TABLE IF NOT EXISTS rule_evidence (
  evidence_id BIGINT NOT NULL AUTO_INCREMENT,
  rule_version VARCHAR(128) NOT NULL,
  rule_key VARCHAR(128) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_title VARCHAR(255) NOT NULL,
  source_url TEXT NOT NULL,
  article_or_locator VARCHAR(255) NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  identity_hash CHAR(64) NOT NULL,
  effective_from CHAR(8) NOT NULL,
  effective_to CHAR(8) NOT NULL DEFAULT '',
  applicability_scope VARCHAR(255) NOT NULL DEFAULT '',
  review_status VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
  reviewed_by VARCHAR(128) NOT NULL DEFAULT '',
  reviewed_at DATETIME(3) NULL,
  PRIMARY KEY (evidence_id),
  UNIQUE KEY uq_rule_evidence_identity (identity_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS return_policy_revision (
  policy_id BIGINT NOT NULL AUTO_INCREMENT,
  rule_version VARCHAR(128) NOT NULL,
  board_scope VARCHAR(64) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  stock_return_method VARCHAR(128) NOT NULL,
  index_return_method VARCHAR(128) NOT NULL,
  comparison_base VARCHAR(128) NOT NULL,
  suspension_resume_policy VARCHAR(255) NOT NULL,
  ex_right_policy VARCHAR(255) NOT NULL,
  valid_from CHAR(8) NOT NULL,
  valid_to CHAR(8) NOT NULL DEFAULT '',
  evidence_ids JSON NOT NULL,
  review_status VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
  content_hash CHAR(64) NOT NULL,
  PRIMARY KEY (policy_id),
  KEY ix_return_policy_scope (rule_version, board_scope, valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_policy_revision (
  policy_id BIGINT NOT NULL AUTO_INCREMENT,
  version VARCHAR(128) NOT NULL,
  source_name VARCHAR(32) NOT NULL,
  content_hash CHAR(64) NOT NULL,
  market_phase VARCHAR(32) NOT NULL,
  freshness_seconds INT NOT NULL,
  stale_seconds INT NOT NULL,
  max_quote_skew_seconds INT NOT NULL,
  config_hash CHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
  created_at DATETIME(3) NOT NULL,
  PRIMARY KEY (policy_id),
  UNIQUE KEY uq_source_policy (version, source_name, market_phase, config_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset_pointer (
  environment_name VARCHAR(32) NOT NULL,
  channel VARCHAR(32) NOT NULL,
  dataset_id BIGINT NOT NULL,
  generation BIGINT NOT NULL,
  previous_id BIGINT NULL,
  changed_at DATETIME(3) NOT NULL,
  PRIMARY KEY (environment_name, channel),
  UNIQUE KEY uq_dataset_pointer_generation (environment_name, channel, generation)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
