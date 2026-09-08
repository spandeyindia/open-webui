<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getOracleInventoryHealth } from '$lib/apis/configs';
	import { exportRowsToExcel } from '$lib/utils/excel';

	type HealthRow = {
		id: string;
		name: string;
		service_name: string;
		protocol: string;
		listener_status: string;
		database_status: string;
		overall_status: string;
		total_sessions: number | null;
		active_sessions: number | null;
		inactive_sessions: number | null;
		total_processes: number | null;
		detail?: string | null;
	};

	let rows: HealthRow[] = [];
	let loading = false;
	let loaded = false;
	const exportColumns = [
		{ key: 'name', title: 'Database' }, { key: 'service_name', title: 'Service name' }, { key: 'protocol', title: 'Protocol' },
		{ key: 'listener_status', title: 'Listener' }, { key: 'database_status', title: 'Database status' }, { key: 'overall_status', title: 'Status' },
		{ key: 'active_sessions', title: 'Active' }, { key: 'inactive_sessions', title: 'Inactive' }, { key: 'total_sessions', title: 'Total sessions' },
		{ key: 'total_processes', title: 'Total processes' }, { key: 'detail', title: 'Detail' }
	];

	const statusClass = (status: string) =>
		status === 'UP'
			? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
			: status === 'DEGRADED'
				? 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
				: 'bg-red-500/15 text-red-700 dark:text-red-300';

	const refresh = async () => {
		loading = true;
		try {
			const result = await getOracleInventoryHealth(localStorage.token);
			rows = result.rows;
			loaded = true;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	onMount(refresh);
</script>

<section class="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white/95 shadow-sm dark:border-slate-700 dark:bg-slate-900/95">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
		<div class="text-left"><div class="font-semibold text-slate-800 dark:text-slate-100">Registered database health</div><div class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">Listener, database, connection protocol, sessions, and Oracle processes</div></div>
		<div class="flex gap-2"><button class="rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={rows.length === 0} on:click={() => exportRowsToExcel('registered-database-health', exportColumns, rows)}>Export Excel</button><button class="rounded-lg bg-cyan-700 px-3 py-2 text-sm text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={loading} on:click={refresh}>{loading ? 'Refreshing...' : 'Refresh'}</button></div>
	</div>
	{#if rows.length > 0}
		<div class="min-h-0 flex-1 overflow-auto">
			<table class="min-w-max w-full text-left text-xs">
				<thead class="sticky top-0 bg-slate-50 text-slate-500 dark:bg-slate-800 dark:text-slate-400"><tr><th class="px-3 py-2">Database</th><th class="px-3 py-2">Service name</th><th class="px-3 py-2">Protocol</th><th class="px-3 py-2">Listener</th><th class="px-3 py-2">Database</th><th class="px-3 py-2">Status</th><th class="px-3 py-2 text-right">Active</th><th class="px-3 py-2 text-right">Inactive</th><th class="px-3 py-2 text-right">Total sessions</th><th class="px-3 py-2 text-right">Total processes</th><th class="px-3 py-2">Detail</th></tr></thead>
				<tbody>{#each rows as row (row.id)}<tr class="border-t border-slate-100 dark:border-slate-800"><td class="px-3 py-2 font-medium">{row.name}</td><td class="px-3 py-2">{row.service_name}</td><td class="px-3 py-2">{row.protocol}</td><td class="px-3 py-2"><span class={`rounded-full px-2 py-0.5 font-medium ${statusClass(row.listener_status)}`}>{row.listener_status}</span></td><td class="px-3 py-2"><span class={`rounded-full px-2 py-0.5 font-medium ${statusClass(row.database_status)}`}>{row.database_status}</span></td><td class="px-3 py-2"><span class={`rounded-full px-2 py-0.5 font-medium ${statusClass(row.overall_status)}`}>{row.overall_status}</span></td><td class="px-3 py-2 text-right">{row.active_sessions ?? '-'}</td><td class="px-3 py-2 text-right">{row.inactive_sessions ?? '-'}</td><td class="px-3 py-2 text-right">{row.total_sessions ?? '-'}</td><td class="px-3 py-2 text-right">{row.total_processes ?? '-'}</td><td class="max-w-80 px-3 py-2 text-slate-500">{row.detail ?? '-'}</td></tr>{/each}</tbody>
			</table>
		</div>
	{:else if loaded && !loading}
		<p class="px-4 py-6 text-left text-sm text-slate-500">No registered Oracle databases. Add targets in Admin Panel &gt; Settings &gt; Database.</p>
	{/if}
</section>
