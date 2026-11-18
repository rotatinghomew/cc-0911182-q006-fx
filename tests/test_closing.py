import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from warehouse.closing import (close_period, confirm_restatement, current_version,
                                diff_report, entry_lineage, restate_period, version_report)
from warehouse.export import export_tables, read_export
from warehouse.ingest import ingest
from warehouse.storage import open_store, records

OCT_KNOWN = '2026-11-02T00:00:00Z'
LATE_KNOWN = '2026-11-15T00:00:00Z'


class ClosingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = open_store(self.root / 'store.duckdb')
        self.payload = json.loads(Path('samples/delivery.json').read_text())
        self.november = json.loads(Path('samples/november_update.json').read_text())

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def load(self, payload=None):
        return ingest(self.connection, copy.deepcopy(payload or self.payload))

    def entries(self, version_id):
        return {row['entry_id']: row for row in records(self.connection,
                'SELECT * FROM close_entries WHERE version_id=? ORDER BY entry_id', [version_id])}

    def totals(self, version_id):
        return {row['currency']: row for row in records(self.connection,
                'SELECT * FROM close_totals WHERE version_id=?', [version_id])}

    def test_close_freezes_three_bases_side_by_side(self):
        self.load()
        result = close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        self.assertEqual(result['status'], 'approved')
        row = self.entries('oct-close')['j1']
        self.assertEqual(row['original_amount'], Decimal('1001'))    # 原币（JPY 0 位小数）
        self.assertEqual(row['operating_amount'], Decimal('6.71'))  # 经营口径，交易日汇率
        self.assertEqual(row['settlement_amount'], Decimal('6.71'))  # 结算口径，月末汇率
        self.assertEqual(row['operating_rate_id'], 'r1')
        self.assertEqual(row['settlement_rate_id'], 'r1')
        self.assertFalse(row['operating_gap'])
        self.assertFalse(row['settlement_gap'])
        # 三口径是独立列，日常查询视图不受影响
        from warehouse.queries import overview
        self.assertIn('converted', overview(self.connection))

    def test_settlement_uses_month_end_rate_operating_uses_trade_day(self):
        self.load()
        self.connection.execute("INSERT INTO currencies VALUES ('TST',2)")
        self.connection.execute(
            "INSERT INTO revenue VALUES ('e1','','2026-10-10','2026-10-11T00:00:00Z','TST','1000','sale','p')")
        self.connection.execute(
            "INSERT INTO rates VALUES ('rd','2026-10-10','TST','USD','0.0060','2026-10-11T00:00:00Z')")
        self.connection.execute(
            "INSERT INTO rates VALUES ('re','2026-10-31','TST','USD','0.0070','2026-10-31T12:00:00Z')")
        close_period(self.connection, 'c1', '2026-10', 'USD', '2026-11-02T00:00:00Z')
        row = self.entries('c1')['e1']
        self.assertEqual(row['operating_rate_id'], 'rd')
        self.assertEqual(row['operating_amount'], Decimal('6.00'))          # 1000 * 0.0060
        self.assertEqual(row['settlement_rate_id'], 're')
        self.assertEqual(row['settlement_amount'], Decimal('7.00'))         # 1000 * 0.0070 月末汇率

    def test_rounding_delta_ties_out_per_entry_vs_aggregate(self):
        self.load()
        # 两笔 JPY 各 1 元放在独立月份：逐笔 0.0067 -> 0.01，合计 0.02；未取整汇总 0.0134 -> 0.01
        for idx in ('a', 'b'):
            self.connection.execute(
                "INSERT INTO revenue VALUES (?,?,?,?, 'JPY','1','sale','p')",
                ['rx-' + idx, '', '2026-09-12', '2026-09-13T00:00:00Z'])
            self.connection.execute(
                "INSERT INTO rates VALUES (?, '2026-09-12','JPY','USD','0.0067',?)",
                ['rr-' + idx, '2026-09-13T00:00:00Z'])
            self.connection.execute(
                "INSERT INTO rates VALUES (?, '2026-09-30','JPY','USD','0.0067',?)",
                ['rre-' + idx, '2026-09-30T12:00:00Z'])
        close_period(self.connection, 'rc', '2026-09', 'USD', '2026-10-02T00:00:00Z')
        data = self.totals('rc')['JPY']
        self.assertEqual(data['operating_rounded_sum'], Decimal('0.02'))
        self.assertEqual(data['operating_rounded_total'], Decimal('0.01'))
        self.assertEqual(data['operating_rounding_delta'], Decimal('0.01'))
        # 差额恒等于逐笔合计与汇总取整之差
        self.assertEqual(data['operating_rounding_delta'],
                         data['operating_rounded_sum'] - data['operating_rounded_total'])

    def test_missing_rate_is_tracked_gap_not_one_to_one(self):
        self.payload['tables']['rates'] = []
        self.load()
        result = close_period(self.connection, 'gap-close', '2026-10', 'USD', OCT_KNOWN)
        self.assertEqual(result['gaps'], 1)
        row = self.entries('gap-close')['j1']
        self.assertIsNone(row['operating_raw'])
        self.assertIsNone(row['operating_amount'])
        self.assertTrue(row['operating_gap'])
        self.assertEqual(self.totals('gap-close')['JPY']['gap_entries'], 1)

    def test_close_replay_and_collision_rules(self):
        self.load()
        first = close_period(self.connection, 'm', '2026-10', 'USD', OCT_KNOWN)
        again = close_period(self.connection, 'm', '2026-10', 'USD', OCT_KNOWN)
        self.assertFalse(first['replayed'])
        self.assertTrue(again['replayed'])
        with self.assertRaises(ValueError):
            close_period(self.connection, 'm', '2026-10', 'USD', LATE_KNOWN)
        with self.assertRaises(ValueError):
            close_period(self.connection, 'other', '2026-10', 'USD', OCT_KNOWN)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM close_versions').fetchone()[0], 1)

    def test_restatement_creates_diff_without_touching_approved(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        approved_before = self.totals('oct-close')['JPY']
        self.load(self.november)
        result = restate_period(self.connection, 'oct-restate', '2026-10', 'USD', LATE_KNOWN)
        self.assertEqual(result['status'], 'proposed')
        # j1 因补发汇率变化；j2 是关账后到达、指向 10 月原单的退款，重述必须纳入
        self.assertEqual(result['changed_entries'], 2)
        # 已批准版本数字原样保留
        self.assertEqual(self.totals('oct-close')['JPY'], approved_before)
        self.assertEqual(self.entries('oct-close')['j1']['operating_amount'], Decimal('6.71'))
        # 重述版本采用补发的官方汇率
        self.assertEqual(self.entries('oct-restate')['j1']['operating_amount'], Decimal('6.81'))
        # 后期退款：经营口径仍用自身交易日（缺 11-03 JPY 汇率 -> 缺口），
        # 结算口径用被重述月份（10 月）的月末汇率
        j2 = self.entries('oct-restate')['j2']
        self.assertEqual(j2['business_period'], '2026-10')
        self.assertEqual(j2['business_day'], '2026-11-03')
        self.assertEqual(j2['settlement_rate_id'], 'r1-late')
        self.assertEqual(j2['settlement_amount'], Decimal('-0.82'))
        self.assertTrue(j2['operating_gap'])
        diffs = diff_report(self.connection, 'oct-restate')
        by_entry = {diff['entry_id']: diff for diff in diffs}
        diff = by_entry['j1']
        self.assertEqual(diff['change_kind'], 'rate')
        self.assertEqual(diff['operating_delta'], Decimal('0.10'))
        self.assertEqual(diff['settlement_delta'], Decimal('0.10'))
        self.assertEqual(diff['prior_operating_rate_id'], 'r1')
        self.assertEqual(diff['restated_operating_rate_id'], 'r1-late')
        self.assertEqual(diff['prior_version_id'], 'oct-close')
        self.assertEqual(by_entry['j2']['change_kind'], 'new')
        self.assertEqual(by_entry['j2']['original_delta'], Decimal('-120'))

    def test_restatement_retry_does_not_duplicate_diffs(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        self.load(self.november)
        first = restate_period(self.connection, 'r1', '2026-10', 'USD', LATE_KNOWN)
        second = restate_period(self.connection, 'r1', '2026-10', 'USD', LATE_KNOWN)
        self.assertFalse(first['replayed'])
        self.assertTrue(second['replayed'])
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM restatement_diffs WHERE version_id=?', ['r1']).fetchone()[0], 2)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM close_versions').fetchone()[0], 2)
        with self.assertRaises(ValueError):
            restate_period(self.connection, 'r2', '2026-10', 'USD', LATE_KNOWN)

    def test_confirm_switches_baseline_but_keeps_history_frozen(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        self.load(self.november)
        restate_period(self.connection, 'oct-restate', '2026-10', 'USD', LATE_KNOWN)
        confirmed = confirm_restatement(self.connection, 'oct-restate')
        self.assertEqual(confirmed['status'], 'approved')
        replay = confirm_restatement(self.connection, 'oct-restate')
        self.assertTrue(replay['replayed'])
        statuses = {row['version_id']: row['status'] for row in records(self.connection,
                    'SELECT version_id,status FROM close_versions')}
        self.assertEqual(statuses, {'oct-close': 'superseded', 'oct-restate': 'approved'})
        self.assertEqual(current_version(self.connection, '2026-10')['version_id'], 'oct-restate')
        # 被取代版本的冻结数字仍可完整复现
        self.assertEqual(self.entries('oct-close')['j1']['operating_amount'], Decimal('6.71'))
        lineage = entry_lineage(self.connection, 'j1')
        self.assertEqual([row['version_id'] for row in lineage], ['oct-close', 'oct-restate'])
        self.assertEqual([row['operating_rate_id'] for row in lineage], ['r1', 'r1-late'])

    def test_cross_period_moves_attribute_change_to_each_month(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        oct_approved = self.totals('oct-close')['JPY']
        self.load(self.november)
        close_period(self.connection, 'nov-close', '2026-11', 'USD', '2026-11-29T00:00:00Z')
        # 11 月关账没有改动 10 月任何总数
        self.assertEqual(self.totals('oct-close')['JPY'], oct_approved)
        moves = records(self.connection,
                        'SELECT * FROM cross_period_moves WHERE version_id=? ORDER BY entry_id',
                        ['nov-close'])
        self.assertEqual(len(moves), 1)
        move = moves[0]
        self.assertEqual(move['entry_id'], 'j2')
        self.assertEqual(move['source_period'], '2026-10')
        self.assertEqual(move['settlement_period'], '2026-11')
        self.assertEqual(move['original_amount'], Decimal('-120'))
        # 结算月自身的逐单与汇总照常独立成立
        nov = self.entries('nov-close')
        self.assertIn('j2', nov)
        self.assertTrue(nov['j2']['operating_gap'])                   # 交易日 JPY 汇率缺口
        # 结算口径用结算月（11 月）月末汇率 r3：-120 * 0.0069 = -0.828 -> -0.83
        self.assertEqual(nov['j2']['settlement_rate_id'], 'r3')
        self.assertEqual(nov['j2']['settlement_amount'], Decimal('-0.83'))
        # 11 月关账中只有 j2（11 月处理、指向 10 月原单）构成跨月调整；
        # k2 的原单 k1 本身就是 11 月交易，归属月与自身业务月一致，不属于跨月
        report = version_report(self.connection, 'nov-close')
        self.assertEqual([m['entry_id'] for m in report['cross_period_moves']], ['j2'])
        move = report['cross_period_moves'][0]
        self.assertEqual(move['source_period'], '2026-10')
        self.assertEqual(move['settlement_period'], '2026-11')

        # 同一笔调整在 10 月重述中作为后期调整纳入并保留同样的跨月链接，两月视角互不覆写
        restate_period(self.connection, 'oct-restate-2', '2026-10', 'USD', LATE_KNOWN)
        restated_moves = records(self.connection,
                                 'SELECT * FROM cross_period_moves WHERE version_id=?',
                                 ['oct-restate-2'])
        self.assertEqual([m['entry_id'] for m in restated_moves], ['j2'])
        j2_move = restated_moves[0]
        self.assertEqual(j2_move['source_period'], '2026-10')
        self.assertEqual(j2_move['settlement_period'], '2026-11')

    def test_failed_restatement_is_atomic(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        self.connection.execute(
            "INSERT INTO revenue VALUES ('broken','missing-origin','2026-10-20',"
            "'2026-10-21T00:00:00Z','JPY','5','correction','p')")
        with self.assertRaises(ValueError):
            restate_period(self.connection, 'bad', '2026-10', 'USD', LATE_KNOWN)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM close_versions').fetchone()[0], 1)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM restatement_diffs').fetchone()[0], 0)

    def test_gap_fill_after_official_rate_republished(self):
        self.payload['tables']['rates'] = []
        self.load()
        close_period(self.connection, 'sep-like', '2026-10', 'USD', OCT_KNOWN)
        self.assertTrue(self.entries('sep-like')['j1']['operating_gap'])
        # 官方补发交易日汇率后重述
        self.connection.execute(
            "INSERT INTO rates VALUES ('late','2026-10-31','JPY','USD','0.0067',?)",
            [LATE_KNOWN])
        restate_period(self.connection, 'filled', '2026-10', 'USD', LATE_KNOWN)
        diff = diff_report(self.connection, 'filled')[0]
        self.assertEqual(diff['change_kind'], 'gap_filled')
        self.assertFalse(self.entries('filled')['j1']['operating_gap'])
        self.assertEqual(self.entries('filled')['j1']['operating_amount'], Decimal('6.71'))
        # 批准旧版本仍保留当时的缺口事实
        self.assertTrue(self.entries('sep-like')['j1']['operating_gap'])

    def test_export_contains_closing_facts(self):
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        manifest = export_tables(self.connection, self.root / 'out', 'rel-1')
        for table in ('close_versions', 'close_entries', 'close_totals'):
            self.assertIn(table, manifest['tables'])
            rows = read_export(self.connection, self.root / 'out', 'rel-1', table)
            self.assertGreater(len(rows), 0)

    def test_empty_period_and_bad_period_rejected(self):
        self.load()
        with self.assertRaises(ValueError):
            close_period(self.connection, 'empty', '2025-01', 'USD', OCT_KNOWN)
        with self.assertRaises(ValueError):
            close_period(self.connection, 'bad', '2026-13', 'USD', OCT_KNOWN)

    def test_diff_amounts_tie_to_totals_across_versions(self):
        """逐笔差额之和必须等于两版本汇总之差，结算可据此勾稽。"""
        self.load()
        close_period(self.connection, 'oct-close', '2026-10', 'USD', OCT_KNOWN)
        self.load(self.november)
        restate_period(self.connection, 'r1', '2026-10', 'USD', LATE_KNOWN)
        diffs = diff_report(self.connection, 'r1')
        before = self.totals('oct-close')['JPY']
        after = self.totals('r1')['JPY']
        op_diff = sum((d['operating_delta'] for d in diffs
                       if d['operating_delta'] is not None), Decimal('0'))
        st_diff = sum((d['settlement_delta'] for d in diffs
                       if d['settlement_delta'] is not None), Decimal('0'))
        original_diff = sum((d['original_delta'] for d in diffs), Decimal('0'))
        self.assertEqual(op_diff, after['operating_rounded_sum'] - before['operating_rounded_sum'])
        self.assertEqual(st_diff, after['settlement_rounded_sum'] - before['settlement_rounded_sum'])
        self.assertEqual(original_diff, after['original_total'] - before['original_total'])

    def test_restatement_chain_keeps_every_version_reproducible(self):
        """确认后可再次重述，形成版本链，每个版本的数字都能原样复现。"""
        self.load()
        close_period(self.connection, 'v0', '2026-10', 'USD', OCT_KNOWN)
        self.load(self.november)
        restate_period(self.connection, 'v1', '2026-10', 'USD', LATE_KNOWN)
        # 未确认前不能开第二个待确认重述
        with self.assertRaises(ValueError):
            restate_period(self.connection, 'v2-early', '2026-10', 'USD', LATE_KNOWN)
        confirm_restatement(self.connection, 'v1')
        # 再次补发新汇率，形成第二代重述
        self.connection.execute(
            "INSERT INTO rates VALUES ('r1-final','2026-10-31','JPY','USD','0.0069',?)",
            ['2026-11-20T00:00:00Z'])
        restate_period(self.connection, 'v2', '2026-10', 'USD', '2026-11-21T00:00:00Z')
        confirm_restatement(self.connection, 'v2')
        chain = records(self.connection,
                        'SELECT version_id,parent_version_id,status,superseded_by FROM close_versions ORDER BY created_at')
        statuses = {row['version_id']: (row['status'], row['parent_version_id'], row['superseded_by'])
                    for row in chain}
        self.assertEqual(statuses['v0'][0], 'superseded')
        self.assertEqual(statuses['v1'][0], 'superseded')
        self.assertEqual(statuses['v2'][0], 'approved')
        self.assertEqual(statuses['v2'][1], 'v1')
        # 三代数字各自冻结：v0 用 r1，v1 用 r1-late，v2 用 r1-final
        lineage = entry_lineage(self.connection, 'j1')
        self.assertEqual([r['operating_rate_id'] for r in lineage], ['r1', 'r1-late', 'r1-final'])
        self.assertEqual(current_version(self.connection, '2026-10')['version_id'], 'v2')
        # 第二代差额相对于第一代（v1），链路不断
        d2 = diff_report(self.connection, 'v2')
        self.assertEqual({d['prior_version_id'] for d in d2}, {'v1'})


if __name__ == '__main__':
    unittest.main()
