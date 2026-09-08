#!/usr/bin/env python3
"""Copy an Open WebUI SQLite schema and rows into an empty Oracle schema.

This intentionally creates quoted Oracle identifiers matching the SQLite names.
It is a data migration/export tool, not a switch of Open WebUI's primary ORM to
Oracle. It never drops or truncates remote objects.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import oracledb


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def oracle_type(sqlite_type: str, is_primary_key: bool) -> str:
    value = (sqlite_type or '').upper()
    if is_primary_key and any(token in value for token in ('CHAR', 'TEXT', 'CLOB')):
        return 'VARCHAR2(255)'
    if 'INT' in value:
        return 'NUMBER(19)'
    if any(token in value for token in ('REAL', 'FLOA', 'DOUB')):
        return 'BINARY_DOUBLE'
    if 'BLOB' in value:
        return 'BLOB'
    if is_primary_key:
        return 'VARCHAR2(255)'
    return 'CLOB'


def oracle_value(value, sqlite_type: str, is_primary_key: bool):
    """Coerce SQLite's dynamic values to the Oracle type chosen for the column."""
    if value is None or isinstance(value, bytes):
        return value
    target_type = oracle_type(sqlite_type, is_primary_key)
    if target_type in {'CLOB', 'VARCHAR2(255)'}:
        return str(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite-path', required=True, type=Path)
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--batch-size', type=int, default=200)
    args = parser.parse_args()

    local = sqlite3.connect(args.sqlite_path)
    local.row_factory = sqlite3.Row
    tables = [
        row[0]
        for row in local.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    remote = oracledb.connect(user=args.user, password=args.password, dsn=args.dsn)
    try:
        with remote.cursor() as cursor:
            cursor.execute('SELECT table_name FROM user_tables')
            existing = {row[0].lower() for row in cursor}
            collisions = sorted(name for name in tables if name.lower() in existing)
            if collisions:
                raise RuntimeError('Remote schema is not empty; refusing to overwrite: ' + ', '.join(collisions))

            for table in tables:
                columns = list(local.execute(f'PRAGMA table_info({quote(table)})'))
                primary_keys = sorted((column for column in columns if column['pk']), key=lambda column: column['pk'])
                definitions = [
                    f"{quote(column['name'])} {oracle_type(column['type'], bool(column['pk']))}"
                    + ('' if column['notnull'] else ' NULL')
                    for column in columns
                ]
                if primary_keys:
                    definitions.append('PRIMARY KEY (' + ', '.join(quote(column['name']) for column in primary_keys) + ')')
                cursor.execute(f"CREATE TABLE {quote(table)} (" + ', '.join(definitions) + ')')

                column_names = [column['name'] for column in columns]
                source_sql = f"SELECT " + ', '.join(quote(name) for name in column_names) + f" FROM {quote(table)}"
                insert_sql = (
                    f"INSERT INTO {quote(table)} (" + ', '.join(quote(name) for name in column_names) + ') VALUES ('
                    + ', '.join(f':{index + 1}' for index in range(len(column_names))) + ')'
                )
                rows = local.execute(source_sql)
                batch = []
                copied = 0
                for row in rows:
                    batch.append(
                        tuple(
                            oracle_value(row[column['name']], column['type'], bool(column['pk']))
                            for column in columns
                        )
                    )
                    if len(batch) == args.batch_size:
                        cursor.executemany(insert_sql, batch)
                        copied += len(batch)
                        batch = []
                if batch:
                    cursor.executemany(insert_sql, batch)
                    copied += len(batch)
                remote.commit()
                print(f'{table}: {copied} rows')
    finally:
        remote.close()
        local.close()


if __name__ == '__main__':
    main()
