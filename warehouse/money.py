from datetime import datetime
from decimal import Decimal,ROUND_HALF_EVEN
from .storage import records


def source_totals(connection):
    return records(connection,'SELECT partner,currency,sum(amount) amount,count(*) entries FROM revenue GROUP BY partner,currency ORDER BY partner,currency')


def converted_entries(connection,base='USD',known_at=None):
    rates=records(connection,'SELECT * FROM rates WHERE base_currency=?',[base])
    digits=dict(connection.execute('SELECT code,minor_digits FROM currencies').fetchall())
    if base not in digits:
        raise ValueError('本位币精度未知')
    result=[]
    for entry in records(connection,'SELECT * FROM revenue ORDER BY entry_id'):
        choices=[rate for rate in rates if rate['currency']==entry['currency'] and rate['rate_day']==entry['business_day']]
        if known_at:
            cutoff=datetime.fromisoformat(known_at.replace('Z','+00:00'))
            choices=[rate for rate in choices if datetime.fromisoformat(rate['published_at'])<=cutoff]
        chosen=max(choices,key=lambda rate:(datetime.fromisoformat(rate['published_at']),rate['rate_id'])) if choices else None
        if entry['currency']==base:
            amount=entry['amount']
            rate_id='identity'
        elif chosen:
            amount=entry['amount']*chosen['rate']
            rate_id=chosen['rate_id']
        else:
            amount=None
            rate_id=None
        entry.update(base_currency=base,rate_id=rate_id,converted=amount.quantize(Decimal(1).scaleb(-digits[base]),rounding=ROUND_HALF_EVEN) if amount is not None else None)
        result.append(entry)
    return result
