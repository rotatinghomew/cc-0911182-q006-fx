from datetime import date
from .checks import text,number,moment


def normalize(table,original):
    row=dict(original)
    for field,value in row.items():
        if field=='minor_digits':
            if type(value) is not int or not 0<=value<=4:
                raise ValueError('币种精度无效')
        elif field in {'amount','rate'}:
            row[field]=number(value,field=='rate')
        elif field.endswith('_day'):
            row[field]=date.fromisoformat(value).isoformat()
        elif field.endswith('_at'):
            row[field]=moment(value)
        elif field=='original_id' and value=='':
            continue
        else:
            text(value)
    if table=='rates' and float(row['rate'])<=0:
        raise ValueError('汇率必须为正')
    if table=='revenue' and row['kind'] not in {'sale','refund','correction'}:
        raise ValueError('收入类型无效')
    if table=='revenue' and row['kind']!='sale' and not row['original_id']:
        raise ValueError('更正与退款必须有原单')
    return row
