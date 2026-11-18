from .closing import current_version, diff_report, entry_lineage, version_report
from .money import source_totals, converted_entries


def overview(connection):
    return {'original': source_totals(connection), 'converted': converted_entries(connection)}


def close_overview(connection, period=None):
    """关账查询入口：默认汇总全部已冻结版本，可按月份过滤。"""
    if period is None:
        from .storage import records
        return {'versions': records(connection,
                'SELECT * FROM close_versions ORDER BY period,created_at,version_id')}
    return {
        'current': current_version(connection, period),
        'approved_versions': version_chain(connection, period),
    }


def version_chain(connection, period):
    from .storage import records
    return records(connection,
                   'SELECT * FROM close_versions WHERE period=? ORDER BY created_at,version_id', [period])
