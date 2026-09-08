<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getOracleMonitoringTargets, getOraclePatchLevels } from '$lib/apis/configs';
	import { exportRowsToExcel } from '$lib/utils/excel';

	type Target = { id: string; name: string; protocol: string };
	type PatchRow = {
		target_id: string;
		database_name: string;
		service_name: string;
		protocol: string;
		patch_id: string | number | null;
		patch_type: string | null;
		action: string | null;
		status: string | null;
		action_time: string | null;
		source_version: string | null;
		target_version: string | null;
		description: string | null;
	};
	type TargetError = { database_name: string; service_name: string; detail: string };
	type PatchSummary = {
		database_name: string;
		service_name: string;
		protocol: string;
		patched_component: string;
		database_release: string;
		cpu_ru_version: string;
		patch_id: string;
		current_patch_status: string;
	};

	export let onBack: () => void = () => {};

	const columns = [
		['database_name', 'Database name'],
		['service_name', 'Service name'],
		['protocol', 'Protocol'],
		['patch_id', 'Patch ID'],
		['patch_type', 'Patch type'],
		['action', 'Action'],
		['status', 'Status'],
		['action_time', 'Action time'],
		['source_version', 'Source version'],
		['target_version', 'Target database release'],
		['description', 'Description']
	] as const;
	const summaryColumns = [
		{ key: 'database_name', title: 'Database name' }, { key: 'service_name', title: 'Service name' },
		{ key: 'patched_component', title: 'Patched component' }, { key: 'database_release', title: 'Database release' },
		{ key: 'cpu_ru_version', title: 'CPU / RU version' }, { key: 'patch_id', title: 'Patch ID' },
		{ key: 'current_patch_status', title: 'Current patch status' }
	];

	let targets: Target[] = [];
	let selectedTargetId = '';
	let rows: PatchRow[] = [];
	let errors: TargetError[] = [];
	let filters: Record<string, string> = {};
	let loading = false;
	let loaded = false;
	let showSummary = false;

	$: tabRows = selectedTargetId ? rows.filter((row) => row.target_id === selectedTargetId) : [];
	$: filteredRows = tabRows.filter((row) =>
		columns.every(([key]) =>
			String(row[key] ?? '')
				.toLowerCase()
				.includes((filters[key] ?? '').trim().toLowerCase())
		)
	);
	const patchVersion = (description: string | null) =>
		description?.match(/\b\d+(?:\.\d+){3,}\b/)?.[0] ?? '-';

	const patchedComponent = (description: string | null, version: string) => {
		if (!description) return '-';
		const versionIndex = description.indexOf(version);
		return (versionIndex >= 0 ? description.slice(0, versionIndex) : description)
			.replace(/[\s:()\-]+$/, '')
			.trim();
	};

	const isReleaseUpdate = (row: PatchRow) =>
		row.status === 'SUCCESS' && row.action === 'APPLY' &&
		(/^(RU|RUR)$/i.test(row.patch_type ?? '') || /release update/i.test(row.description ?? ''));

	$: patchSummary = Object.values(
		[...filteredRows].sort((left, right) => {
			const leftPriority = isReleaseUpdate(left) ? 1 : 0;
			const rightPriority = isReleaseUpdate(right) ? 1 : 0;
			return rightPriority - leftPriority || (right.action_time ?? '').localeCompare(left.action_time ?? '');
		}).reduce<Record<string, PatchSummary>>((summary, row) => {
			const key = `${row.database_name}\u0000${row.service_name}\u0000${row.protocol}`;
			if (!summary[key]) {
				const version = patchVersion(row.description);
				const patch = row.patch_id ? `Patch ${row.patch_id}` : 'Patch ID unavailable';
				const action = row.action ? `${row.action} ` : '';
				summary[key] = {
					database_name: row.database_name,
					service_name: row.service_name,
					protocol: row.protocol,
					patched_component: patchedComponent(row.description, version),
					database_release: row.target_version ?? '-',
					cpu_ru_version: version,
					patch_id: row.patch_id ? String(row.patch_id) : '-',
					current_patch_status: `${row.status ?? 'UNKNOWN'} — ${action}${patch}`
				};
			}
			return summary;
		}, {})
	).sort(
		(left, right) =>
			left.database_name.localeCompare(right.database_name) ||
			left.service_name.localeCompare(right.service_name) ||
			left.protocol.localeCompare(right.protocol)
	);

	const exportPatch = () => {
		if (showSummary) exportRowsToExcel('db-current-patch-level-summary', summaryColumns, patchSummary);
		else exportRowsToExcel('db-current-patch-level', columns.map(([key, title]) => ({ key, title })), filteredRows);
	};

	const loadPatchLevels = async () => {
		loading = true;
		try {
			const result = await getOraclePatchLevels(localStorage.token);
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
			if (selectedTargetId) await loadPatchLevels();
			else loaded = true;
		} catch (error) {
			toast.error(`${error}`);
		}
	});
</script>

<div class="flex min-h-0 flex-1 flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3 dark:border-slate-700">
		<div class="flex min-w-0 flex-1 gap-2 overflow-x-auto" role="tablist" aria-label="Configured databases">{#each targets as target (target.id)}<button class={`shrink-0 rounded-lg px-3 py-2 text-sm transition ${selectedTargetId === target.id ? 'bg-cyan-700 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'}`} role="tab" aria-selected={selectedTargetId === target.id} on:click={() => { selectedTargetId = target.id; showSummary = false; }}>{target.name}</button>{/each}</div>
		<div class="flex shrink-0 gap-2"><button class="rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={showSummary ? patchSummary.length === 0 : filteredRows.length === 0} on:click={exportPatch}>Export Excel</button><button class="rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={tabRows.length === 0} on:click={() => (showSummary = !showSummary)}>{showSummary ? 'Details' : 'Summary'}</button><button class="rounded-lg bg-cyan-700 px-3 py-2 text-sm text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={loading} on:click={loadPatchLevels}>{loading ? 'Refreshing...' : 'Refresh'}</button><button class="rounded-lg px-3 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={onBack}>Back to health</button></div>
	</div>

	{#if errors.length > 0}
		<div class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
			{#each errors as error, index (index)}
				<div>{error.database_name}{error.service_name ? ` (${error.service_name})` : ''}: {error.detail}</div>
			{/each}
		</div>
	{/if}

	{#if showSummary && patchSummary.length > 0}
		<div class="min-h-0 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="min-w-full text-left text-xs">
				<thead class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400"><tr><th class="px-3 py-2 font-medium">Database name</th><th class="px-3 py-2 font-medium">Service name</th><th class="px-3 py-2 font-medium">Patched component</th><th class="px-3 py-2 font-medium">Database release</th><th class="px-3 py-2 font-medium">CPU / RU version</th><th class="px-3 py-2 font-medium">Patch ID</th><th class="px-3 py-2 font-medium">Current patch status</th></tr></thead>
				<tbody>{#each patchSummary as row (row.database_name + row.service_name + row.protocol)}<tr class="border-t border-gray-100 dark:border-gray-800"><td class="px-3 py-2 font-medium">{row.database_name}</td><td class="px-3 py-2">{row.service_name}</td><td class="px-3 py-2">{row.patched_component}</td><td class="px-3 py-2">{row.database_release}</td><td class="px-3 py-2">{row.cpu_ru_version}</td><td class="px-3 py-2">{row.patch_id}</td><td class="px-3 py-2">{row.current_patch_status}</td></tr>{/each}</tbody>
			</table>
		</div>
	{:else if tabRows.length > 0}
		<div class="min-h-0 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="min-w-max text-left text-xs">
				<thead class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
					<tr>{#each columns as [, title]}<th class="whitespace-nowrap px-3 py-2 font-medium">{title}</th>{/each}</tr>
					<tr class="border-t border-gray-200 dark:border-gray-700">
						{#each columns as [key, title]}
							<th class="px-2 pb-2"><input class="w-full min-w-28 rounded border border-gray-200 bg-white px-2 py-1 text-xs font-normal text-gray-700 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200" aria-label={`Filter ${title}`} placeholder="Filter" value={filters[key] ?? ''} on:input={(event) => (filters = { ...filters, [key]: event.currentTarget.value })} /></th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each filteredRows as row, index (index)}
						<tr class="border-t border-gray-100 dark:border-gray-800">{#each columns as [key]}<td class="max-w-96 px-3 py-2 align-top">{row[key] ?? '-'}</td>{/each}</tr>
					{/each}
					{#if filteredRows.length === 0}<tr><td class="px-3 py-4 text-gray-500" colspan={columns.length}>No patch records match the selected filters.</td></tr>{/if}
				</tbody>
			</table>
		</div>
	{:else if loaded && !loading}
		<p class="text-xs text-gray-500 dark:text-gray-400">No SQL patch records were returned. Configure an Oracle target, or review any connection errors above.</p>
	{/if}
</div>
