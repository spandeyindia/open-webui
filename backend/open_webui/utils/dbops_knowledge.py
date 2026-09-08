"""Read-only DbOps monitoring history tool provisioning."""

from __future__ import annotations

from open_webui.models.tools import ToolForm, ToolMeta, Tools
from open_webui.models.users import Users
from open_webui.utils.plugin import load_tool_module_by_id
from open_webui.utils.tools import get_tool_specs

DBOPS_KNOWLEDGE_TOOL_ID = 'dbops_monitoring_knowledge'

# This is persisted as an Open WebUI Tool so native and non-native function
# calling models can use the same read-only knowledge source in every chat.
DBOPS_KNOWLEDGE_TOOL = r'''
"""
title: DbOps Monitoring Knowledge
description: Read-only access to non-secret DbOps monitoring history.
version: 1.0.0
"""

class Tools:
    def search_dbops_monitoring_history(
        self,
        question: str = '',
        database: str = '',
        report_type: str = '',
        limit: int = 12,
    ) -> str:
        """Use for DbOps questions about database health, sessions, capacity, patching,
        RMAN backups, connection errors, or session-kill actions. Returns only stored,
        non-secret monitoring results; never use it for passwords or alert/trace logs.
        """
        import json
        import sqlite3
        from open_webui.env import DATA_DIR

        limit = max(1, min(int(limit), 30))
        conn = sqlite3.connect(str(DATA_DIR / 'webui.db'))
        conn.row_factory = sqlite3.Row
        filters, params = [], []
        if database.strip():
            filters.append('(LOWER(target_name) LIKE ? OR LOWER(service_name) LIKE ?)')
            term = f"%{database.strip().lower()}%"
            params.extend([term, term])
        if report_type.strip():
            filters.append('report_type = ?')
            params.append(report_type.strip())
        if question.strip():
            filters.append('(LOWER(target_name) LIKE ? OR LOWER(service_name) LIKE ? OR LOWER(error_detail) LIKE ? OR LOWER(payload_json) LIKE ?)')
            term = f"%{question.strip().lower()}%"
            params.extend([term, term, term, term])
        where = (' WHERE ' + ' AND '.join(filters)) if filters else ''
        rows = conn.execute(
            'SELECT captured_at, report_type, target_name, service_name, protocol, success, error_detail, payload_json '
            'FROM oracle_monitor_history' + where + ' ORDER BY captured_at DESC LIMIT ?',
            [*params, limit],
        ).fetchall()
        conn.close()
        if not rows:
            return 'No matching DbOps monitoring history is stored yet. Run the relevant card report or Check Now first.'
        results = []
        for row in rows:
            payload = json.loads(row['payload_json'])
            summary = payload.get('summary') if isinstance(payload, dict) else None
            results.append({
                'captured_at_epoch': row['captured_at'],
                'report_type': row['report_type'],
                'database': row['target_name'],
                'service': row['service_name'],
                'protocol': row['protocol'],
                'success': bool(row['success']),
                'error': row['error_detail'],
                'metrics': summary if isinstance(summary, dict) else payload,
            })
        return json.dumps(results, default=str)
'''


async def ensure_dbops_monitoring_knowledge_tool() -> None:
    """Create the shared, read-only monitoring tool once an administrator exists."""
    if await Tools.get_tool_by_id(DBOPS_KNOWLEDGE_TOOL_ID):
        return
    owner = await Users.get_super_admin_user()
    if owner is None:
        return
    module, _ = await load_tool_module_by_id(DBOPS_KNOWLEDGE_TOOL_ID, content=DBOPS_KNOWLEDGE_TOOL)
    await Tools.insert_new_tool(
        owner.id,
        ToolForm(
            id=DBOPS_KNOWLEDGE_TOOL_ID,
            name='DbOps Monitoring Knowledge',
            content=DBOPS_KNOWLEDGE_TOOL,
            meta=ToolMeta(description='Read-only monitoring history for DbOps questions.'),
            access_grants=[{'principal_type': 'anyone', 'principal_id': '*', 'permission': 'read'}],
        ),
        get_tool_specs(module),
    )
