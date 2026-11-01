-- Shared monitor facts, atomic publication/outbox and immutable authorized reads.
-- Apply with the explicit migration CLI or the production database console.
CREATE TABLE IF NOT EXISTS publication_record (
 publication_id VARCHAR(64) NOT NULL,
 publication_kind VARCHAR(16) NOT NULL,
 content_hash CHAR(64) NOT NULL,
 payload JSON NOT NULL,
 expires_at DOUBLE NULL,
 PRIMARY KEY (publication_id),
 KEY ix_publication_expiry (publication_kind, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS publication_state (
 state_key VARCHAR(128) NOT NULL,
 state_value TEXT NOT NULL,
 PRIMARY KEY (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS publication_fact (
 revision_id BIGINT NOT NULL AUTO_INCREMENT,
 source_id VARCHAR(64) NOT NULL,
 content_hash CHAR(64) NOT NULL,
 symbol VARCHAR(16) NOT NULL,
 announcement_date CHAR(8) NOT NULL,
 pending TINYINT NOT NULL,
 payload JSON NOT NULL,
 PRIMARY KEY (revision_id),
 UNIQUE KEY uq_publication_fact (source_id, content_hash),
 KEY ix_publication_fact_source (source_id, revision_id),
 KEY ix_publication_fact_symbol (symbol, announcement_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS publication_outbox (
 publication_id VARCHAR(64) NOT NULL,
 attempt INT NOT NULL DEFAULT 0,
 delivered TINYINT NOT NULL DEFAULT 0,
 last_error VARCHAR(255) NOT NULL DEFAULT '',
 PRIMARY KEY (publication_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS publication_refresh_job (
 job_id CHAR(64) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL,
 owner CHAR(32) NOT NULL DEFAULT '',
 lease_until DOUBLE NOT NULL DEFAULT 0,
 expires_at DOUBLE NOT NULL,
 result_view_id VARCHAR(64) NOT NULL DEFAULT '',
 PRIMARY KEY (job_id),
 KEY ix_refresh_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
