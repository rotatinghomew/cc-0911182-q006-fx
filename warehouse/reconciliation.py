import json
from .money import converted_entries
from .storage import canonical


def reconcile(connection,run_id,base,known_at):
    prior=connection.execute('SELECT base_currency,known_at,rows_json,gaps FROM reconciliation_runs WHERE run_id=?',[run_id]).fetchone()
    if prior:
        if prior[:2]!=(base,known_at):
            raise ValueError('核对标识已经用于另一口径')
        return {'rows':json.loads(prior[2]),'gaps':prior[3]}
    rows=converted_entries(connection,base,known_at)
    serialized=json.loads(json.dumps(rows,ensure_ascii=False,default=str))
    gaps=sum(row['converted'] is None for row in rows)
    connection.execute('INSERT INTO reconciliation_runs VALUES (?,?,?,?,?)',[run_id,base,known_at,canonical(serialized),gaps])
    return {'rows':serialized,'gaps':gaps}
