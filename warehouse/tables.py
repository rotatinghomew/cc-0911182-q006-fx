TABLES = {'currencies': (['code', 'minor_digits'], ['code']), 'revenue': (['entry_id', 'original_id', 'business_day', 'received_at', 'currency', 'amount', 'kind', 'partner'], ['entry_id']), 'rates': (['rate_id', 'rate_day', 'currency', 'base_currency', 'rate', 'published_at'], ['rate_id'])}

# 关账与重述事实单独登记，导出顺序保证版本头先于明细
CLOSING_TABLES = {
    'close_versions': (['version_id', 'period', 'base_currency', 'version_kind', 'status',
                        'superseded_by', 'parent_version_id', 'rate_known_at', 'created_at',
                        'digest', 'gaps'], ['version_id']),
    'close_entries': (['version_id', 'entry_id', 'original_id', 'business_day', 'business_period',
                       'currency', 'original_amount', 'operating_rate_id', 'operating_raw',
                       'operating_amount', 'settlement_rate_id', 'settlement_raw',
                       'settlement_amount', 'operating_gap', 'settlement_gap'], ['version_id', 'entry_id']),
    'close_totals': (['version_id', 'currency', 'original_total', 'operating_rounded_sum',
                      'operating_raw_sum', 'operating_rounded_total', 'operating_rounding_delta',
                      'settlement_rounded_sum', 'settlement_raw_sum', 'settlement_rounded_total',
                      'settlement_rounding_delta', 'gap_entries'], ['version_id', 'currency']),
    'restatement_diffs': (['version_id', 'entry_id', 'currency', 'change_kind', 'prior_version_id',
                           'original_delta', 'operating_delta', 'settlement_delta',
                           'prior_operating_rate_id', 'restated_operating_rate_id',
                           'prior_settlement_rate_id', 'restated_settlement_rate_id'],
                          ['version_id', 'entry_id']),
    'cross_period_moves': (['version_id', 'entry_id', 'source_period', 'settlement_period',
                            'partner', 'currency', 'original_amount'], ['version_id', 'entry_id']),
}
