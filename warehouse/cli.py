import argparse
import json
from pathlib import Path

from .closing import (close_period, confirm_restatement, diff_report,
                      entry_lineage, restate_period, version_report)
from .export import export_tables
from .ingest import ingest
from .queries import close_overview, overview
from .storage import open_store


def main():
    parser = argparse.ArgumentParser(description='本地仓库日常接收、核对、关账与重述')
    parser.add_argument('--database', default='warehouse.duckdb')
    commands = parser.add_subparsers(dest='action', required=True)
    load = commands.add_parser('load', help='接收一个业务包')
    load.add_argument('file', type=Path)
    commands.add_parser('overview', help='查看现有业务汇总')
    output = commands.add_parser('export', help='冻结列式导出')
    output.add_argument('destination', type=Path)
    output.add_argument('revision')

    close = commands.add_parser('close', help='关账：冻结三口径版本')
    close.add_argument('close_id')
    close.add_argument('period', help='会计月份 YYYY-MM')
    close.add_argument('--base', default='USD')
    close.add_argument('--known-at', required=True, help='汇率知晓截止时点（含时区）')

    restate = commands.add_parser('restate', help='重述：按新资料生成待确认版本与差额')
    restate.add_argument('restatement_id')
    restate.add_argument('period')
    restate.add_argument('--base', default='USD')
    restate.add_argument('--known-at', required=True)

    confirm = commands.add_parser('confirm', help='确认重述，旧版本冻结转 superseded')
    confirm.add_argument('restatement_id')

    report = commands.add_parser('report', help='查看版本汇总、取整差额与跨月调整')
    report.add_argument('version_id')

    diffs = commands.add_parser('diffs', help='从差额追到原单、汇率与批准版本')
    diffs.add_argument('version_id')

    lineage = commands.add_parser('lineage', help='查看单笔原单穿过的所有版本')
    lineage.add_argument('entry_id')

    closes = commands.add_parser('closes', help='查看关账版本清单')
    closes.add_argument('--period', default=None)

    args = parser.parse_args()
    connection = open_store(args.database)
    try:
        if args.action == 'load':
            result = ingest(connection, json.loads(args.file.read_text(encoding='utf-8')))
        elif args.action == 'export':
            result = export_tables(connection, args.destination, args.revision)
        elif args.action == 'overview':
            result = overview(connection)
        elif args.action == 'close':
            result = close_period(connection, args.close_id, args.period, args.base, args.known_at)
        elif args.action == 'restate':
            result = restate_period(connection, args.restatement_id, args.period,
                                    args.base, args.known_at)
        elif args.action == 'confirm':
            result = confirm_restatement(connection, args.restatement_id)
        elif args.action == 'report':
            result = version_report(connection, args.version_id)
        elif args.action == 'diffs':
            result = diff_report(connection, args.version_id)
        elif args.action == 'lineage':
            result = entry_lineage(connection, args.entry_id)
        else:
            result = close_overview(connection, args.period)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
