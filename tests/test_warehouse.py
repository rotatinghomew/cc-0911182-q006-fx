
import copy
import json
import tempfile
import unittest
from pathlib import Path

from warehouse.export import export_tables, read_export
from warehouse.ingest import ingest
from warehouse.queries import overview
from warehouse.storage import open_store


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = open_store(self.root / 'store.duckdb')
        self.payload = json.loads(Path('samples/delivery.json').read_text())

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def load(self):
        return ingest(self.connection, copy.deepcopy(self.payload))

    def test_replay_keeps_source_rows(self):
        first = self.load()
        second = self.load()
        self.assertGreater(first['inserted'], 0)
        self.assertEqual(second['inserted'], 0)
        self.assertTrue(second['replayed'])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_batch_collision_is_atomic(self):
        self.load()
        collision = copy.deepcopy(self.payload)
        collision['tables'] = {}
        with self.assertRaises(ValueError):
            ingest(self.connection, collision)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_unknown_table_does_not_write_receipt(self):
        invalid = {'batch_id': 'bad', 'tables': {'missing_table': []}}
        with self.assertRaises(ValueError):
            ingest(self.connection, invalid)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)

    def test_different_batch_same_rows_is_not_duplicate(self):
        first = self.load()
        duplicate = copy.deepcopy(self.payload)
        duplicate['batch_id'] = 'retry-another-envelope'
        self.assertEqual(ingest(self.connection, duplicate)['inserted'], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM source_rows').fetchone()[0], first['inserted'])

    def test_export_roundtrip_and_hash(self):
        self.load()
        result = export_tables(self.connection, self.root / 'exports', 'release1')
        for table, entry in result['tables'].items():
            actual = read_export(self.connection, self.root / 'exports', 'release1', table)
            self.assertEqual(len(actual), entry['rows'])
            self.assertEqual(set(actual[0]) if actual else set(),
                             set(self.payload['tables'][table][0]) if actual else set())
        table = next(iter(result['tables']))
        output = self.root / 'exports' / 'release1' / (table + '.parquet')
        output.write_bytes(output.read_bytes() + b'broken')
        with self.assertRaises(ValueError):
            read_export(self.connection, self.root / 'exports', 'release1', table)

    def test_export_names_and_immutability(self):
        self.load()
        with self.assertRaises(ValueError):
            export_tables(self.connection, self.root, '../escape')
        export_tables(self.connection, self.root, 'stable')
        with self.assertRaises(FileExistsError):
            export_tables(self.connection, self.root, 'stable')

    def test_reopen_preserves_data(self):
        self.load()
        expected = overview(self.connection)
        self.connection.close()
        self.connection = open_store(self.root / 'store.duckdb')
        self.assertEqual(overview(self.connection), expected)

    def test_rejected_record_has_no_partial_state(self):
        invalid = copy.deepcopy(self.payload)
        populated = next(rows for rows in invalid['tables'].values() if rows)
        populated.append({})
        with self.assertRaises(ValueError):
            ingest(self.connection, invalid)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM source_rows').fetchone()[0], 0)

    def test_source_currency_and_rounding(self):
        from warehouse.money import converted_entries,source_totals
        self.load()
        rows={row['entry_id']:row for row in converted_entries(self.connection)}
        self.assertEqual(str(rows['j1']['converted']),'6.71')
        self.assertEqual(str(rows['k1']['converted']),'7.62')
        self.assertEqual(len(source_totals(self.connection)),2)

    def test_unknown_rate_is_gap(self):
        from warehouse.money import converted_entries
        self.payload['tables']['rates']=[]
        self.load()
        self.assertTrue(all(row['converted'] is None for row in converted_entries(self.connection)))

    def test_known_time_selects_rate_publication(self):
        from warehouse.money import converted_entries
        self.load()
        self.assertTrue(all(row['converted'] is None for row in converted_entries(self.connection,known_at='2026-10-30T00:00:00Z')))


    def test_reconciliation_records_publication_cutoff(self):
        from warehouse.reconciliation import reconcile
        self.load()
        result=reconcile(self.connection,'daily','USD','2026-11-02T00:00:00Z')
        self.assertEqual(result['gaps'],0)
        self.assertEqual(reconcile(self.connection,'daily','USD','2026-11-02T00:00:00Z'),result)
        with self.assertRaises(ValueError):
            reconcile(self.connection,'daily','USD','2026-11-03T00:00:00Z')
