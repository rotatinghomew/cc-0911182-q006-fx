CREATE TABLE IF NOT EXISTS receipts(batch_id VARCHAR PRIMARY KEY, signature VARCHAR NOT NULL, inserted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_rows(table_name VARCHAR, row_key VARCHAR, payload VARCHAR NOT NULL, batch_id VARCHAR NOT NULL, PRIMARY KEY(table_name,row_key));
        CREATE TABLE IF NOT EXISTS currencies(code VARCHAR PRIMARY KEY, minor_digits INTEGER CHECK(minor_digits BETWEEN 0 AND 4));
        CREATE TABLE IF NOT EXISTS revenue(entry_id VARCHAR PRIMARY KEY, original_id VARCHAR, business_day VARCHAR, received_at VARCHAR, currency VARCHAR, amount DECIMAL(24,6), kind VARCHAR, partner VARCHAR);
        CREATE TABLE IF NOT EXISTS rates(rate_id VARCHAR PRIMARY KEY, rate_day VARCHAR, currency VARCHAR, base_currency VARCHAR, rate DECIMAL(24,10), published_at VARCHAR);

CREATE TABLE IF NOT EXISTS reconciliation_runs(run_id VARCHAR PRIMARY KEY, base_currency VARCHAR, known_at VARCHAR, rows_json VARCHAR, gaps INTEGER);