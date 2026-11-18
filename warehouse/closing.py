"""关账与重述。

三种金额口径在同一版本内并列冻结，互不覆盖：
- original_amount：合作方报送的原币金额；
- operating_*：交易日官方汇率折算的经营口径；
- settlement_*：所属月份月末汇率折算的结算口径。

approved 版本一经生成即冻结；重述只产生 proposed 新版本和差额事实，
confirm 之后才成为现行版本，旧版本转为 superseded 但数字原样保留。
缺汇率保留 NULL 并打 gap 标记，绝不按一比一补值。
"""
import calendar
import re
from decimal import Decimal, ROUND_HALF_EVEN

from .checks import moment
from .money import pick_rate, rate_book
from .storage import canonical, records, transaction

PERIOD_RE = re.compile(r'^(\d{4})-(\d{2})$')
ZERO = Decimal('0')


def _check_period(period):
    match = PERIOD_RE.fullmatch(period or '')
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ValueError('会计月份格式应为 YYYY-MM')
    year, month = int(match.group(1)), int(match.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return f'{year:04d}-{month:02d}', f'{year:04d}-{month:02d}-{last_day:02d}'


def _attributed_period(connection, entry):
    """更正/退款的经济归属跟随原单；普通收入单归属自身业务月。"""
    if not entry['original_id']:
        return entry['business_day'][:7]
    row = connection.execute('SELECT business_day FROM revenue WHERE entry_id=?',
                             [entry['original_id']]).fetchone()
    if row is None:
        raise ValueError('更正记录引用的原单不存在：' + entry['entry_id'])
    return row[0][:7]


def _quantum(digits):
    return Decimal(1).scaleb(-digits)


def _period_entries(connection, period, include_late_adjustments):
    """关账只取本业务月的事实；重述还要纳入日后到达、指向本月原单的更正/退款。"""
    if not include_late_adjustments:
        return records(connection, "SELECT * FROM revenue WHERE substr(business_day,1,7)=? ORDER BY entry_id",
                       [period])
    return records(connection, """
        SELECT * FROM revenue
        WHERE substr(business_day,1,7)=?
           OR (original_id IS NOT NULL AND original_id <> ''
               AND substr(business_day,1,7)<>?
               AND EXISTS (SELECT 1 FROM revenue o
                           WHERE o.entry_id=revenue.original_id AND substr(o.business_day,1,7)=?))
        ORDER BY entry_id""", [period, period, period])


def _convert(entry, rate_day, book, base, base_quantum, known_at):
    """返回 (rate_id, 未取整折算, 取整折算, 是否缺口)。"""
    if entry['currency'] == base:
        converted = entry['amount']
        return 'identity', converted, converted.quantize(base_quantum, rounding=ROUND_HALF_EVEN), False
    rate = pick_rate(book, entry['currency'], rate_day, base, known_at)
    if rate is None:
        return None, None, None, True
    raw = entry['amount'] * rate['rate']
    return rate['rate_id'], raw, raw.quantize(base_quantum, rounding=ROUND_HALF_EVEN), False


def _snapshot(connection, period, period_end, base, known_at, include_late_adjustments=False):
    entries = _period_entries(connection, period, include_late_adjustments)
    if not entries:
        raise ValueError('该月份没有收入事实，不能关账：' + period)
    digits = dict(connection.execute('SELECT code,minor_digits FROM currencies').fetchall())
    if base not in digits:
        raise ValueError('本位币精度未知')
    base_quantum = _quantum(digits[base])
    book = rate_book(connection)
    rows, gaps = [], 0
    for entry in entries:
        if entry['currency'] not in digits:
            raise ValueError('币种精度未知：' + entry['currency'])
        # 经济归属月跟随原单，仅用于标记与跨月链接；
        # 结算口径统一采用本版本所在月的月末汇率（结算在哪个月就用哪个月的月结口径）
        attributed_period = _attributed_period(connection, entry)
        op_rate, op_raw, op_amount, op_gap = _convert(
            entry, entry['business_day'], book, base, base_quantum, known_at)
        st_rate, st_raw, st_amount, st_gap = _convert(
            entry, period_end, book, base, base_quantum, known_at)
        gaps += int(op_gap or st_gap)
        rows.append({
            'entry_id': entry['entry_id'],
            'original_id': entry['original_id'] or None,
            'business_day': entry['business_day'],
            'business_period': attributed_period,
            'currency': entry['currency'],
            'original_amount': entry['amount'].quantize(_quantum(digits[entry['currency']]),
                                                        rounding=ROUND_HALF_EVEN),
            'operating_rate_id': op_rate,
            'operating_raw': op_raw,
            'operating_amount': op_amount,
            'settlement_rate_id': st_rate,
            'settlement_raw': st_raw,
            'settlement_amount': st_amount,
            'operating_gap': op_gap,
            'settlement_gap': st_gap,
        })
    totals = _totals(rows, digits, base_quantum)
    return rows, totals, gaps


def _totals(rows, digits, base_quantum):
    """逐笔取整合计、未取整汇总及其再取整并列，差额恒等于两者之差。"""
    totals = []
    for currency in sorted({row['currency'] for row in rows}):
        group = [row for row in rows if row['currency'] == currency]
        original_total = sum((row['original_amount'] for row in group), ZERO)
        op_rounded_sum = sum((row['operating_amount'] for row in group if not row['operating_gap']), ZERO)
        op_raw_sum = sum((row['operating_raw'] for row in group if not row['operating_gap']), ZERO)
        op_rounded_total = op_raw_sum.quantize(base_quantum, rounding=ROUND_HALF_EVEN)
        st_rounded_sum = sum((row['settlement_amount'] for row in group if not row['settlement_gap']), ZERO)
        st_raw_sum = sum((row['settlement_raw'] for row in group if not row['settlement_gap']), ZERO)
        st_rounded_total = st_raw_sum.quantize(base_quantum, rounding=ROUND_HALF_EVEN)
        totals.append({
            'currency': currency,
            'original_total': original_total.quantize(_quantum(digits[currency]), rounding=ROUND_HALF_EVEN),
            'operating_rounded_sum': op_rounded_sum,
            'operating_raw_sum': op_raw_sum,
            'operating_rounded_total': op_rounded_total,
            'operating_rounding_delta': op_rounded_sum - op_rounded_total,
            'settlement_rounded_sum': st_rounded_sum,
            'settlement_raw_sum': st_raw_sum,
            'settlement_rounded_total': st_rounded_total,
            'settlement_rounding_delta': st_rounded_sum - st_rounded_total,
            'gap_entries': sum(1 for row in group if row['operating_gap'] or row['settlement_gap']),
        })
    return totals


def _cross_period_moves(connection, rows):
    """经济归属月与自身业务月不一致的调整：两侧月份各自承担什么，显式链接而非并改两月总数。

    关账版本：当月处理的、指向早月原单的更正/退款（settlement_period=自身业务月）。
    重述版本：重述来源月时纳入的后期调整（source_period=被重述的归属月）。
    """
    moves = []
    for row in rows:
        if not row['original_id']:
            continue
        own_period = row['business_day'][:7]
        source_period = row['business_period']
        if own_period == source_period:
            continue
        partner = connection.execute('SELECT partner FROM revenue WHERE entry_id=?',
                                     [row['original_id']]).fetchone()
        if partner is None:
            raise ValueError('更正记录引用的原单不存在：' + row['entry_id'])
        moves.append({
            'entry_id': row['entry_id'],
            'source_period': source_period,
            'settlement_period': own_period,
            'partner': partner[0],
            'currency': row['currency'],
            'original_amount': row['original_amount'],
        })
    return moves


def _persist_version(connection, version_id, period, base, kind, status, known_at,
                     created_at, parent, rows, totals, gaps, moves):
    digest = canonical({
        'entries': [{key: (str(value) if isinstance(value, Decimal) else value)
                     for key, value in row.items()} for row in rows],
        'totals': [{key: (str(value) if isinstance(value, Decimal) else value)
                    for key, value in row.items()} for row in totals],
    })
    connection.execute(
        'INSERT INTO close_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        [version_id, period, base, kind, status, None, parent, known_at, created_at, digest, gaps])
    for row in rows:
        connection.execute(
            'INSERT INTO close_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [version_id, row['entry_id'], row['original_id'], row['business_day'], row['business_period'],
             row['currency'], row['original_amount'], row['operating_rate_id'], row['operating_raw'],
             row['operating_amount'], row['settlement_rate_id'], row['settlement_raw'],
             row['settlement_amount'], row['operating_gap'], row['settlement_gap']])
    for total in totals:
        connection.execute('INSERT INTO close_totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           [version_id, total['currency'], total['original_total'],
                            total['operating_rounded_sum'], total['operating_raw_sum'],
                            total['operating_rounded_total'], total['operating_rounding_delta'],
                            total['settlement_rounded_sum'], total['settlement_raw_sum'],
                            total['settlement_rounded_total'], total['settlement_rounding_delta'],
                            total['gap_entries']])
    for move in moves:
        connection.execute('INSERT INTO cross_period_moves VALUES (?,?,?,?,?,?,?)',
                           [version_id, move['entry_id'], move['source_period'], move['settlement_period'],
                            move['partner'], move['currency'], move['original_amount']])
    return digest


def close_period(connection, close_id, period, base, known_at, created_at=None):
    """冻结一个月份的首次关账版本；同标识重放必须同口径，不重复生成。"""
    period, period_end = _check_period(period)
    known_at = moment(known_at)
    created_at = moment(created_at) if created_at else _now()
    prior = connection.execute('SELECT * FROM close_versions WHERE version_id=?', [close_id]).fetchone()
    if prior:
        if (prior[1], prior[2], prior[3], prior[7]) != (period, base, 'close', known_at):
            raise ValueError('关账标识已经用于另一口径')
        return {'version_id': close_id, 'replayed': True, 'status': prior[4]}
    if connection.execute("SELECT 1 FROM close_versions WHERE period=? AND status='approved'",
                          [period]).fetchone():
        raise ValueError('该月份已有关账版本，变化须走重述')
    rows, totals, gaps = _snapshot(connection, period, period_end, base, known_at)
    with transaction(connection):
        moves = _cross_period_moves(connection, rows)
        digest = _persist_version(connection, close_id, period, base, 'close', 'approved',
                                  known_at, created_at, None, rows, totals, gaps, moves)
    return {'version_id': close_id, 'period': period, 'status': 'approved',
            'entries': len(rows), 'gaps': gaps, 'digest': digest, 'replayed': False}


def restate_period(connection, restatement_id, period, base, known_at, created_at=None):
    """按新资料重述已关账月份：生成 proposed 版本与差额，不触碰已批准数字。"""
    period, period_end = _check_period(period)
    known_at = moment(known_at)
    created_at = moment(created_at) if created_at else _now()
    prior = connection.execute('SELECT * FROM close_versions WHERE version_id=?',
                               [restatement_id]).fetchone()
    if prior:
        if (prior[1], prior[2], prior[3], prior[7]) != (period, base, 'restatement', known_at):
            raise ValueError('重述标识已经用于另一口径')
        return {'version_id': restatement_id, 'replayed': True, 'status': prior[4]}
    parent_row = connection.execute(
        "SELECT version_id FROM close_versions WHERE period=? AND status='approved' ORDER BY created_at DESC",
        [period]).fetchone()
    if parent_row is None:
        raise ValueError('该月份尚无批准关账，不能重述')
    if connection.execute(
            "SELECT 1 FROM close_versions WHERE period=? AND status='proposed'", [period]).fetchone():
        raise ValueError('该月份已有待确认重述，请先确认或作废')
    parent_id = parent_row[0]
    rows, totals, gaps = _snapshot(connection, period, period_end, base, known_at,
                                   include_late_adjustments=True)
    diffs = _build_diffs(connection, parent_id, rows)
    with transaction(connection):
        moves = _cross_period_moves(connection, rows)
        digest = _persist_version(connection, restatement_id, period, base, 'restatement', 'proposed',
                                  known_at, created_at, parent_id, rows, totals, gaps, moves)
        for diff in diffs:
            connection.execute('INSERT INTO restatement_diffs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                               [restatement_id, diff['entry_id'], diff['currency'], diff['change_kind'],
                                parent_id, diff['original_delta'], diff['operating_delta'],
                                diff['settlement_delta'], diff['prior_operating_rate_id'],
                                diff['restated_operating_rate_id'], diff['prior_settlement_rate_id'],
                                diff['restated_settlement_rate_id']])
    return {'version_id': restatement_id, 'period': period, 'status': 'proposed',
            'parent_version_id': parent_id, 'entries': len(rows), 'changed_entries': len(diffs),
            'gaps': gaps, 'digest': digest, 'replayed': False}


def _build_diffs(connection, parent_id, rows):
    prior = {row['entry_id']: row for row in records(
        connection, 'SELECT * FROM close_entries WHERE version_id=?', [parent_id])}
    diffs = []
    for row in rows:
        old = prior.get(row['entry_id'])
        if old is None:
            kind = 'new'
            original_delta = row['original_amount']
            op_delta = None if row['operating_gap'] else row['operating_amount']
            st_delta = None if row['settlement_gap'] else row['settlement_amount']
            prior_op = prior_st = None
        else:
            original_delta = row['original_amount'] - old['original_amount']
            op_delta = None if (row['operating_gap'] or old['operating_gap']) \
                else row['operating_amount'] - old['operating_amount']
            st_delta = None if (row['settlement_gap'] or old['settlement_gap']) \
                else row['settlement_amount'] - old['settlement_amount']
            prior_op, prior_st = old['operating_rate_id'], old['settlement_rate_id']
            if old['operating_gap'] != row['operating_gap'] or old['settlement_gap'] != row['settlement_gap']:
                kind = 'gap_opened' if (row['operating_gap'] or row['settlement_gap']) else 'gap_filled'
            elif original_delta != 0:
                kind = 'amount'
            elif (old['operating_rate_id'] != row['operating_rate_id']
                  or old['settlement_rate_id'] != row['settlement_rate_id']):
                kind = 'rate'
            elif original_delta == 0 and op_delta == 0 and st_delta == 0:
                continue
            else:
                kind = 'rate'
        diffs.append({
            'entry_id': row['entry_id'], 'currency': row['currency'], 'change_kind': kind,
            'original_delta': original_delta, 'operating_delta': op_delta, 'settlement_delta': st_delta,
            'prior_operating_rate_id': prior_op,
            'restated_operating_rate_id': row['operating_rate_id'],
            'prior_settlement_rate_id': prior_st,
            'restated_settlement_rate_id': row['settlement_rate_id'],
        })
    return diffs


def confirm_restatement(connection, restatement_id):
    """确认重述：旧批准版本冻结转 superseded，proposed 成为现行 approved；重复确认不产生新差额。"""
    row = connection.execute(
        'SELECT status,parent_version_id,period FROM close_versions WHERE version_id=?',
        [restatement_id]).fetchone()
    if row is None:
        raise ValueError('重述版本不存在')
    status, parent_id, period = row
    if status == 'approved':
        return {'version_id': restatement_id, 'period': period, 'replayed': True, 'status': status}
    if status != 'proposed':
        raise ValueError('只有待确认的重述可以确认')
    with transaction(connection):
        connection.execute(
            "UPDATE close_versions SET status='superseded',superseded_by=? WHERE version_id=?",
            [restatement_id, parent_id])
        connection.execute("UPDATE close_versions SET status='approved' WHERE version_id=?",
                           [restatement_id])
    return {'version_id': restatement_id, 'period': period, 'replayed': False,
            'status': 'approved', 'superseded': parent_id}


def current_version(connection, period):
    period, _ = _check_period(period)
    row = connection.execute(
        "SELECT * FROM close_versions WHERE period=? AND status='approved' ORDER BY created_at DESC",
        [period]).fetchone()
    if row is None:
        return None
    return _version_header(row)


def version_report(connection, version_id):
    row = connection.execute('SELECT * FROM close_versions WHERE version_id=?', [version_id]).fetchone()
    if row is None:
        raise ValueError('版本不存在')
    return {
        'version': _version_header(row),
        'totals': records(connection, 'SELECT * FROM close_totals WHERE version_id=? ORDER BY currency',
                          [version_id]),
        'cross_period_moves': records(
            connection, 'SELECT * FROM cross_period_moves WHERE version_id=? ORDER BY entry_id',
            [version_id]),
    }


def diff_report(connection, version_id):
    """差额追溯：每条差额带原单、前后汇率与版本标识。"""
    return records(connection, """
        SELECT d.version_id, d.prior_version_id, d.entry_id, e.business_day, e.business_period,
               d.currency, d.change_kind, d.original_delta, d.operating_delta, d.settlement_delta,
               d.prior_operating_rate_id, d.restated_operating_rate_id,
               d.prior_settlement_rate_id, d.restated_settlement_rate_id,
               e.original_amount, e.operating_amount, e.settlement_amount,
               e.operating_gap, e.settlement_gap
        FROM restatement_diffs d
        JOIN close_entries e ON e.version_id=d.version_id AND e.entry_id=d.entry_id
        WHERE d.version_id=? ORDER BY d.entry_id""", [version_id])


def entry_lineage(connection, entry_id):
    """从一笔原单穿过所有关账/重述版本，查看每个版本采用的汇率与三口径金额。"""
    return records(connection, """
        SELECT v.version_id, v.period, v.version_kind, v.status, v.rate_known_at,
               e.entry_id, e.original_id, e.business_day, e.currency, e.original_amount,
               e.operating_rate_id, e.operating_amount, e.operating_gap,
               e.settlement_rate_id, e.settlement_amount, e.settlement_gap
        FROM close_entries e JOIN close_versions v ON v.version_id=e.version_id
        WHERE e.entry_id=? ORDER BY v.created_at, v.version_id""", [entry_id])


def _version_header(row):
    return {
        'version_id': row[0], 'period': row[1], 'base_currency': row[2], 'version_kind': row[3],
        'status': row[4], 'superseded_by': row[5], 'parent_version_id': row[6],
        'rate_known_at': row[7], 'created_at': row[8], 'digest': row[9], 'gaps': row[10],
    }


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
