from __future__ import annotations

import copy
import asyncio
import json
import logging
import os
import posixpath
import socket
import subprocess
import tempfile
import time
from uuid import uuid4
from typing import Literal, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from mcp.shared.auth import OAuthMetadata
from open_webui.config import BannerModel
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT
from open_webui.events import EVENTS, publish_event
from open_webui.models.config import Config
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.headers import bearer_auth_header, get_custom_headers
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.oauth import (
    OAuthClientInformationFull,
    apply_connection_oauth_options,
    decrypt_data,
    encrypt_data,
    get_discovery_urls,
    get_oauth_client_info_with_dynamic_client_registration,
    get_oauth_client_info_with_static_credentials,
    recover_static_oauth_client_metadata,
    resolve_oauth_client_info,
)
from open_webui.utils.tools import (
    get_tool_server_data,
    get_tool_server_url,
    set_terminal_servers,
    set_tool_servers,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from open_webui.internal.db import get_async_db

router = APIRouter()

log = logging.getLogger(__name__)

CONNECTIONS_CONFIG_KEYS = {
    'ENABLE_DIRECT_CONNECTIONS': 'direct.enable',
    'ENABLE_BASE_MODELS_CACHE': 'models.base_models_cache',
}
CODE_EXECUTION_CONFIG_KEYS = {
    'ENABLE_CODE_EXECUTION': 'code_execution.enable',
    'CODE_EXECUTION_ENGINE': 'code_execution.engine',
    'CODE_EXECUTION_JUPYTER_URL': 'code_execution.jupyter.url',
    'CODE_EXECUTION_JUPYTER_AUTH': 'code_execution.jupyter.auth',
    'CODE_EXECUTION_JUPYTER_AUTH_TOKEN': 'code_execution.jupyter.auth_token',
    'CODE_EXECUTION_JUPYTER_AUTH_PASSWORD': 'code_execution.jupyter.auth_password',
    'CODE_EXECUTION_JUPYTER_TIMEOUT': 'code_execution.jupyter.timeout',
    'ENABLE_CODE_INTERPRETER': 'code_interpreter.enable',
    'CODE_INTERPRETER_ENGINE': 'code_interpreter.engine',
    'CODE_INTERPRETER_PROMPT_TEMPLATE': 'code_interpreter.prompt_template',
    'CODE_INTERPRETER_JUPYTER_URL': 'code_interpreter.jupyter.url',
    'CODE_INTERPRETER_JUPYTER_AUTH': 'code_interpreter.jupyter.auth',
    'CODE_INTERPRETER_JUPYTER_AUTH_TOKEN': 'code_interpreter.jupyter.auth_token',
    'CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD': 'code_interpreter.jupyter.auth_password',
    'CODE_INTERPRETER_JUPYTER_TIMEOUT': 'code_interpreter.jupyter.timeout',
}
MODELS_CONFIG_KEYS = {
    'DEFAULT_MODELS': 'ui.default_models',
    'DEFAULT_PINNED_MODELS': 'ui.default_pinned_models',
    'MODEL_ORDER_LIST': 'ui.model_order_list',
    'DEFAULT_MODEL_METADATA': 'models.default_metadata',
    'DEFAULT_MODEL_PARAMS': 'models.default_params',
}
SUBAGENTS_CONFIG_KEYS = {
    'ENABLE_SUBAGENTS': 'subagents.enable',
    'SUBAGENTS_BACKGROUND_ENABLED': 'subagents.background_enabled',
    'SUBAGENTS_MAX_CONCURRENT': 'subagents.max_concurrent',
    'SUBAGENTS_MAX_ASYNC': 'subagents.max_async',
    'SUBAGENTS_MAX_ITERATIONS': 'subagents.max_iterations',
    'SUBAGENTS_MAX_OUTPUT': 'subagents.max_output',
    'SUBAGENTS_SYSTEM_PROMPT': 'subagents.system_prompt',
}

ORACLE_MONITOR_TARGETS_CONFIG_KEY = 'oracle_monitor.targets'
ORACLE_LOG_DOWNLOADS: dict[str, dict[str, str]] = {}
ORACLE_MONITOR_HISTORY_RETENTION_DAYS = 90


ORACLE_MONITOR_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS oracle_monitor_history (
    id TEXT PRIMARY KEY,
    captured_at INTEGER NOT NULL,
    captured_by TEXT,
    report_type TEXT NOT NULL,
    target_id TEXT,
    target_name TEXT,
    service_name TEXT,
    protocol TEXT,
    success INTEGER NOT NULL,
    error_detail TEXT,
    payload_json TEXT NOT NULL
)
"""


async def _record_oracle_monitor_history(
    report_type: str,
    payload: dict,
    user_id: str,
    *,
    target: dict | None = None,
    error_detail: str | None = None,
) -> None:
    """Persist a non-secret operational snapshot for history and future chat retrieval."""
    captured_at = int(time.time())
    try:
        async with get_async_db() as db:
            await db.execute(text(ORACLE_MONITOR_HISTORY_DDL))
            await db.execute(
                text(
                    'CREATE INDEX IF NOT EXISTS idx_oracle_monitor_history_lookup '
                    'ON oracle_monitor_history (report_type, target_id, captured_at DESC)'
                )
            )
            await db.execute(
                text(
                    """
                    INSERT INTO oracle_monitor_history (
                        id, captured_at, captured_by, report_type, target_id, target_name,
                        service_name, protocol, success, error_detail, payload_json
                    ) VALUES (
                        :id, :captured_at, :captured_by, :report_type, :target_id, :target_name,
                        :service_name, :protocol, :success, :error_detail, :payload_json
                    )
                    """
                ),
                {
                    'id': str(uuid4()),
                    'captured_at': captured_at,
                    'captured_by': user_id,
                    'report_type': report_type,
                    'target_id': target.get('id') if target else None,
                    'target_name': target.get('name') if target else None,
                    'service_name': target.get('service_name') if target else None,
                    'protocol': target.get('protocol') if target else None,
                    'success': 0 if error_detail else 1,
                    'error_detail': error_detail,
                    'payload_json': json.dumps(payload, default=str, separators=(',', ':')),
                },
            )
            await db.execute(
                text('DELETE FROM oracle_monitor_history WHERE captured_at < :oldest'),
                {'oldest': captured_at - ORACLE_MONITOR_HISTORY_RETENTION_DAYS * 24 * 60 * 60},
            )
            await db.commit()
    except Exception as exc:
        # Monitoring itself must remain available if history persistence is unavailable.
        log.warning('Unable to persist Oracle monitoring history: %s', exc)


async def get_config_values(key_map: dict[str, str]) -> dict:
    values = await Config.get_many(*key_map.values())
    return {field: values[storage_key] for field, storage_key in key_map.items() if storage_key in values}


def config_updates(data: dict, key_map: dict[str, str]) -> dict:
    return {key_map[field]: value for field, value in data.items() if field in key_map}


class OracleMonitorTarget(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1521, ge=1, le=65535)
    protocol: Literal['TCP', 'TCPS'] = 'TCP'
    service_name: str = Field(min_length=1, max_length=255)
    username: str = Field(default='SYS', min_length=1, max_length=128)
    password: str | None = Field(default=None, max_length=1024)
    oracle_os_owner: str | None = Field(default=None, max_length=128)
    oracle_os_password: str | None = Field(default=None, max_length=1024)
    oracle_base: str | None = Field(default=None, max_length=1024)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)

    @field_validator('username')
    @classmethod
    def require_sys_user(cls, value: str) -> str:
        if value.strip().upper() != 'SYS':
            raise ValueError('Oracle monitoring targets must use the SYS user')
        return 'SYS'


class OracleMonitorTargetsForm(BaseModel):
    targets: list[OracleMonitorTarget] = Field(default_factory=list, max_length=100)


class OracleSessionSelection(BaseModel):
    inst_id: int = Field(ge=1)
    sid: int = Field(ge=1)
    serial_number: int = Field(ge=1)


class OracleSessionKillRequest(BaseModel):
    sessions: list[OracleSessionSelection] = Field(min_length=1, max_length=100)


class OracleParameterFilter(BaseModel):
    name_filter: str = Field(default='', max_length=200)


class OracleTraceFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def _public_oracle_target(target: dict) -> dict:
    return {
        'id': target['id'],
        'name': target['name'],
        'host': target['host'],
        'port': target['port'],
        'protocol': target.get('protocol', 'TCP'),
        'service_name': target['service_name'],
        'username': target.get('username', 'SYS'),
        'oracle_os_owner': target.get('oracle_os_owner'),
        'oracle_base': target.get('oracle_base'),
        'connect_timeout_seconds': target['connect_timeout_seconds'],
        'has_password': bool(target.get('password')),
        'has_oracle_os_password': bool(target.get('oracle_os_password')),
    }


def _oracle_target_sort_key(target: dict) -> tuple[str, str, str]:
    return (
        str(target.get('name', target.get('database_name', ''))).casefold(),
        str(target.get('service_name', '')).casefold(),
        str(target.get('protocol', 'TCP')).casefold(),
    )


def _oracle_connect(target: dict):
    """Open a direct TCP/TCPS SYSDBA connection for one configured target."""
    import oracledb

    password = decrypt_data(target['password'])
    dsn = (
        f"(DESCRIPTION=(ADDRESS=(PROTOCOL={target.get('protocol', 'TCP')})"
        f"(HOST={target['host']})(PORT={target['port']}))"
        f"(CONNECT_DATA=(SERVICE_NAME={target['service_name']})))"
    )
    return oracledb.connect(
        user='SYS',
        password=password,
        dsn=dsn,
        mode=oracledb.AUTH_MODE_SYSDBA,
        tcp_connect_timeout=target['connect_timeout_seconds'],
    )


def _oracle_monitor_status(target: dict) -> dict:
    """Run the network and database checks without exposing a stored secret."""
    result = {
        'id': target['id'],
        'name': target['name'],
        'host': target['host'],
        'port': target['port'],
        'protocol': target.get('protocol', 'TCP'),
        'service_name': target['service_name'],
        'listener_status': 'DOWN',
        'database_status': 'DOWN',
        'instance_name': None,
        'instance_status': None,
        'open_mode': None,
        'database_role': None,
        'version_full': None,
        'startup_time': None,
        'uptime': None,
        'fra_available_mb': None,
        'fra_available_pct': None,
        'detail': None,
    }
    timeout = target['connect_timeout_seconds']

    try:
        with socket.create_connection((target['host'], target['port']), timeout=timeout):
            result['listener_status'] = 'UP'
    except OSError as exc:
        result['detail'] = f'Listener connection failed: {str(exc)[:180]}'
        return result

    try:
        with _oracle_connect(target) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        i.instance_name,
                        i.status,
                        i.version_full,
                        TO_CHAR(i.startup_time, 'YYYY-MM-DD HH24:MI:SS') AS startup_time,
                        EXTRACT(DAY FROM (SYSTIMESTAMP - CAST(i.startup_time AS TIMESTAMP))) || 'd ' ||
                        EXTRACT(HOUR FROM (SYSTIMESTAMP - CAST(i.startup_time AS TIMESTAMP))) || 'h ' ||
                        EXTRACT(MINUTE FROM (SYSTIMESTAMP - CAST(i.startup_time AS TIMESTAMP))) || 'm' AS uptime,
                        ROUND(GREATEST(f.space_limit - f.space_used, 0) / 1024 / 1024, 2) AS fra_available_mb,
                        ROUND(
                            GREATEST(f.space_limit - f.space_used, 0) * 100 / NULLIF(f.space_limit, 0),
                            2
                        ) AS fra_available_pct,
                        d.open_mode,
                        d.database_role
                    FROM v$instance i
                    CROSS JOIN v$database d
                    LEFT JOIN v$recovery_file_dest f ON 1 = 1
                    """
                )
                row = cursor.fetchone()
        result.update(
            {
                'database_status': 'UP',
                'instance_name': row[0],
                'instance_status': row[1],
                'version_full': row[2],
                'startup_time': row[3],
                'uptime': row[4],
                'fra_available_mb': row[5],
                'fra_available_pct': row[6],
                'open_mode': row[7],
                'database_role': row[8],
            }
        )
    except Exception as exc:
        result['detail'] = f'Database connection/query failed: {str(exc)[:180]}'

    return result


ORACLE_CURRENT_USAGE_QUERY = """
WITH session_banners AS (
    SELECT
        ci.inst_id,
        ci.sid,
        ci.serial#,
        LISTAGG(ci.network_service_banner, ' | ')
            WITHIN GROUP (ORDER BY ci.network_service_banner) AS banners
    FROM gv$session_connect_info ci
    GROUP BY ci.inst_id, ci.sid, ci.serial#
)
SELECT
    s.inst_id,
    s.sid,
    s.serial# AS serial_number,
    s.username,
    s.status,
    EXTRACT(DAY FROM (SYSTIMESTAMP - CAST(s.logon_time AS TIMESTAMP))) || 'd ' ||
    EXTRACT(HOUR FROM (SYSTIMESTAMP - CAST(s.logon_time AS TIMESTAMP))) || 'h ' ||
    EXTRACT(MINUTE FROM (SYSTIMESTAMP - CAST(s.logon_time AS TIMESTAMP))) || 'm' AS age,
    s.service_name,
    s.machine,
    s.program,
    s.logon_time,
    CASE
        WHEN LOWER(b.banners) LIKE '%tcp/ip with ssl%' THEN 'TCPS'
        WHEN LOWER(b.banners) LIKE '%tcp/ip%' THEN 'TCP'
        ELSE 'OTHER / REVIEW BANNER'
    END AS transport,
    s.sql_id,
    s.prev_sql_id,
    COALESCE(
        (
            SELECT DBMS_LOB.SUBSTR(cur.sql_fulltext, 32767, 1)
            FROM gv$sql cur
            WHERE cur.inst_id = s.inst_id
              AND cur.sql_id = s.sql_id
              AND ROWNUM = 1
        ),
        (
            SELECT DBMS_LOB.SUBSTR(prev.sql_fulltext, 32767, 1)
            FROM gv$sql prev
            WHERE prev.inst_id = s.inst_id
              AND prev.sql_id = s.prev_sql_id
              AND ROWNUM = 1
        )
    ) AS last_sql
FROM gv$session s
LEFT JOIN session_banners b
  ON b.inst_id = s.inst_id
 AND b.sid = s.sid
 AND b.serial# = s.serial#
WHERE s.type = 'USER'
  AND s.username IS NOT NULL
ORDER BY transport, s.inst_id, s.sid
"""


def _oracle_current_usage(target: dict) -> dict:
    """Execute the requested user-session transport report for one target."""
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(ORACLE_CURRENT_USAGE_QUERY)
            columns = [column[0].lower() for column in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor]

    transports = {'TCP': 0, 'TCPS': 0, 'OTHER / REVIEW BANNER': 0}
    statuses = {'ACTIVE': 0, 'INACTIVE': 0}
    for row in rows:
        transports[row['transport']] = transports.get(row['transport'], 0) + 1
        statuses[row['status']] = statuses.get(row['status'], 0) + 1
    return {
        'target': _public_oracle_target(target),
        'summary': {
            'total_sessions': len(rows),
            'active_sessions': statuses.get('ACTIVE', 0),
            'inactive_sessions': statuses.get('INACTIVE', 0),
            'tcp_sessions': transports.get('TCP', 0),
            'tcps_sessions': transports.get('TCPS', 0),
            'review_sessions': transports.get('OTHER / REVIEW BANNER', 0),
        },
        'rows': rows,
    }


def _oracle_current_usage_summary(target: dict) -> dict:
    """Return a lightweight USER-session total for the home-page card."""
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*)
                FROM gv$session
                WHERE type = 'USER' AND username IS NOT NULL
                GROUP BY status
                """
            )
            counts = {status: count for status, count in cursor}
    return {
        'total_sessions': sum(counts.values()),
        'active_sessions': counts.get('ACTIVE', 0),
        'inactive_sessions': counts.get('INACTIVE', 0),
    }


def _oracle_display_value(value) -> str | None:
    """Convert Oracle values to concise JSON-safe strings for the details views."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex().upper()
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat(sep=' ')
        except TypeError:
            return value.isoformat()
    return str(value)


ORACLE_PARAMETER_TYPE_CATEGORIES = {
    '1': 'Boolean',
    '2': 'String',
    '3': 'Integer',
    '4': 'Parameter file',
    '5': 'Reserved',
    '6': 'Big integer',
}


def _oracle_details(target: dict, detail_type: Literal['database', 'instance', 'parameters'], name_filter: str = '') -> dict:
    """Return requested fixed V$ view data without allowing arbitrary SQL input."""
    queries = {
        'database': 'SELECT * FROM v$database',
        'instance': 'SELECT * FROM v$instance',
        'parameters': (
            "SELECT * FROM v$parameter "
            "WHERE :name_filter IS NULL OR INSTR(LOWER(name), LOWER(:name_filter)) > 0 "
            "ORDER BY name"
        ),
    }
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            if detail_type == 'parameters':
                cursor.execute(queries[detail_type], {'name_filter': name_filter.strip()})
            else:
                cursor.execute(queries[detail_type])
            columns = [column[0].lower() for column in cursor.description]
            rows = [
                {column: _oracle_display_value(value) for column, value in zip(columns, row, strict=True)}
                for row in cursor
            ]
    if detail_type == 'parameters' and 'type' in columns:
        columns = ['type_category' if column == 'type' else column for column in columns]
        for row in rows:
            type_code = row.pop('type', None)
            row['type_category'] = ORACLE_PARAMETER_TYPE_CATEGORIES.get(str(type_code), f'Other ({type_code})')
    return {'columns': columns, 'rows': rows}


def _oracle_sftp_download(target: dict, remote_path: str) -> tuple[str, str]:
    """Download a diagnostic log with SFTP as the configured Oracle OS owner."""
    owner = (target.get('oracle_os_owner') or '').strip()
    oracle_base = (target.get('oracle_base') or '').strip()
    encrypted_password = target.get('oracle_os_password')
    if not owner or not oracle_base or not encrypted_password:
        raise ValueError('Configure Oracle OS owner, Oracle OS password, and ORACLE_BASE before viewing logs')

    password = decrypt_data(encrypted_password)
    askpass_path = None
    local_path = None
    keep_temp_file = False
    try:
        with tempfile.NamedTemporaryFile(mode='w', prefix='jdeops-ssh-askpass-', delete=False) as askpass_file:
            askpass_path = askpass_file.name
            askpass_file.write('#!/bin/sh\nprintf %s "$JDEOPS_SSH_PASSWORD"\n')
        os.chmod(askpass_path, 0o700)
        environment = os.environ.copy()
        environment.update(
            {
                'DISPLAY': 'jdeops-ssh',
                'SSH_ASKPASS': askpass_path,
                'SSH_ASKPASS_REQUIRE': 'force',
                'JDEOPS_SSH_PASSWORD': password,
            }
        )
        with tempfile.NamedTemporaryFile(prefix='jdeops-oracle-log-', delete=False) as log_file:
            local_path = log_file.name
        result = subprocess.run(
            [
                'sftp',
                '-b', '-',
                '-o', 'BatchMode=no',
                '-o', 'StrictHostKeyChecking=accept-new',
                '-o', f'ConnectTimeout={target.get("connect_timeout_seconds", 5)}',
                f'{owner}@{target["host"]}',
            ],
            input=f'get "{remote_path}" "{local_path}"\n',
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or 'SFTP download failed').strip()[:300])
        with open(local_path, 'rb') as log_file:
            log_file.seek(0, os.SEEK_END)
            log_file.seek(max(log_file.tell() - 256 * 1024, 0))
            keep_temp_file = True
            return local_path, log_file.read().decode('utf-8', errors='replace')
    finally:
        if askpass_path:
            try:
                os.unlink(askpass_path)
            except FileNotFoundError:
                pass
        if local_path and not keep_temp_file:
            try:
                os.unlink(local_path)
            except FileNotFoundError:
                pass


def _oracle_log(target: dict, log_type: Literal['alert', 'listener']) -> dict:
    """Locate and return the newest tail of the requested Oracle diagnostic log."""
    oracle_base = (target.get('oracle_base') or '').strip()
    if log_type == 'alert':
        with _oracle_connect(target) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT d.db_unique_name, i.instance_name FROM v$database d CROSS JOIN v$instance i')
                db_unique_name, instance_name = cursor.fetchone()
        path = posixpath.join(
            oracle_base,
            'diag',
            'rdbms',
            str(db_unique_name),
            str(instance_name),
            'trace',
            f'alert_{instance_name}.log',
        )
    else:
        host_name = str(target['host']).split('.')[0]
        path = posixpath.join(oracle_base, 'diag', 'tnslsnr', host_name, 'listener', 'trace', 'listener.log')

    temp_path, content = _oracle_sftp_download(target, path)
    return {'log_type': log_type, 'path': path, 'content': content, 'temp_path': temp_path}


def _oracle_trace_log(target: dict, trace_path: str) -> dict:
    """Download a trace file referenced by an alert log, limited to ORACLE_BASE diagnostics."""
    oracle_base = (target.get('oracle_base') or '').strip()
    allowed_root = posixpath.normpath(posixpath.join(oracle_base, 'diag'))
    normalized_path = posixpath.normpath(trace_path)
    try:
        is_within_diagnostics = posixpath.commonpath([allowed_root, normalized_path]) == allowed_root
    except ValueError:
        is_within_diagnostics = False
    if not is_within_diagnostics or not normalized_path.lower().endswith(('.trc', '.trm')):
        raise ValueError('Only .trc or .trm files below ORACLE_BASE/diag can be opened')
    temp_path, content = _oracle_sftp_download(target, normalized_path)
    return {'log_type': 'trace', 'path': normalized_path, 'content': content, 'temp_path': temp_path}


def _oracle_kill_inactive_sessions(target: dict, sessions: list[OracleSessionSelection]) -> dict:
    """Recheck selected RAC sessions and kill only those still inactive."""
    killed = []
    skipped = []
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            for session in sessions:
                cursor.execute(
                    """
                    SELECT status
                    FROM gv$session
                    WHERE inst_id = :inst_id
                      AND sid = :sid
                      AND serial# = :serial_number
                      AND type = 'USER'
                      AND username IS NOT NULL
                    """,
                    {
                        'inst_id': session.inst_id,
                        'sid': session.sid,
                        'serial_number': session.serial_number,
                    },
                )
                row = cursor.fetchone()
                reference = f'{session.inst_id}:{session.sid}:{session.serial_number}'
                if row is None:
                    skipped.append({'session': reference, 'reason': 'Session no longer exists'})
                    continue
                if row[0] != 'INACTIVE':
                    skipped.append({'session': reference, 'reason': f"Session is {row[0]}, not INACTIVE"})
                    continue
                cursor.execute(
                    f"ALTER SYSTEM KILL SESSION '{session.sid},{session.serial_number},@{session.inst_id}' IMMEDIATE"
                )
                killed.append(reference)
    return {'killed': killed, 'skipped': skipped}


ORACLE_PATCH_LEVEL_QUERY = """
SELECT
    patch_id,
    patch_type,
    action,
    status,
    TO_CHAR(action_time, 'YYYY-MM-DD HH24:MI:SS') AS action_time,
    source_version,
    target_version,
    description
FROM dba_registry_sqlpatch
ORDER BY action_time DESC
"""


def _oracle_patch_level(target: dict) -> dict:
    """Return SQL patch inventory for one configured Oracle target."""
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(ORACLE_PATCH_LEVEL_QUERY)
            columns = [column[0].lower() for column in cursor.description]
            patch_rows = [dict(zip(columns, row, strict=True)) for row in cursor]

    public_target = _public_oracle_target(target)
    rows = [
        {
            'database_name': public_target['name'],
            'service_name': public_target['service_name'],
            'protocol': public_target['protocol'],
            'target_id': public_target['id'],
            **row,
        }
        for row in patch_rows
    ]
    return {'target': public_target, 'rows': rows}


def _oracle_inventory_health(target: dict) -> dict:
    """Return the dashboard health and capacity snapshot for one Oracle target."""
    result = _oracle_monitor_status(target)
    result.update(
        {
            'overall_status': 'UP' if result['listener_status'] == 'UP' and result['database_status'] == 'UP' else 'DOWN',
            'total_sessions': None,
            'active_sessions': None,
            'inactive_sessions': None,
            'total_processes': None,
        }
    )
    if result['database_status'] != 'UP':
        return result

    try:
        with _oracle_connect(target) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_sessions,
                        SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_sessions,
                        SUM(CASE WHEN status = 'INACTIVE' THEN 1 ELSE 0 END) AS inactive_sessions
                    FROM gv$session
                    WHERE type = 'USER' AND username IS NOT NULL
                    """
                )
                total_sessions, active_sessions, inactive_sessions = cursor.fetchone()
                cursor.execute('SELECT COUNT(*) FROM gv$process')
                total_processes = cursor.fetchone()[0]
        result.update(
            {
                'total_sessions': total_sessions,
                'active_sessions': active_sessions or 0,
                'inactive_sessions': inactive_sessions or 0,
                'total_processes': total_processes,
            }
        )
    except Exception as exc:
        result['overall_status'] = 'DEGRADED'
        result['detail'] = f"Capacity query failed: {str(exc)[:180]}"
    return result


ORACLE_BACKUP_STATUS_QUERY = """
SELECT
    input_type,
    status,
    TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
    TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time,
    elapsed_seconds,
    output_bytes_display,
    time_taken_display,
    compression_ratio
FROM v$rman_backup_job_details
ORDER BY start_time DESC
FETCH FIRST 100 ROWS ONLY
"""


def _oracle_backup_status(target: dict) -> dict:
    """Return recent RMAN backup-job history for one Oracle target."""
    with _oracle_connect(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(ORACLE_BACKUP_STATUS_QUERY)
            columns = [column[0].lower() for column in cursor.description]
            backup_rows = [dict(zip(columns, row, strict=True)) for row in cursor]
    public_target = _public_oracle_target(target)
    return {
        'rows': [
            {
                'database_name': public_target['name'],
                'service_name': public_target['service_name'],
                'protocol': public_target['protocol'],
                'target_id': public_target['id'],
                **row,
            }
            for row in backup_rows
        ]
    }


############################
# ImportConfig
# Thy configuration come, thy settings be done,
# in production as it is in development.
############################


class ImportConfigForm(BaseModel):
    config: dict


@router.post('/import', response_model=dict)
async def import_config(request: Request, form_data: ImportConfigForm, user=Depends(get_admin_user)):
    await Config.upsert(form_data.config)
    await publish_event(
        request,
        EVENTS.CONFIG_IMPORTED,
        actor=user,
        subject_id='import',
        data={'keys': list(form_data.config.keys())},
    )
    return await Config.get_all()


############################
# ExportConfig
############################


@router.get('/export', response_model=dict)
async def export_config(user=Depends(get_admin_user)):
    return await Config.get_all()


@router.get('/namespace/{namespace}', response_model=dict)
async def get_config_namespace(namespace: str, user=Depends(get_admin_user)):
    return await Config.get_namespace(namespace)


############################
# Oracle Monitoring Targets
############################


@router.get('/oracle-monitor/targets', response_model=dict)
async def get_oracle_monitor_targets(user=Depends(get_admin_user)):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    return {'targets': [_public_oracle_target(target) for target in targets]}


@router.get('/oracle-monitor/targets/monitoring', response_model=dict)
async def get_oracle_monitoring_targets(user=Depends(get_verified_user)):
    """Return non-secret target metadata for the on-demand monitoring card."""
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    return {'targets': [_public_oracle_target(target) for target in sorted(targets, key=_oracle_target_sort_key)]}


@router.post('/oracle-monitor/targets', response_model=dict)
async def set_oracle_monitor_targets(form_data: OracleMonitorTargetsForm, user=Depends(get_admin_user)):
    existing_targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    existing_by_id = {target.get('id'): target for target in existing_targets if target.get('id')}
    targets = []

    for form_target in form_data.targets:
        target = form_target.model_dump()
        target_id = target['id'] or str(uuid4())
        existing = existing_by_id.get(target_id, {})
        password = target.pop('password')
        oracle_os_password = target.pop('oracle_os_password')
        target['id'] = target_id
        target['password'] = encrypt_data(password) if password else existing.get('password')
        target['oracle_os_password'] = (
            encrypt_data(oracle_os_password) if oracle_os_password else existing.get('oracle_os_password')
        )
        targets.append(target)

    await Config.upsert({ORACLE_MONITOR_TARGETS_CONFIG_KEY: targets})
    return {'targets': [_public_oracle_target(target) for target in targets]}


@router.post('/oracle-monitor/status', response_model=dict)
async def get_oracle_monitor_status(user=Depends(get_verified_user)):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    results = await asyncio.gather(*[asyncio.to_thread(_oracle_monitor_status, target) for target in targets])
    response = {'targets': sorted(results, key=_oracle_target_sort_key)}
    await asyncio.gather(
        *[
            _record_oracle_monitor_history(
                'health_check', result, str(user.id), target=target, error_detail=result.get('detail')
            )
            for target, result in zip(targets, results, strict=True)
        ]
    )
    return response


@router.post('/oracle-monitor/status/{target_id}', response_model=dict)
async def get_oracle_monitor_target_status(target_id: str, user=Depends(get_verified_user)):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    response = await asyncio.to_thread(_oracle_monitor_status, target)
    await _record_oracle_monitor_history(
        'check_now', response, str(user.id), target=target, error_detail=response.get('detail')
    )
    return response


@router.post('/oracle-monitor/details/{target_id}/parameters', response_model=dict)
async def get_oracle_target_parameters(
    target_id: str,
    form_data: OracleParameterFilter,
    user=Depends(get_verified_user),
):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    try:
        response = await asyncio.to_thread(_oracle_details, target, 'parameters', form_data.name_filter)
        await _record_oracle_monitor_history('parameters', response, str(user.id), target=target)
        return response
    except Exception as exc:
        log.warning('Oracle parameter details query failed for %s: %s', target_id, exc)
        await _record_oracle_monitor_history(
            'parameters', {}, str(user.id), target=target, error_detail=str(exc)[:180]
        )
        raise HTTPException(status_code=502, detail=f'Parameter query failed: {str(exc)[:180]}') from exc


@router.post('/oracle-monitor/details/{target_id}/{detail_type}', response_model=dict)
async def get_oracle_target_details(
    target_id: str,
    detail_type: Literal['database', 'instance'],
    user=Depends(get_verified_user),
):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    try:
        response = await asyncio.to_thread(_oracle_details, target, detail_type)
        await _record_oracle_monitor_history(f'{detail_type}_details', response, str(user.id), target=target)
        return response
    except Exception as exc:
        log.warning('Oracle %s details query failed for %s: %s', detail_type, target_id, exc)
        await _record_oracle_monitor_history(
            f'{detail_type}_details', {}, str(user.id), target=target, error_detail=str(exc)[:180]
        )
        raise HTTPException(status_code=502, detail=f'{detail_type.title()} details query failed: {str(exc)[:180]}') from exc


@router.post('/oracle-monitor/logs/{target_id}/log/{log_type}', response_model=dict)
async def get_oracle_target_log(
    target_id: str,
    log_type: Literal['alert', 'listener'],
    user=Depends(get_verified_user),
):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    try:
        result = await asyncio.to_thread(_oracle_log, target, log_type)
        log_id = str(uuid4())
        ORACLE_LOG_DOWNLOADS[log_id] = {
            'path': result.pop('temp_path'),
            'owner_id': str(user.id),
            'filename': os.path.basename(result['path']) or f'{log_type}.log',
        }
        return {**result, 'log_id': log_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.warning('Oracle %s log retrieval failed for %s: %s', log_type, target_id, exc)
        raise HTTPException(status_code=502, detail=f'{log_type.title()} log retrieval failed: {str(exc)[:220]}') from exc


@router.post('/oracle-monitor/logs/{target_id}/trace', response_model=dict)
async def get_oracle_target_trace_log(
    target_id: str,
    form_data: OracleTraceFileRequest,
    user=Depends(get_verified_user),
):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    try:
        result = await asyncio.to_thread(_oracle_trace_log, target, form_data.path)
        log_id = str(uuid4())
        ORACLE_LOG_DOWNLOADS[log_id] = {
            'path': result.pop('temp_path'),
            'owner_id': str(user.id),
            'filename': os.path.basename(result['path']) or 'oracle-trace.trc',
        }
        return {**result, 'log_id': log_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.warning('Oracle trace log retrieval failed for %s: %s', target_id, exc)
        raise HTTPException(status_code=502, detail=f'Trace log retrieval failed: {str(exc)[:220]}') from exc


@router.get('/oracle-monitor/logs/download/{log_id}')
async def download_oracle_target_log(log_id: str, user=Depends(get_verified_user)):
    log_download = ORACLE_LOG_DOWNLOADS.get(log_id)
    if log_download is None or log_download['owner_id'] != str(user.id):
        raise HTTPException(status_code=404, detail='Temporary log download not found')
    if not os.path.exists(log_download['path']):
        ORACLE_LOG_DOWNLOADS.pop(log_id, None)
        raise HTTPException(status_code=404, detail='Temporary log file is no longer available')
    return FileResponse(log_download['path'], filename=log_download['filename'], media_type='text/plain')


@router.delete('/oracle-monitor/logs/{log_id}', response_model=dict)
async def delete_oracle_target_log(log_id: str, user=Depends(get_verified_user)):
    log_download = ORACLE_LOG_DOWNLOADS.get(log_id)
    if log_download is None or log_download['owner_id'] != str(user.id):
        raise HTTPException(status_code=404, detail='Temporary log download not found')
    ORACLE_LOG_DOWNLOADS.pop(log_id, None)
    try:
        os.unlink(log_download['path'])
    except FileNotFoundError:
        pass
    return {'deleted': True}


@router.get('/oracle-monitor/history', response_model=dict)
async def get_oracle_monitor_history(
    report_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
    user=Depends(get_verified_user),
):
    """Read persisted, non-secret monitoring snapshots for future chat and UI use."""
    safe_limit = min(max(limit, 1), 500)
    filters = []
    params = {'limit': safe_limit}
    if report_type:
        filters.append('report_type = :report_type')
        params['report_type'] = report_type
    if target_id:
        filters.append('target_id = :target_id')
        params['target_id'] = target_id
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''
    async with get_async_db() as db:
        await db.execute(text(ORACLE_MONITOR_HISTORY_DDL))
        result = await db.execute(
            text(
                f"""
                SELECT id, captured_at, captured_by, report_type, target_id, target_name,
                       service_name, protocol, success, error_detail, payload_json
                FROM oracle_monitor_history
                {where_clause}
                ORDER BY captured_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        rows = [dict(row) for row in result.mappings()]
    for row in rows:
        row['payload'] = json.loads(row.pop('payload_json'))
    return {'rows': rows}


@router.post('/oracle-monitor/current-usage/{target_id}', response_model=dict)
async def get_oracle_current_usage(target_id: str, user=Depends(get_verified_user)):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    if target.get('protocol', 'TCP').upper() == 'TCPS':
        raise HTTPException(status_code=400, detail='Current usage excludes TCPS targets')
    try:
        response = await asyncio.to_thread(_oracle_current_usage, target)
        await _record_oracle_monitor_history('current_usage', response, str(user.id), target=target)
        return response
    except Exception as exc:
        log.warning('Oracle current usage query failed for %s: %s', target_id, exc)
        await _record_oracle_monitor_history(
            'current_usage', {}, str(user.id), target=target, error_detail=str(exc)[:180]
        )
        raise HTTPException(status_code=502, detail=f'Current usage query failed: {str(exc)[:180]}') from exc


@router.post('/oracle-monitor/current-usage/{target_id}/kill', response_model=dict)
async def kill_oracle_inactive_sessions(
    target_id: str,
    form_data: OracleSessionKillRequest,
    user=Depends(get_verified_user),
):
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    target = next((item for item in targets if item.get('id') == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Configured Oracle monitoring target not found')
    if target.get('protocol', 'TCP').upper() == 'TCPS':
        raise HTTPException(status_code=400, detail='Current usage excludes TCPS targets')
    try:
        response = await asyncio.to_thread(_oracle_kill_inactive_sessions, target, form_data.sessions)
        # Keep a non-secret operator audit record alongside the monitoring
        # snapshots.  The selected session references are operational data and
        # make a later chat answer able to explain what was actually killed.
        await _record_oracle_monitor_history(
            'session_kill',
            {
                'selected': [session.model_dump() for session in form_data.sessions],
                **response,
            },
            str(user.id),
            target=target,
        )
        return response
    except Exception as exc:
        log.warning('Oracle session kill failed for %s: %s', target_id, exc)
        await _record_oracle_monitor_history(
            'session_kill',
            {'selected': [session.model_dump() for session in form_data.sessions]},
            str(user.id),
            target=target,
            error_detail=str(exc)[:180],
        )
        raise HTTPException(status_code=502, detail=f'Session kill failed: {str(exc)[:180]}') from exc


@router.post('/oracle-monitor/patch-level', response_model=dict)
async def get_oracle_patch_levels(user=Depends(get_verified_user)):
    """Collect DBA_REGISTRY_SQLPATCH rows from every configured database."""
    targets = [
        target
        for target in (await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or [])
        if target.get('protocol', 'TCP').upper() != 'TCPS'
    ]
    if not targets:
        return {'rows': [], 'errors': []}

    results = await asyncio.gather(
        *[asyncio.to_thread(_oracle_patch_level, target) for target in targets],
        return_exceptions=True,
    )
    rows = []
    errors = []
    for target, result in zip(targets, results, strict=True):
        if isinstance(result, Exception):
            errors.append(
                {
                    'database_name': target.get('name', 'Unnamed database'),
                    'service_name': target.get('service_name', ''),
                    'detail': str(result)[:180],
                }
            )
        else:
            rows.extend(result['rows'])
    rows.sort(key=lambda row: row.get('action_time') or '', reverse=True)
    rows.sort(key=_oracle_target_sort_key)
    errors.sort(key=_oracle_target_sort_key)
    response = {'rows': rows, 'errors': errors}
    await _record_oracle_monitor_history('patch_level', response, str(user.id))
    return response


@router.post('/oracle-monitor/inventory-health', response_model=dict)
async def get_oracle_inventory_health(user=Depends(get_verified_user)):
    """Refresh listener, database, session, and process health for all targets."""
    targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
    results = await asyncio.gather(
        *[asyncio.to_thread(_oracle_inventory_health, target) for target in targets],
        return_exceptions=True,
    )
    rows = []
    for target, result in zip(targets, results, strict=True):
        if isinstance(result, Exception):
            rows.append(
                {
                    **_public_oracle_target(target),
                    'listener_status': 'DOWN',
                    'database_status': 'DOWN',
                    'overall_status': 'DOWN',
                    'total_sessions': None,
                    'active_sessions': None,
                    'inactive_sessions': None,
                    'total_processes': None,
                    'detail': str(result)[:180],
                }
            )
        else:
            rows.append(result)
    rows.sort(key=_oracle_target_sort_key)
    response = {'rows': rows}
    await _record_oracle_monitor_history('inventory_health', response, str(user.id))
    return response


@router.post('/oracle-monitor/backup-status', response_model=dict)
async def get_oracle_backup_status(user=Depends(get_verified_user)):
    """Collect recent RMAN backup-job details from all configured databases."""
    targets = [
        target
        for target in (await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or [])
        if target.get('protocol', 'TCP').upper() != 'TCPS'
    ]
    results = await asyncio.gather(
        *[asyncio.to_thread(_oracle_backup_status, target) for target in targets],
        return_exceptions=True,
    )
    rows = []
    errors = []
    for target, result in zip(targets, results, strict=True):
        if isinstance(result, Exception):
            errors.append(
                {
                    'database_name': target.get('name', 'Unnamed database'),
                    'service_name': target.get('service_name', ''),
                    'protocol': target.get('protocol', 'TCP'),
                    'detail': str(result)[:180],
                }
            )
        else:
            rows.extend(result['rows'])
    rows.sort(key=lambda row: row.get('start_time') or '', reverse=True)
    rows.sort(key=_oracle_target_sort_key)
    errors.sort(key=_oracle_target_sort_key)
    response = {'rows': rows, 'errors': errors}
    await _record_oracle_monitor_history('backup_status', response, str(user.id))
    return response


############################
# Connections Config
############################


class ConnectionsConfigForm(BaseModel):
    ENABLE_DIRECT_CONNECTIONS: bool
    ENABLE_BASE_MODELS_CACHE: bool


@router.get('/connections', response_model=ConnectionsConfigForm)
async def get_connections_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(CONNECTIONS_CONFIG_KEYS)


@router.post('/connections', response_model=ConnectionsConfigForm)
async def set_connections_config(
    request: Request,
    form_data: ConnectionsConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), CONNECTIONS_CONFIG_KEYS))
    values = await get_config_values(CONNECTIONS_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_CONNECTIONS_UPDATED,
        actor=user,
        subject_id='connections',
        subject_type='config',
        data=values,
    )
    return values


class OAuthClientRegistrationForm(BaseModel):
    url: str
    client_id: str
    client_name: str | None = None
    client_secret: str | None = None
    oauth_server_url: str | None = None
    oauth_scope: str | None = None


@router.post('/oauth/clients/register')
async def register_oauth_client(
    request: Request,
    form_data: OAuthClientRegistrationForm,
    type: str | None = None,
    user=Depends(get_admin_user),
):
    try:
        oauth_client_id = form_data.client_id
        if type:
            oauth_client_id = f'{type}:{form_data.client_id}'

        oauth_server_url = form_data.oauth_server_url if form_data.oauth_server_url else form_data.url

        if form_data.client_secret:
            # Static credentials: skip dynamic registration, build from provided credentials
            oauth_client_info = await get_oauth_client_info_with_static_credentials(
                request,
                oauth_client_id,
                oauth_server_url,
                oauth_client_id=form_data.client_id,
                oauth_client_secret=form_data.client_secret,
                oauth_scope=form_data.oauth_scope,
            )
        else:
            oauth_client_info = await get_oauth_client_info_with_dynamic_client_registration(
                request, oauth_client_id, oauth_server_url, oauth_scope=form_data.oauth_scope
            )
        return {
            'status': True,
            'oauth_client_info': encrypt_data(oauth_client_info.model_dump(mode='json')),
        }
    except Exception as e:
        log.debug('Failed to register OAuth client: %s', e)
        raise HTTPException(
            status_code=400,
            detail=f'Failed to register OAuth client: {e}',
        )


############################
# ToolServers Config
############################


class ToolServerConnection(BaseModel):
    url: str
    path: str
    type: str | None = 'openapi'  # openapi, mcp
    auth_type: str | None
    headers: dict | str | None = None
    key: str | None
    config: dict | None
    info: dict | None = None

    model_config = ConfigDict(extra='allow')


class ToolServersConfigForm(BaseModel):
    TOOL_SERVER_CONNECTIONS: list[ToolServerConnection]


@router.get('/tool_servers', response_model=ToolServersConfigForm)
async def get_tool_servers_config(request: Request, user=Depends(get_admin_user)):
    return {'TOOL_SERVER_CONNECTIONS': await Config.get('tool_server.connections')}


@router.post('/tool_servers', response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_admin_user),
):
    existing_connections = await Config.get('tool_server.connections', []) or []
    for connection in existing_connections:
        server_type = connection.get('type', 'openapi')
        auth_type = connection.get('auth_type', 'none')

        if auth_type in ('oauth_2.1', 'oauth_2.1_static'):
            # Remove existing OAuth clients for tool servers
            server_id = (connection.get('info') or {}).get('id')
            client_key = f'{server_type}:{server_id}'

            try:
                request.app.state.oauth_client_manager.remove_client(client_key)
            except Exception:
                pass

    # Set new tool server connections
    connections = [connection.model_dump() for connection in form_data.TOOL_SERVER_CONNECTIONS]
    await Config.upsert({'tool_server.connections': connections})

    await set_tool_servers(request)

    for connection in connections:
        server_type = connection.get('type', 'openapi')
        if server_type == 'mcp':
            server_id = (connection.get('info') or {}).get('id')
            auth_type = connection.get('auth_type', 'none')

            if auth_type in ('oauth_2.1', 'oauth_2.1_static') and server_id:
                try:
                    oauth_client_info = resolve_oauth_client_info(connection)
                    oauth_client_info = await recover_static_oauth_client_metadata(connection, oauth_client_info)
                    oauth_client_info = apply_connection_oauth_options(connection, oauth_client_info)
                    request.app.state.oauth_client_manager.add_client(
                        f'{server_type}:{server_id}',
                        OAuthClientInformationFull(**oauth_client_info),
                    )
                except Exception as e:
                    log.debug(
                        'Failed to add OAuth client for MCP tool server %s: %s',
                        server_id,
                        f'{type(e).__name__}: {e}' if str(e) else type(e).__name__,
                    )
                    continue

    await publish_event(
        request,
        EVENTS.CONFIG_TOOL_SERVERS_UPDATED,
        actor=user,
        subject_id='tool_server.connections',
        subject_type='config',
        data={'count': len(connections), 'types': [connection.get('type', 'openapi') for connection in connections]},
    )
    return {'TOOL_SERVER_CONNECTIONS': connections}


class TerminalServerConnection(BaseModel):
    id: str | None = ''
    name: str | None = ''

    enabled: bool | None = True

    url: str
    path: str | None = '/openapi.json'

    key: str | None = ''
    auth_type: str | None = 'bearer'

    config: dict | None = None

    server_type: str | None = None
    policy_id: str | None = None

    model_config = ConfigDict(extra='allow')


class TerminalServersConfigForm(BaseModel):
    TERMINAL_SERVER_CONNECTIONS: list[TerminalServerConnection]


@router.get('/terminal_servers')
async def get_terminal_servers_config(request: Request, user=Depends(get_admin_user)):
    return {'TERMINAL_SERVER_CONNECTIONS': await Config.get('terminal_server.connections')}


@router.post('/terminal_servers')
async def set_terminal_servers_config(
    request: Request,
    form_data: TerminalServersConfigForm,
    user=Depends(get_admin_user),
):
    connections = [
        connection.model_dump(exclude={'policy', 'lifecycle'}) for connection in form_data.TERMINAL_SERVER_CONNECTIONS
    ]
    await Config.upsert({'terminal_server.connections': connections})

    await set_terminal_servers(request)

    await publish_event(
        request,
        EVENTS.CONFIG_TERMINAL_SERVERS_UPDATED,
        actor=user,
        subject_id='terminal_server.connections',
        subject_type='config',
        data={'count': len(connections)},
    )
    return {'TERMINAL_SERVER_CONNECTIONS': connections}


@router.post('/terminal_servers/verify')
async def verify_terminal_server_connection(
    request: Request, form_data: TerminalServerConnection, user=Depends(get_admin_user)
):
    """
    Verify the connection to a terminal server by detecting its type.

    Tries GET {url}/api/v1/policies (orchestrator) then GET {url}/api/config
    (plain terminal).  Returns ``{status: true, type: "orchestrator"|"terminal"}``.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            # Orchestrators expose a policies API; plain terminals don't.
            try:
                async with session.get(
                    f'{base_url}/api/v1/policies', headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL
                ) as resp:
                    if resp.ok:
                        return {'status': True, 'type': 'orchestrator'}
            except Exception:
                pass

            # Fall back to open-terminal config endpoint.
            try:
                async with session.get(
                    f'{base_url}/api/config', headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL
                ) as resp:
                    if resp.ok:
                        return {'status': True, 'type': 'terminal'}
            except Exception:
                pass

    except Exception as e:
        log.debug('Failed to connect to the terminal server: %s', e)

    raise HTTPException(status_code=400, detail='Failed to connect to the terminal server')


class TerminalServerPolicyForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    policy_data: dict | None = None


class TerminalServerLifecycleForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    lifecycle_data: dict | None = None


class TerminalServerRefreshForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    user_id: str | None = None
    policy_id: str | None = None
    only_idle: bool = True
    reset: bool = False


@router.post('/terminal_servers/policy')
async def put_terminal_server_policy(
    request: Request, form_data: TerminalServerPolicyForm, user=Depends(get_admin_user)
):
    """Proxy a policy read or update to an orchestrator terminal server."""
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            policy_url = f'{base_url}/api/v1/policies/{form_data.policy_id}'
            async with session.request(
                'GET' if form_data.policy_data is None else 'PUT',
                policy_url,
                headers=headers,
                json=form_data.policy_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug('Failed to access policy on terminal server: %s', e)
        raise HTTPException(status_code=400, detail='Failed to access policy on terminal server')


@router.post('/terminal_servers/lifecycle')
async def put_terminal_server_lifecycle(
    request: Request, form_data: TerminalServerLifecycleForm, user=Depends(get_admin_user)
):
    """Proxy a lifecycle read or update to an orchestrator terminal server."""
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            lifecycle_url = f'{base_url}/api/v1/policies/{form_data.policy_id}/lifecycle'
            async with session.request(
                'GET' if form_data.lifecycle_data is None else 'PUT',
                lifecycle_url,
                headers=headers,
                json=form_data.lifecycle_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug('Failed to access lifecycle on terminal server: %s', e)
        raise HTTPException(status_code=400, detail='Failed to access lifecycle on terminal server')


@router.post('/terminal_servers/refresh')
async def refresh_terminal_server_terminals(
    request: Request, form_data: TerminalServerRefreshForm, user=Depends(get_admin_user)
):
    """
    Proxy a terminal refresh request to an orchestrator terminal server.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    body = {
        'only_idle': form_data.only_idle,
        'reset': form_data.reset,
    }
    if form_data.user_id:
        body['user_id'] = form_data.user_id
    if form_data.policy_id:
        body['policy_id'] = form_data.policy_id

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            refresh_url = f'{base_url}/api/v1/terminals/refresh'
            async with session.post(
                refresh_url,
                headers=headers,
                json=body,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug('Failed to refresh terminals: %s', e)
        raise HTTPException(status_code=400, detail='Failed to refresh terminals')


@router.post('/tool_servers/verify')
async def verify_tool_servers_config(request: Request, form_data: ToolServerConnection, user=Depends(get_admin_user)):
    """
    Verify the connection to the tool server.
    """
    try:
        if form_data.type == 'mcp':
            if form_data.auth_type in ('oauth_2.1', 'oauth_2.1_static'):
                oauth_server_url = (
                    form_data.info.get('oauth_server_url')
                    if form_data.info and form_data.info.get('oauth_server_url')
                    else form_data.url
                )
                discovery_urls = await get_discovery_urls(oauth_server_url)
                for discovery_url in discovery_urls:
                    log.debug('Trying to fetch OAuth 2.1 discovery document from %s', discovery_url)
                    async with aiohttp.ClientSession(
                        trust_env=True,
                        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
                    ) as session:
                        async with session.get(
                            discovery_url, ssl=AIOHTTP_CLIENT_SESSION_SSL
                        ) as oauth_server_metadata_response:
                            if oauth_server_metadata_response.status == 200:
                                try:
                                    oauth_server_metadata = OAuthMetadata.model_validate(
                                        await oauth_server_metadata_response.json()
                                    )
                                    return {
                                        'status': True,
                                        'oauth_server_metadata': oauth_server_metadata.model_dump(mode='json'),
                                    }
                                except Exception as e:
                                    log.info('Failed to parse OAuth 2.1 discovery document: %s', e)
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f'Failed to parse OAuth 2.1 discovery document from {discovery_url}',
                                    )

                raise HTTPException(
                    status_code=400,
                    detail=f'Failed to fetch OAuth 2.1 discovery document from {discovery_urls}',
                )
            else:
                try:
                    client = MCPClient()
                    headers = None

                    token = None
                    if form_data.auth_type == 'bearer':
                        token = form_data.key
                    elif form_data.auth_type == 'session':
                        token = request.state.token.credentials
                    elif form_data.auth_type == 'system_oauth':
                        oauth_token = None
                        try:
                            if request.cookies.get('oauth_session_id', None):
                                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                                    user.id,
                                    request.cookies.get('oauth_session_id', None),
                                )

                                if oauth_token:
                                    token = oauth_token.get('access_token', '')
                        except Exception as e:
                            pass
                    if token:
                        headers = {'Authorization': f'Bearer {token}'}

                    if form_data.headers and isinstance(form_data.headers, dict):
                        if headers is None:
                            headers = {}
                        custom_headers = await get_custom_headers(form_data.headers, user)
                        headers.update(custom_headers)

                    await client.connect(form_data.url, headers=headers)
                    specs = await client.list_tool_specs()
                    return {
                        'status': True,
                        'specs': specs,
                    }
                except Exception as e:
                    log.debug('Failed to create MCP client: %s', e)
                    raise HTTPException(
                        status_code=400,
                        detail=f'Failed to create MCP client',
                    )
                finally:
                    if client:
                        await client.disconnect()
        else:  # openapi
            token = None
            headers = None
            if form_data.auth_type == 'bearer':
                token = form_data.key
            elif form_data.auth_type == 'session':
                token = request.state.token.credentials
            elif form_data.auth_type == 'system_oauth':
                try:
                    if request.cookies.get('oauth_session_id', None):
                        oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                            user.id,
                            request.cookies.get('oauth_session_id', None),
                        )

                        if oauth_token:
                            token = oauth_token.get('access_token', '')

                except Exception as e:
                    pass

            if token:
                headers = {'Authorization': f'Bearer {token}'}

            if form_data.headers and isinstance(form_data.headers, dict):
                if headers is None:
                    headers = {}
                custom_headers = await get_custom_headers(form_data.headers, user)
                headers.update(custom_headers)

            url = get_tool_server_url(form_data.url, form_data.path)
            return await get_tool_server_data(url, headers=headers)
    except HTTPException as e:
        raise e
    except Exception as e:
        log.debug('Failed to connect to the tool server: %s', e)
        raise HTTPException(
            status_code=400,
            detail=f'Failed to connect to the tool server',
        )


############################
# CodeInterpreterConfig
############################
class CodeInterpreterConfigForm(BaseModel):
    ENABLE_CODE_EXECUTION: bool
    CODE_EXECUTION_ENGINE: str
    CODE_EXECUTION_JUPYTER_URL: str | None
    CODE_EXECUTION_JUPYTER_AUTH: str | None
    CODE_EXECUTION_JUPYTER_AUTH_TOKEN: str | None
    CODE_EXECUTION_JUPYTER_AUTH_PASSWORD: str | None
    CODE_EXECUTION_JUPYTER_TIMEOUT: int | None
    ENABLE_CODE_INTERPRETER: bool
    CODE_INTERPRETER_ENGINE: str
    CODE_INTERPRETER_PROMPT_TEMPLATE: str | None
    CODE_INTERPRETER_JUPYTER_URL: str | None
    CODE_INTERPRETER_JUPYTER_AUTH: str | None
    CODE_INTERPRETER_JUPYTER_AUTH_TOKEN: str | None
    CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD: str | None
    CODE_INTERPRETER_JUPYTER_TIMEOUT: int | None


@router.get('/code_execution', response_model=CodeInterpreterConfigForm)
async def get_code_execution_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(CODE_EXECUTION_CONFIG_KEYS)


@router.post('/code_execution', response_model=CodeInterpreterConfigForm)
async def set_code_execution_config(
    request: Request, form_data: CodeInterpreterConfigForm, user=Depends(get_admin_user)
):
    await Config.upsert(config_updates(form_data.model_dump(), CODE_EXECUTION_CONFIG_KEYS))
    values = await get_config_values(CODE_EXECUTION_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_CODE_EXECUTION_UPDATED,
        actor=user,
        subject_id='code_execution',
        subject_type='config',
        data={
            'code_execution_enabled': values.get('ENABLE_CODE_EXECUTION'),
            'code_execution_engine': values.get('CODE_EXECUTION_ENGINE'),
            'code_interpreter_enabled': values.get('ENABLE_CODE_INTERPRETER'),
            'code_interpreter_engine': values.get('CODE_INTERPRETER_ENGINE'),
        },
    )
    return values


############################
# SetDefaultModels
############################
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: str | None
    DEFAULT_PINNED_MODELS: str | None
    MODEL_ORDER_LIST: list[str] | None
    DEFAULT_MODEL_METADATA: dict | None = None
    DEFAULT_MODEL_PARAMS: dict | None = None


@router.get('/models/defaults')
async def get_models_defaults(request: Request, user=Depends(get_verified_user)):
    return {
        'DEFAULT_MODEL_METADATA': await Config.get('models.default_metadata'),
    }


@router.get('/models', response_model=ModelsConfigForm)
async def get_models_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(MODELS_CONFIG_KEYS)


@router.post('/models', response_model=ModelsConfigForm)
async def set_models_config(request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)):
    await Config.upsert(config_updates(form_data.model_dump(), MODELS_CONFIG_KEYS))
    values = await get_config_values(MODELS_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_MODELS_UPDATED,
        actor=user,
        subject_id='models',
        subject_type='config',
        data={
            'default_models': values.get('DEFAULT_MODELS'),
            'default_pinned_models': values.get('DEFAULT_PINNED_MODELS'),
            'model_order_count': len(values.get('MODEL_ORDER_LIST') or []),
        },
    )
    return values


class SubagentsConfigForm(BaseModel):
    ENABLE_SUBAGENTS: bool
    SUBAGENTS_BACKGROUND_ENABLED: bool
    SUBAGENTS_MAX_CONCURRENT: int
    SUBAGENTS_MAX_ASYNC: int
    SUBAGENTS_MAX_ITERATIONS: int
    SUBAGENTS_MAX_OUTPUT: int
    SUBAGENTS_SYSTEM_PROMPT: str


@router.get('/subagents', response_model=SubagentsConfigForm)
async def get_subagents_config(user=Depends(get_admin_user)):
    return await get_config_values(SUBAGENTS_CONFIG_KEYS)


@router.post('/subagents', response_model=SubagentsConfigForm)
async def set_subagents_config(
    request: Request,
    form_data: SubagentsConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), SUBAGENTS_CONFIG_KEYS))
    values = await get_config_values(SUBAGENTS_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_UPDATED,
        actor=user,
        subject_id='subagents',
        subject_type='config',
        data={'enabled': values.get('ENABLE_SUBAGENTS')},
    )
    return values


class PromptSuggestion(BaseModel):
    title: list[str]
    content: str


class SetDefaultSuggestionsForm(BaseModel):
    suggestions: list[PromptSuggestion]


@router.post('/suggestions', response_model=list[PromptSuggestion])
async def set_default_suggestions(
    request: Request,
    form_data: SetDefaultSuggestionsForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    await Config.upsert({'ui.prompt_suggestions': data['suggestions']})
    suggestions = await Config.get('ui.prompt_suggestions')
    await publish_event(
        request,
        EVENTS.CONFIG_SUGGESTIONS_UPDATED,
        actor=user,
        subject_id='ui.prompt_suggestions',
        subject_type='config',
        data={'count': len(suggestions or [])},
    )
    return suggestions


############################
# SetBanners
############################


class SetBannersForm(BaseModel):
    banners: list[BannerModel]


@router.post('/banners', response_model=list[BannerModel])
async def set_banners(
    request: Request,
    form_data: SetBannersForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    await Config.upsert({'ui.banners': data['banners']})
    banners = await Config.get('ui.banners')
    await publish_event(
        request,
        EVENTS.CONFIG_BANNERS_UPDATED,
        actor=user,
        subject_id='ui.banners',
        subject_type='config',
        data={'count': len(banners or [])},
    )
    return banners


@router.get('/banners', response_model=list[BannerModel])
async def get_banners(
    request: Request,
    user=Depends(get_verified_user),
):
    return await Config.get('ui.banners')
