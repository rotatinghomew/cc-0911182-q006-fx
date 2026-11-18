from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN

from .storage import records


def rate_book(connection):
    """按币种与汇率日组织已发布汇率，选率规则集中在这里，经营口径与关账口径共用。"""
    rates = {}
    for rate in records(connection, 'SELECT * FROM rates'):
        rates.setdefault((rate['currency'], rate['rate_day']), []).append(rate)
    return rates


def pick_rate(book, currency, rate_day, base, known_at=None):
    """取指定汇率日在知晓时点前最后发布的官方汇率；无汇率返回 None（显式缺口，不做一比一假设）。"""
    choices = list(book.get((currency, rate_day), ()))
    if base is not None:
        choices = [rate for rate in choices if rate['base_currency'] == base]
    if known_at is not None:
        cutoff = datetime.fromisoformat(known_at.replace('Z', '+00:00'))
        choices = [rate for rate in choices
                   if datetime.fromisoformat(rate['published_at']) <= cutoff]
    if not choices:
        return None
    return max(choices, key=lambda rate: (datetime.fromisoformat(rate['published_at']), rate['rate_id']))


def source_totals(connection):
    return records(connection, 'SELECT partner,currency,sum(amount) amount,count(*) entries FROM revenue GROUP BY partner,currency ORDER BY partner,currency')


def converted_entries(connection, base='USD', known_at=None):
    book = rate_book(connection)
    digits = dict(connection.execute('SELECT code,minor_digits FROM currencies').fetchall())
    if base not in digits:
        raise ValueError('本位币精度未知')
    quantum = Decimal(1).scaleb(-digits[base])
    result = []
    for entry in records(connection, 'SELECT * FROM revenue ORDER BY entry_id'):
        if entry['currency'] == base:
            chosen = 'identity'
            amount = entry['amount']
        else:
            rate = pick_rate(book, entry['currency'], entry['business_day'], base, known_at)
            chosen = rate['rate_id'] if rate else None
            amount = entry['amount'] * rate['rate'] if rate else None
        entry.update(base_currency=base, rate_id=chosen,
                     converted=amount.quantize(quantum, rounding=ROUND_HALF_EVEN) if amount is not None else None)
        result.append(entry)
    return result
