CREATE TABLE IF NOT EXISTS receipts(batch_id VARCHAR PRIMARY KEY, signature VARCHAR NOT NULL, inserted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_rows(table_name VARCHAR, row_key VARCHAR, payload VARCHAR NOT NULL, batch_id VARCHAR NOT NULL, PRIMARY KEY(table_name,row_key));
        CREATE TABLE IF NOT EXISTS currencies(code VARCHAR PRIMARY KEY, minor_digits INTEGER CHECK(minor_digits BETWEEN 0 AND 4));
        CREATE TABLE IF NOT EXISTS revenue(entry_id VARCHAR PRIMARY KEY, original_id VARCHAR, business_day VARCHAR, received_at VARCHAR, currency VARCHAR, amount DECIMAL(24,6), kind VARCHAR, partner VARCHAR);
        CREATE TABLE IF NOT EXISTS rates(rate_id VARCHAR PRIMARY KEY, rate_day VARCHAR, currency VARCHAR, base_currency VARCHAR, rate DECIMAL(24,10), published_at VARCHAR);

CREATE TABLE IF NOT EXISTS reconciliation_runs(run_id VARCHAR PRIMARY KEY, base_currency VARCHAR, known_at VARCHAR, rows_json VARCHAR, gaps INTEGER);

-- 关账版本：同一关账标识重放必须同口径；approved 版本的数字冻结，不被后续重述覆写
CREATE TABLE IF NOT EXISTS close_versions(
    version_id VARCHAR PRIMARY KEY,
    period VARCHAR NOT NULL,
    base_currency VARCHAR NOT NULL,
    version_kind VARCHAR NOT NULL CHECK(version_kind IN ('close','restatement')),
    status VARCHAR NOT NULL CHECK(status IN ('approved','proposed','superseded')),
    superseded_by VARCHAR,
    parent_version_id VARCHAR,
    rate_known_at VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    digest VARCHAR NOT NULL,
    gaps INTEGER NOT NULL,
    UNIQUE(period, version_kind, version_id)
);

-- 关账逐单事实：原币、经营口径（交易日汇率）、结算口径（月末汇率）三口径并列保留
CREATE TABLE IF NOT EXISTS close_entries(
    version_id VARCHAR NOT NULL,
    entry_id VARCHAR NOT NULL,
    original_id VARCHAR,
    business_day VARCHAR NOT NULL,
    business_period VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    original_amount DECIMAL(24,6) NOT NULL,
    operating_rate_id VARCHAR,
    operating_raw DECIMAL(24,10),
    operating_amount DECIMAL(24,10),
    settlement_rate_id VARCHAR,
    settlement_raw DECIMAL(24,10),
    settlement_amount DECIMAL(24,10),
    operating_gap BOOLEAN NOT NULL,
    settlement_gap BOOLEAN NOT NULL,
    PRIMARY KEY(version_id, entry_id)
);

-- 关账汇总：逐笔取整合计与汇总后取整并列，取整差额可直接核对
CREATE TABLE IF NOT EXISTS close_totals(
    version_id VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    original_total DECIMAL(24,6) NOT NULL,
    operating_rounded_sum DECIMAL(24,10) NOT NULL,
    operating_raw_sum DECIMAL(24,10) NOT NULL,
    operating_rounded_total DECIMAL(24,10) NOT NULL,
    operating_rounding_delta DECIMAL(24,10) NOT NULL,
    settlement_rounded_sum DECIMAL(24,10) NOT NULL,
    settlement_raw_sum DECIMAL(24,10) NOT NULL,
    settlement_rounded_total DECIMAL(24,10) NOT NULL,
    settlement_rounding_delta DECIMAL(24,10) NOT NULL,
    gap_entries INTEGER NOT NULL,
    PRIMARY KEY(version_id, currency)
);

-- 重述差额：结算人员可由差额追到原单、采用汇率与批准版本
CREATE TABLE IF NOT EXISTS restatement_diffs(
    version_id VARCHAR NOT NULL,
    entry_id VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    change_kind VARCHAR NOT NULL CHECK(change_kind IN ('rate','amount','new','gap_filled','gap_opened','none')),
    prior_version_id VARCHAR NOT NULL,
    original_delta DECIMAL(24,6) NOT NULL,
    operating_delta DECIMAL(24,10),
    settlement_delta DECIMAL(24,10),
    prior_operating_rate_id VARCHAR,
    restated_operating_rate_id VARCHAR,
    prior_settlement_rate_id VARCHAR,
    restated_settlement_rate_id VARCHAR,
    PRIMARY KEY(version_id, entry_id)
);

-- 跨月调整：来源月（按原单归属）与结算月（按更正自身业务日）各自承担什么变化，显式链接而非并改两月总数
CREATE TABLE IF NOT EXISTS cross_period_moves(
    version_id VARCHAR NOT NULL,
    entry_id VARCHAR NOT NULL,
    source_period VARCHAR NOT NULL,
    settlement_period VARCHAR NOT NULL,
    partner VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    original_amount DECIMAL(24,6) NOT NULL,
    PRIMARY KEY(version_id, entry_id)
);
