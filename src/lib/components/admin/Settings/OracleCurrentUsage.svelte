<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getOracleCurrentUsage, getOracleMonitoringTargets, killOracleInactiveSessions } from '$lib/apis/configs';
	import { exportRowsToExcel } from '$lib/utils/excel';
	import Modal from '$lib/components/common/Modal.svelte';

	type Target = { id: string; name: string; host: string; port: number; protocol: string; service_name: string };
	type Summary = {
		total_sessions: number;
		active_sessions: number;
		inactive_sessions: number;
		tcp_sessions: number;
		tcps_sessions: number;
		review_sessions: number;
	};

	export let onBack: () => void = () => {};

	let targets: Target[] = [];
	let selectedTargetId = '';
	let summary: Summary | null = null;
	let rows: Record<string, unknown>[] = [];
	let loading = false;
	let killing = false;
	let selectedSessionKeys: string[] = [];
	let selectedSession: Record<string, unknown> | null = null;
	let sessionDetailsShow = false;

	const columns = [
		['select', 'Select'], ['sid', 'SID'], ['serial_number', 'Serial#'], ['username', 'Username'],
		['status', 'Status'], ['age', 'Age'], ['service_name', 'Service'], ['machine', 'Machine'], ['program', 'Program'],
		['logon_time', 'Logon time'], ['transport', 'Transport'], ['last_sql', 'Last SQL']
	] as const;
	const exportColumns = [
		{ key: 'inst_id', title: 'Instance' }, ...columns.filter(([key]) => key !== 'select').map(([key, title]) => ({ key, title }))
	];

	const sessionKey = (row: Record<string, unknown>) => `${row.inst_id}:${row.sid}:${row.serial_number}`;
	const toggleSession = (row: Record<string, unknown>) => {
		const key = sessionKey(row);
		selectedSessionKeys = selectedSessionKeys.includes(key)
			? selectedSessionKeys.filter((selectedKey) => selectedKey !== key)
			: [...selectedSessionKeys, key];
	};
	const openSessionDetails = (row: Record<string, unknown>) => {
		selectedSession = row;
		sessionDetailsShow = true;
	};

	const loadUsage = async () => {
		if (!selectedTargetId) return;
		loading = true;
		try {
			const result = await getOracleCurrentUsage(localStorage.token, selectedTargetId);
			summary = result.summary;
			rows = result.rows;
			selectedSessionKeys = [];
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const killSelectedSessions = async () => {
		if (!selectedTargetId || selectedSessionKeys.length === 0) return;
		const selectedRows = rows.filter((row) => selectedSessionKeys.includes(sessionKey(row)));
		killing = true;
		try {
			const result = await killOracleInactiveSessions(
				localStorage.token,
				selectedTargetId,
				selectedRows.map((row) => ({
					inst_id: Number(row.inst_id),
					sid: Number(row.sid),
					serial_number: Number(row.serial_number)
				}))
			);
			if (result.killed.length > 0) toast.success(`${result.killed.length} inactive session(s) killed`);
			result.skipped.forEach((session: { session: string; reason: string }) => toast.error(`${session.session}: ${session.reason}`));
			await loadUsage();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			killing = false;
		}
	};

	onMount(async () => {
		try {
			const result = await getOracleMonitoringTargets(localStorage.token);
			targets = result.targets.filter((target: Target) => target.protocol !== 'TCPS');
			selectedTargetId = targets[0]?.id ?? '';
			if (selectedTargetId) await loadUsage();
		} catch (error) {
			toast.error(`${error}`);
		}
	});
</script>

<div class="flex min-h-0 flex-1 flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3 dark:border-slate-700">
		<div class="flex min-w-0 flex-1 gap-2 overflow-x-auto" role="tablist" aria-label="Configured databases">
			{#each targets as target (target.id)}
				<button class={`shrink-0 rounded-lg px-3 py-2 text-sm transition ${selectedTargetId === target.id ? 'bg-cyan-700 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'}`} role="tab" aria-selected={selectedTargetId === target.id} on:click={() => { selectedTargetId = target.id; loadUsage(); }}>{target.name}</button>
			{/each}
		</div>
		<div class="flex shrink-0 gap-2"><button class="rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={rows.length === 0} on:click={() => exportRowsToExcel('database-current-usage', exportColumns, rows)}>Export Excel</button><button class="rounded-lg bg-red-700 px-3 py-2 text-sm font-semibold text-white shadow-[0_3px_0_#7f1d1d] transition hover:bg-red-800 active:translate-y-0.5 active:shadow-none disabled:cursor-not-allowed disabled:opacity-50" type="button" disabled={selectedSessionKeys.length === 0 || killing} on:click={killSelectedSessions}>{killing ? 'Killing...' : `Kill Selected Sessions${selectedSessionKeys.length ? ` (${selectedSessionKeys.length})` : ''}`}</button><button class="rounded-lg px-3 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={onBack}>Back to health</button></div>
	</div>

	{#if summary}
		<div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
			User sessions: <span class="font-semibold">{summary.total_sessions}</span> · Active: <span class="font-semibold text-emerald-700 dark:text-emerald-300">{summary.active_sessions}</span> · Inactive: <span class="animate-pulse font-semibold text-red-700 dark:text-red-300">{summary.inactive_sessions}</span> · TCP: <span class="font-semibold">{summary.tcp_sessions}</span> · TCPS: <span class="font-semibold">{summary.tcps_sessions}</span> · Review banner: <span class="font-semibold">{summary.review_sessions}</span>
		</div>
	{/if}

	{#if rows.length > 0}
		<div class="min-h-0 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="min-w-max text-left text-xs">
				<thead class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400"><tr>{#each columns as [, title]}<th class="whitespace-nowrap px-3 py-2">{title}</th>{/each}</tr></thead>
				<tbody>{#each rows as row, index (index)}<tr class={`border-t border-gray-100 dark:border-gray-800 ${row.status === 'INACTIVE' ? 'bg-red-950 text-red-50 dark:bg-red-950' : ''}`}>{#each columns as [key]}<td class="max-w-96 px-3 py-2 align-top">{#if key === 'select'}{#if row.status === 'INACTIVE'}<input type="checkbox" class="size-4 accent-red-600" checked={selectedSessionKeys.includes(sessionKey(row))} aria-label={`Select inactive session ${sessionKey(row)}`} on:change={() => toggleSession(row)} />{:else}<span class="text-gray-400">-</span>{/if}{:else if key === 'last_sql'}<button class="whitespace-nowrap text-cyan-700 underline hover:text-cyan-900 dark:text-cyan-300 dark:hover:text-cyan-100" type="button" on:click={() => openSessionDetails(row)}>View details</button>{:else}{row[key] ?? '-'}{/if}</td>{/each}</tr>{/each}</tbody>
			</table>
		</div>
	{:else if !loading}
		<p class="text-xs text-gray-500 dark:text-gray-400">Select a TCP database and run the report. TCPS targets are excluded; the query returns only Oracle USER sessions.</p>
	{/if}
</div>

{#if selectedSession}
	<Modal bind:show={sessionDetailsShow} size="full" containerClassName="p-4" className="overflow-hidden rounded-2xl bg-white dark:bg-gray-900">
		<div class="mx-auto flex max-h-[80vh] min-h-[24rem] w-full max-w-[68rem] flex-col p-5">
			<div class="mb-4 flex shrink-0 items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-700"><div class="font-semibold">Session details — {sessionKey(selectedSession)}</div><button class="rounded-lg px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={() => (sessionDetailsShow = false)}>Close</button></div>
			<div class="min-h-0 min-w-0 w-full max-w-full overflow-y-auto overflow-x-hidden">
				<div class="grid w-full min-w-0 max-w-full grid-cols-1 gap-y-2 text-left text-xs">{#each Object.entries(selectedSession).filter(([key]) => key !== 'last_sql' && key !== 'network_service_banners') as [key, value]}<div class="min-w-0 max-w-full border-b border-slate-100 py-1 dark:border-slate-800"><span class="text-slate-500">{key}</span><div class="mt-0.5 min-w-0 max-w-full break-all [overflow-wrap:anywhere] font-medium">{value ?? '-'}</div></div>{/each}</div>
				<div class="mt-5 min-w-0 text-left"><div class="mb-2 text-sm font-semibold">Last SQL fired</div><div class="m-0 max-h-80 min-w-0 max-w-full overflow-auto whitespace-pre-wrap break-all [overflow-wrap:anywhere] rounded-lg bg-slate-950 p-3 text-left font-mono text-xs text-slate-100">{selectedSession.last_sql ?? 'SQL text is unavailable; the cursor may have aged out of the shared pool.'}</div></div>
			</div>
		</div>
	</Modal>
{/if}
