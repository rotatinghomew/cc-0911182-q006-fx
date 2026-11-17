from .money import source_totals,converted_entries


def overview(connection):
    return {'original':source_totals(connection),'converted':converted_entries(connection)}
