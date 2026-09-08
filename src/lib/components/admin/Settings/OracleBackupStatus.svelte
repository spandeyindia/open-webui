<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getOracleBackupStatus, getOracleMonitoringTargets } from '$lib/apis/configs';
	import { exportRowsToExcel } from '$lib/utils/excel';

	type BackupRow = {
		target_id: string;
		database_name: string;
		service_name: string;
		protocol: string;
		input_type: string | null;
		status: string | null;
		start_time: string | null;
		end_time: string | null;
		elapsed_seconds: number | null;
		output_bytes_display: string | null;
		time_taken_display: string | null;
		compression_ratio: number | null;
	};
	type TargetError = { database_name: string; service_name: string; protocol: string; detail: string };
	type Target = { id: string; name: string; protocol: string };

	export let onBack: () => void = () => {};

	const columns = [
		['database_name', 'Database name'], ['service_name', 'Service name'], ['protocol', 'Protocol'],
		['input_type', 'Backup type'], ['status', 'Status'], ['start_time', 'Start time'],
		['end_time', 'End time'], ['elapsed_seconds', 'Elapsed seconds'], ['output_bytes_display', 'Output size'],
		['time_taken_display', 'Time taken'], ['compression_ratio', 'Compression ratio']
	] as const;

	let targets: Target[] = [];
	let selectedTargetId = '';
	let rows: BackupRow[] = [];
	let errors: TargetError[] = [];
	let filters: Record<string, string> = {};
	let loading = false;
	let loaded = false;

	$: tabRows = selectedTargetId ? rows.filter((row) => row.target_id === selectedTargetId) : [];
	$: filteredRows = tabRows.filter((row) => columns.every(([key]) => String(row[key] ?? '').toLowerCase().includes((filters[key] ?? '').trim().toLowerCase())));

	const refresh = async () => {
		loading = true;
		try {
			const result = await getOracleBackupStatus(localStorage.token);
			rows = result.rows;
			errors = result.errors;
			errors.forEach((target) => toast.error(`${target.database_name}: ${target.detail}`));
			loaded = true;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	onMount(async () => {
		try {
			const inventory = await getOracleMonitoringTargets(localStorage.token);
			targets = (inventory.targets as Target[]).filter((target) => target.protocol !== 'TCPS');
			selectedTargetId = targets[0]?.id ?? '';
			if (selectedTargetId) await refresh();
			else loaded = true;
		} catch (error) {
			toast.error(`${error}`);
		}
	});
</script>

<div class="flex min-h-0 flex-1 flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3 dark:border-slate-700">
		<div class="flex min-w-0 flex-1 gap-2 overflow-x-auto" role="tablist" aria-label="Configured databases">{#each targets as target (target.id)}<button class={`shrink-0 rounded-lg px-3 py-2 text-sm transition ${selectedTargetId === target.id ? 'bg-cyan-700 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'}`} role="tab" aria-selected={selectedTargetId === target.id} on:click={() => (selectedTargetId = target.id)}>{target.name}</button>{/each}</div>
		<div class="flex shrink-0 gap-2"><button class="rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={filteredRows.length === 0} on:click={() => exportRowsToExcel('db-backup-status', columns.map(([key, title]) => ({ key, title })), filteredRows)}>Export Excel</button><button class="rounded-lg bg-cyan-700 px-3 py-2 text-sm text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={loading} on:click={refresh}>{loading ? 'Refreshing...' : 'Refresh'}</button><button class="rounded-lg px-3 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={onBack}>Back to health</button></div>
	</div>
	{#if errors.length > 0}
		<div class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{#each errors as error, index (index)}<div>{error.database_name} ({error.service_name}, {error.protocol}): {error.detail}</div>{/each}</div>
	{/if}
	{#if tabRows.length > 0}
		<div class="min-h-0 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="min-w-max text-left text-xs">
				<thead class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400"><tr>{#each columns as [, title]}<th class="whitespace-nowrap px-3 py-2 font-medium">{title}</th>{/each}</tr><tr class="border-t border-gray-200 dark:border-gray-700">{#each columns as [key, title]}<th class="px-2 pb-2"><input class="w-full min-w-24 rounded border border-gray-200 bg-white px-2 py-1 text-xs font-normal text-gray-700 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200" aria-label={`Filter ${title}`} placeholder="Filter" value={filters[key] ?? ''} on:input={(event) => (filters = { ...filters, [key]: event.currentTarget.value })} /></th>{/each}</tr></thead>
				<tbody>{#each filteredRows as row, index (index)}<tr class="border-t border-gray-100 dark:border-gray-800">{#each columns as [key]}<td class="whitespace-nowrap px-3 py-2">{row[key] ?? '-'}</td>{/each}</tr>{/each}{#if filteredRows.length === 0}<tr><td class="px-3 py-4 text-gray-500" colspan={columns.length}>No backup jobs match the selected filters.</td></tr>{/if}</tbody>
			</table>
		</div>
	{:else if loaded && !loading}
		<p class="text-xs text-gray-500 dark:text-gray-400">No RMAN backup jobs were returned. Check the configured database connection or backup history.</p>
	{/if}
</div>
