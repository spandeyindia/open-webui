"""Configurable JDE DevOps DBA-Ops home-page cards.

Card definitions are supplied through JDEOPS_HOME_CARDS.  API credentials, when
needed, stay in the server-only apiHeaders field and are never returned to the
browser.
"""

import asyncio
import logging
import os
from typing import Any, Literal

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from open_webui.config import JSONCodec
from open_webui.models.config import Config
from open_webui.routers.configs import (
    ORACLE_MONITOR_TARGETS_CONFIG_KEY,
    _oracle_current_usage_summary,
    _oracle_monitor_status,
)
from open_webui.utils.auth import get_verified_user

router = APIRouter()
log = logging.getLogger(__name__)


class JdeOpsCard(BaseModel):
    id: str
    title: str
    url: str
    target_type: Literal['api', 'dashboard', 'oracle_monitor', 'oracle_connectivity', 'oracle_current_usage', 'oracle_patch_level', 'oracle_backup_status', 'space_analysis', 'knowledge_resources'] = Field(
        default='dashboard', validation_alias=AliasChoices('targetType', 'target_type')
    )
    description: str | None = None
    api_url: str | None = Field(default=None, validation_alias=AliasChoices('apiUrl', 'api_url'))
    api_headers: dict[str, str] = Field(
        default_factory=dict, validation_alias=AliasChoices('apiHeaders', 'api_headers')
    )
    status_path: str | None = Field(
        default=None, validation_alias=AliasChoices('statusPath', 'status_path')
    )
    detail_path: str | None = Field(
        default=None, validation_alias=AliasChoices('detailPath', 'detail_path')
    )
    updated_at_path: str | None = Field(
        default=None, validation_alias=AliasChoices('updatedAtPath', 'updated_at_path')
    )
    refresh_seconds: int = Field(
        default=60, ge=10, le=3600, validation_alias=AliasChoices('refreshSeconds', 'refresh_seconds')
    )

    def public(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'url': self.url,
            'targetType': self.target_type,
            'description': self.description,
            'hasStatus': (self.target_type == 'api' and bool(self.api_url))
            or self.target_type in {'oracle_connectivity', 'oracle_current_usage'},
            'refreshSeconds': self.refresh_seconds,
        }


def _production_database_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='production-database',
        title='Production Database',
        url='',
        target_type='oracle_monitor',
        description='On-demand Oracle database and listener status',
    )


def _production_database_connectivity_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='production-database-connectivity',
        title='Production Database Connectivity Status',
        url='',
        target_type='oracle_connectivity',
        description='Aggregate listener and Oracle database connectivity',
    )


def _database_current_usage_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='database-current-usage',
        title='Database Current Usage',
        url='',
        target_type='oracle_current_usage',
        description='User-session totals across configured databases',
    )


def _db_current_patch_level_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='db-current-patch-level',
        title='DB Current Patch Level',
        url='',
        target_type='oracle_patch_level',
        description='SQL patch inventory from configured databases',
    )


def _db_backup_status_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='db-backup-status',
        title='DB Backup Status',
        url='',
        target_type='oracle_backup_status',
        description='Recent RMAN backup status across configured databases',
    )


def _space_analysis_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='space-analysis',
        title='Space Analysis',
        url='',
        target_type='space_analysis',
        description='Database storage, tablespace, and growth analysis',
    )


def _knowledge_resources_card() -> JdeOpsCard:
    return JdeOpsCard(
        id='knowledge-resources',
        title='Knowledge Resources',
        url='',
        target_type='knowledge_resources',
        description='DBA runbooks, reference material, and operational guidance',
    )


def load_cards() -> list[JdeOpsCard]:
    cards = []
    try:
        raw_cards = JSONCodec.loads(os.getenv('JDEOPS_HOME_CARDS', '[]'))
        if not isinstance(raw_cards, list):
            raise ValueError('JDEOPS_HOME_CARDS must be a JSON array')
        cards = [JdeOpsCard.model_validate(card) for card in raw_cards]
    except Exception as exc:
        log.exception('Error loading JDEOPS_HOME_CARDS: %s', exc)
    production_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'production-database' or card.title.lower() == 'production database'
        ),
        None,
    )
    if production_card_index is None:
        cards.append(_production_database_card())
    else:
        cards[production_card_index] = cards[production_card_index].model_copy(
            update={'target_type': 'oracle_monitor', 'url': ''}
        )
    connectivity_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'production-database-connectivity'
            or card.title.lower() == 'production database connectivity status'
        ),
        None,
    )
    if connectivity_card_index is None:
        cards.append(_production_database_connectivity_card())
    else:
        cards[connectivity_card_index] = cards[connectivity_card_index].model_copy(
            update={'target_type': 'oracle_connectivity', 'url': ''}
        )
    usage_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'database-current-usage' or card.title.lower() == 'database current usage'
        ),
        None,
    )
    if usage_card_index is None:
        cards.append(_database_current_usage_card())
    else:
        cards[usage_card_index] = cards[usage_card_index].model_copy(
            update={'target_type': 'oracle_current_usage', 'url': ''}
        )
    patch_level_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'db-current-patch-level' or card.title.lower() == 'db current patch level'
        ),
        None,
    )
    if patch_level_card_index is None:
        cards.append(_db_current_patch_level_card())
    else:
        cards[patch_level_card_index] = cards[patch_level_card_index].model_copy(
            update={'target_type': 'oracle_patch_level', 'url': ''}
        )
    backup_status_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'db-backup-status' or card.title.lower() == 'db backup status'
        ),
        None,
    )
    if backup_status_card_index is None:
        cards.append(_db_backup_status_card())
    else:
        cards[backup_status_card_index] = cards[backup_status_card_index].model_copy(
            update={'target_type': 'oracle_backup_status', 'url': ''}
        )
    space_analysis_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'space-analysis' or card.title.lower() == 'space analysis'
        ),
        None,
    )
    if space_analysis_card_index is None:
        cards.append(_space_analysis_card())
    else:
        cards[space_analysis_card_index] = cards[space_analysis_card_index].model_copy(
            update={'target_type': 'space_analysis', 'url': ''}
        )
    knowledge_resources_card_index = next(
        (
            index
            for index, card in enumerate(cards)
            if card.id == 'knowledge-resources' or card.title.lower() == 'knowledge resources'
        ),
        None,
    )
    if knowledge_resources_card_index is None:
        cards.append(_knowledge_resources_card())
    else:
        cards[knowledge_resources_card_index] = cards[knowledge_resources_card_index].model_copy(
            update={'target_type': 'knowledge_resources', 'url': ''}
        )
    return cards


def json_path(value: Any, path: str | None) -> Any:
    if not path:
        return None
    current = value
    for key in path.split('.'):
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit():
            index = int(key)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def get_card(card_id: str) -> JdeOpsCard:
    for card in load_cards():
        if card.id == card_id:
            return card
    raise HTTPException(status_code=404, detail='Configured JDE Ops card not found')


@router.get('/cards')
async def get_cards(user=Depends(get_verified_user)):
    return [card.public() for card in load_cards()]


@router.get('/cards/{card_id}/status')
async def get_card_status(card_id: str, user=Depends(get_verified_user)):
    card = get_card(card_id)
    if card.target_type == 'oracle_connectivity':
        targets = await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or []
        if not targets:
            return {
                'status': 'Not configured',
                'detail': 'Add remote Oracle targets in Admin Panel > Settings > Database.',
            }
        results = await asyncio.gather(
            *[asyncio.to_thread(_oracle_monitor_status, target) for target in targets]
        )
        reachable = sum(
            result['listener_status'] == 'UP' and result['database_status'] == 'UP'
            for result in results
        )
        total = len(results)
        status = 'Operational' if reachable == total else 'Degraded' if reachable else 'Down'
        return {
            'status': status,
            'detail': f'{reachable}/{total} configured databases reachable',
        }
    if card.target_type == 'oracle_current_usage':
        targets = [
            target
            for target in (await Config.get(ORACLE_MONITOR_TARGETS_CONFIG_KEY, []) or [])
            if target.get('protocol', 'TCP').upper() != 'TCPS'
        ]
        if not targets:
            return {
                'status': 'Not configured',
                'detail': 'Add remote Oracle targets in Admin Panel > Settings > Database.',
            }
        results = await asyncio.gather(
            *[asyncio.to_thread(_oracle_current_usage_summary, target) for target in targets],
            return_exceptions=True,
        )
        summaries = [result for result in results if isinstance(result, dict)]
        total_sessions = sum(result['total_sessions'] for result in summaries)
        active_sessions = sum(result['active_sessions'] for result in summaries)
        inactive_sessions = sum(result['inactive_sessions'] for result in summaries)
        return {
            'status': f'{total_sessions} sessions',
            'detail': f'{active_sessions} active · {inactive_sessions} inactive across {len(summaries)}/{len(targets)} databases',
        }
    if card.target_type != 'api' or not card.api_url:
        raise HTTPException(status_code=400, detail='This card does not have a JSON status endpoint')

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(card.api_url, headers=card.api_headers) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        log.warning('JDE Ops status request failed for %s: %s', card_id, exc)
        raise HTTPException(status_code=502, detail='Status endpoint is unavailable') from exc

    return {
        'status': json_path(payload, card.status_path),
        'detail': json_path(payload, card.detail_path),
        'updatedAt': json_path(payload, card.updated_at_path),
    }
