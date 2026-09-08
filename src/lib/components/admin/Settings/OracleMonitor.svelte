<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getOracleMonitoringTargets,
		getOracleMonitorStatus,
		getOracleMonitorTargetStatus,
		deleteOracleTargetLog,
		downloadOracleTargetLog,
		getOracleTargetDetails,
		getOracleTargetLog,
		getOracleTargetParameters,
		getOracleTargetTraceLog,
		getOracleMonitorTargets,
		setOracleMonitorTargets
	} from '$lib/apis/configs';
	import { exportRowsToExcel } from '$lib/utils/excel';
	import Modal from '$lib/components/common/Modal.svelte';

	export let mode: 'admin' | 'dashboard' = 'admin';
	export let showInstanceTiming = false;

	type Target = {
		id?: string;
		name: string;
		host: string;
		port: number;
		protocol: 'TCP' | 'TCPS';
		service_name: string;
		username: string;
		password?: string;
		oracle_os_owner?: string;
		oracle_os_password?: string;
		oracle_base?: string;
		connect_timeout_seconds: number;
		has_password?: boolean;
		has_oracle_os_password?: boolean;
	};
	type Status = {
		id: string;
		name: string;
		listener_status: string;
		database_status: string;
		instance_name?: string;
		instance_status?: string;
		open_mode?: string;
		database_role?: string;
		version_full?: string;
		startup_time?: string;
		uptime?: string;
		fra_available_mb?: string | number;
		fra_available_pct?: string | number;
		detail?: string;
	};
	type DetailType = 'database' | 'instance' | 'parameters';
	type DetailRow = Record<string, string | null>;

	let targets: Target[] = [];
	let statuses: Record<string, Status> = {};
	let loading = false;
	let checkingTargetIds: string[] = [];
	let saving = false;
	let detailsShow = false;
	let detailsLoading = false;
	let detailType: DetailType = 'database';
	let detailTarget: Target | null = null;
	let detailColumns: string[] = [];
	let detailRows: DetailRow[] = [];
	let parameterNameFilter = '';
	let detailRequestSequence = 0;
	let logShow = false;
	let logLoading = false;
	let logTitle = '';
	let logPath = '';
	let logContent = '';
	let logId = '';
	let deletingLog = false;
	let logFullscreen = false;
	let logTarget: Target | null = null;
	let logLines: string[] = [];
	let logSearch = '';
	let activeSearchMatch = 0;
	let goToLineNumber = '';
	$: sortedTargets = [...targets].sort((left, right) =>
		left.name.localeCompare(right.name) ||
		left.service_name.localeCompare(right.service_name) ||
		(left.protocol ?? 'TCP').localeCompare(right.protocol ?? 'TCP')
	);
	$: filteredParameterRows = detailRows.filter((row) =>
		String(row.name ?? '').toLowerCase().includes(parameterNameFilter.trim().toLowerCase())
	);
	const databaseDisplayName = (target: Target) =>
		target.name.replace(/\s*-\s*(TCP|TCPS)\s*$/i, '').trim() || target.name;
	const statusCellClass = (status?: string) =>
		status === 'UP'
			? 'bg-emerald-600 font-bold text-white dark:bg-emerald-700'
			: status === 'DOWN'
				? 'bg-red-600 font-bold text-white dark:bg-red-700'
				: '';
	$: tableRows = sortedTargets.map((target, index) => {
		const databaseName = databaseDisplayName(target);
		const groupKey = `${databaseName}\u0000${target.service_name}`;
		const previousTarget = sortedTargets[index - 1];
		const startsGroup = !previousTarget || `${databaseDisplayName(previousTarget)}\u0000${previousTarget.service_name}` !== groupKey;
		return {
			target,
			databaseName,
			startsGroup,
			rowSpan: startsGroup ? sortedTargets.filter((item) => `${databaseDisplayName(item)}\u0000${item.service_name}` === groupKey).length : 0
		};
	});

	const blankTarget = (): Target => ({
		name: '',
		host: '',
		port: 1521,
		protocol: 'TCP',
		service_name: '',
		username: 'SYS',
		password: '',
		oracle_os_owner: '',
		oracle_os_password: '',
		oracle_base: '',
		connect_timeout_seconds: 5
	});

	const loadTargets = async () => {
		const result = await getOracleMonitorTargets(localStorage.token);
		targets = result.targets.map((target: Target) => ({ ...target, protocol: target.protocol ?? 'TCP', password: '', oracle_os_password: '' }));
	};

	const loadMonitoringTargets = async () => {
		const result = await getOracleMonitoringTargets(localStorage.token);
		targets = result.targets.map((target: Target) => ({ ...target, protocol: target.protocol ?? 'TCP', password: '', oracle_os_password: '' }));
	};

	const checkNow = async () => {
		loading = true;
		try {
			const result = await getOracleMonitorStatus(localStorage.token);
			statuses = Object.fromEntries(result.targets.map((status: Status) => [status.id, status]));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const checkTarget = async (target: Target) => {
		if (!target.id) return;
		checkingTargetIds = [...checkingTargetIds, target.id];
		try {
			const status = await getOracleMonitorTargetStatus(localStorage.token, target.id);
			statuses = { ...statuses, [target.id]: status };
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			checkingTargetIds = checkingTargetIds.filter((id) => id !== target.id);
		}
	};

	const loadTargetDetails = async (target: Target, type: DetailType, nameFilter = '') => {
		if (!target.id) return;
		const requestSequence = ++detailRequestSequence;
		detailsLoading = true;
		try {
			const result = type === 'parameters'
				? await getOracleTargetParameters(localStorage.token, target.id, nameFilter)
				: await getOracleTargetDetails(localStorage.token, target.id, type);
			if (requestSequence !== detailRequestSequence) return;
			detailColumns = result.columns;
			detailRows = result.rows;
		} catch (error) {
			if (requestSequence === detailRequestSequence) toast.error(`${error}`);
		} finally {
			if (requestSequence === detailRequestSequence) detailsLoading = false;
		}
	};

	const openTargetDetails = async (target: Target, type: DetailType) => {
		detailTarget = target;
		detailType = type;
		parameterNameFilter = '';
		detailColumns = [];
		detailRows = [];
		detailsShow = true;
		await loadTargetDetails(target, type);
	};

	const refreshParameters = async () => {
		if (detailTarget && detailType === 'parameters') await loadTargetDetails(detailTarget, 'parameters');
	};

	const openTargetLog = async (target: Target, logType: 'alert' | 'listener') => {
		if (!target.id) return;
		logTitle = `${logType === 'alert' ? 'Database alert log' : 'Listener log'} — ${databaseDisplayName(target)}`;
		logPath = '';
		logContent = '';
		logId = '';
		logFullscreen = false;
		logTarget = target;
		logSearch = '';
		activeSearchMatch = 0;
		goToLineNumber = '';
		logShow = true;
		logLoading = true;
		try {
			const result = await getOracleTargetLog(localStorage.token, target.id, logType);
			logPath = result.path;
			logContent = result.content;
			logId = result.log_id;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			logLoading = false;
		}
	};

	const deleteTemporaryLog = async () => {
		if (!logId || deletingLog) return;
		const temporaryLogId = logId;
		logId = '';
		deletingLog = true;
		try {
			await deleteOracleTargetLog(localStorage.token, temporaryLogId);
		} catch (error) {
			toast.error(`Unable to remove temporary log: ${error}`);
		} finally {
			deletingLog = false;
		}
	};

	const downloadLog = async () => {
		if (!logId) return;
		try {
			const { blob, filename } = await downloadOracleTargetLog(localStorage.token, logId);
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = filename;
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const isTraceFilePath = (value: string) => /^\/[\w./-]+\.(?:trc|trm)$/i.test(value);
	$: logLines = logContent ? logContent.split(/\r?\n/) : [];
	$: matchingLogLineIndexes = logSearch.trim()
		? logLines.reduce<number[]>((matches, line, index) => {
			if (line.toLowerCase().includes(logSearch.trim().toLowerCase())) matches.push(index);
			return matches;
		}, [])
		: [];

	const scrollToLogLine = (lineIndex: number) =>
		document.getElementById(`oracle-log-line-${lineIndex}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });

	const goToSearchMatch = (direction: 1 | -1) => {
		if (matchingLogLineIndexes.length === 0) return;
		activeSearchMatch = (activeSearchMatch + direction + matchingLogLineIndexes.length) % matchingLogLineIndexes.length;
		scrollToLogLine(matchingLogLineIndexes[activeSearchMatch]);
	};

	const goToRequestedLine = () => {
		const lineNumber = Number(goToLineNumber);
		if (!Number.isInteger(lineNumber) || lineNumber < 1 || lineNumber > logLines.length) {
			toast.error(`Enter a line number from 1 to ${logLines.length}`);
			return;
		}
		scrollToLogLine(lineNumber - 1);
	};

	const openTraceFile = async (tracePath: string) => {
		if (!logTarget || !isTraceFilePath(tracePath)) return;
		await deleteTemporaryLog();
		logLoading = true;
		logPath = tracePath;
		logContent = '';
		logTitle = `Trace file — ${tracePath.split('/').pop()}`;
		logSearch = '';
		activeSearchMatch = 0;
		goToLineNumber = '';
		try {
			const result = await getOracleTargetTraceLog(localStorage.token, logTarget.id ?? '', tracePath);
			logPath = result.path;
			logContent = result.content;
			logId = result.log_id;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			logLoading = false;
		}
	};

	$: if (!logShow && logId) deleteTemporaryLog();

	const exportStatus = () =>
		exportRowsToExcel(
			'production-database-status',
			[
				{ key: 'database_name', title: 'Database' }, { key: 'host', title: 'Host' }, { key: 'port', title: 'Port' },
				{ key: 'protocol', title: 'Protocol' }, { key: 'service_name', title: 'Service' }, { key: 'listener_status', title: 'Listener' },
				{ key: 'database_status', title: 'Database status' }, { key: 'instance_name', title: 'Instance' }, { key: 'version_full', title: 'Oracle full version' },
				...(showInstanceTiming ? [{ key: 'startup_time', title: 'Startup time' }, { key: 'uptime', title: 'Uptime' }, { key: 'fra_available_mb', title: 'FRA available (MB)' }, { key: 'fra_available_pct', title: 'FRA available (%)' }] : []),
				{ key: 'detail', title: 'Detail' }
			],
			sortedTargets.map((target) => ({
				database_name: target.name,
				host: target.host,
				port: target.port,
				protocol: target.protocol ?? 'TCP',
				service_name: target.service_name,
				listener_status: target.id ? statuses[target.id]?.listener_status ?? '-' : '-',
				database_status: target.id ? statuses[target.id]?.database_status ?? '-' : '-',
				instance_name: target.id ? statuses[target.id]?.instance_name ?? '-' : '-',
				version_full: target.id ? statuses[target.id]?.version_full ?? '-' : '-',
				startup_time: target.id ? statuses[target.id]?.startup_time ?? '-' : '-',
				uptime: target.id ? statuses[target.id]?.uptime ?? '-' : '-',
				fra_available_mb: target.id ? statuses[target.id]?.fra_available_mb ?? '-' : '-',
				fra_available_pct: target.id ? statuses[target.id]?.fra_available_pct ?? '-' : '-',
				detail: target.id ? statuses[target.id]?.detail ?? '-' : '-'
			}))
		);

	const saveTargets = async () => {
		saving = true;
		try {
			const result = await setOracleMonitorTargets(localStorage.token, targets);
			targets = result.targets.map((target: Target) => ({ ...target, password: '', oracle_os_password: '' }));
			toast.success('Oracle monitoring targets saved');
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		try {
			if (mode === 'admin') {
				await loadTargets();
			}
			else {
				await loadMonitoringTargets();
			}
		} catch (error) {
			toast.error(`${error}`);
		}
	});
</script>

{#if mode === 'admin'}
	<div class="space-y-3">
		<p class="text-xs text-gray-500 dark:text-gray-400">
			Every target connects as SYS with SYSDBA. Credentials are encrypted on the server and are never displayed again. Configure each remote Oracle 19c database once; the Production Database cards use this same list.
		</p>
		{#each targets as target, index (target.id ?? index)}
			<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
				<div class="mb-2 flex items-center justify-between gap-3">
					<span class="text-xs font-medium text-gray-700 dark:text-gray-200">Database {index + 1}</span>
					<button class="text-xs text-red-600 hover:underline dark:text-red-400" type="button" on:click={() => (targets = targets.filter((_, i) => i !== index))}>Remove</button>
				</div>
				<div class="grid grid-cols-1 gap-2 md:grid-cols-3">
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.name} placeholder="Display name" />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.host} placeholder="Host or IP address" />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.port} type="number" min="1" max="65535" placeholder="Listener port" />
					<select class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.protocol} aria-label="Database protocol"><option value="TCP">TCP</option><option value="TCPS">TCPS</option></select>
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.service_name} placeholder="Service name" />
					<input class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-800" value="SYS (SYSDBA)" disabled aria-label="Monitoring user" />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.password} type="password" placeholder={target.has_password ? 'Password saved - enter to replace' : 'Password'} />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.oracle_os_owner} placeholder="Oracle OS owner (for logs)" />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.oracle_os_password} type="password" placeholder={target.has_oracle_os_password ? 'Oracle OS password saved - enter to replace' : 'Oracle OS password (for logs)'} />
					<input class="rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700" bind:value={target.oracle_base} placeholder="ORACLE_BASE (for logs)" />
				</div>
			</div>
		{/each}
		<div class="flex items-center gap-3">
			<button class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-800" type="button" on:click={() => (targets = [...targets, blankTarget()])}>Add database</button>
			<button class="rounded-lg bg-cyan-700 px-3 py-1.5 text-xs text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={saving} on:click={saveTargets}>{saving ? 'Saving...' : 'Save databases'}</button>
		</div>
	</div>
{/if}

<div class="flex min-h-0 flex-1 flex-col" class:mt-5={mode === 'admin'}>
	<div class="mb-2 flex items-center justify-between gap-3">
		<div>
			<h3 class="text-xs font-medium text-gray-800 dark:text-gray-100">Remote database status</h3>
			<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">Listener reachability and current Oracle instance status are checked only when requested.</p>
		</div>
		<div class="flex gap-2"><button class="rounded-lg border border-cyan-700 px-3 py-1.5 text-xs text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={sortedTargets.length === 0} on:click={exportStatus}>Export Excel</button><button class="rounded-lg bg-cyan-700 px-3 py-1.5 text-xs text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={loading} on:click={checkNow}>{loading ? 'Checking...' : 'Check now'}</button></div>
	</div>
	{#if sortedTargets.length > 0}
		<div class="min-h-0 flex-1 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="min-w-max text-center text-xs">
				<thead class="bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400"><tr><th class="px-3 py-2">Database</th><th class="px-3 py-2">Host</th><th class="px-3 py-2">Service</th><th class="px-3 py-2">Instance</th><th class="px-3 py-2">Port</th><th class="px-3 py-2">Protocol</th><th class="px-3 py-2">Listener</th><th class="px-3 py-2">DB status</th><th class="px-3 py-2">Oracle full version</th>{#if showInstanceTiming}<th class="px-3 py-2">Startup time</th><th class="px-3 py-2">Uptime</th><th class="px-3 py-2">FRA available (MB)</th><th class="px-3 py-2">FRA available (%)</th><th class="px-3 py-2">DB details</th><th class="px-3 py-2">Instance details</th><th class="px-3 py-2">DB parameters</th>{/if}<th class="px-3 py-2">Detail</th><th class="px-3 py-2">Action</th></tr></thead>
				<tbody>{#each tableRows as tableRow, index (tableRow.target.id ?? index)}{@const target = tableRow.target}{@const status = target.id ? statuses[target.id] : undefined}<tr class="border-t border-gray-100 dark:border-gray-800">{#if tableRow.startsGroup}<td class="px-3 py-2 align-middle font-medium" rowspan={tableRow.rowSpan}>{tableRow.databaseName}</td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{target.host}</td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{target.service_name}</td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.instance_name ?? '-'}{status?.instance_status ? ` (${status.instance_status})` : ''}</td>{/if}<td class="px-3 py-2 align-middle">{target.port}</td><td class="px-3 py-2 align-middle">{target.protocol ?? 'TCP'}</td><td class={`px-3 py-2 align-middle ${statusCellClass(status?.listener_status)}`}><button class="font-inherit underline disabled:no-underline" type="button" disabled={!target.id || !status?.listener_status} on:click={() => openTargetLog(target, 'listener')}>{status?.listener_status ?? '-'}</button></td><td class={`px-3 py-2 align-middle ${statusCellClass(status?.database_status)}`}><button class="font-inherit underline disabled:no-underline" type="button" disabled={!target.id || !status?.database_status} on:click={() => openTargetLog(target, 'alert')}>{status?.database_status ?? '-'}</button></td>{#if tableRow.startsGroup}<td class="whitespace-nowrap px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.version_full ?? '-'}</td>{#if showInstanceTiming}<td class="whitespace-nowrap px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.startup_time ?? '-'}</td><td class="whitespace-nowrap px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.uptime ?? '-'}</td><td class="whitespace-nowrap px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.fra_available_mb ?? '-'}</td><td class="whitespace-nowrap px-3 py-2 align-middle" rowspan={tableRow.rowSpan}>{status?.fra_available_pct ?? '-'}</td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}><button class="whitespace-nowrap text-cyan-700 underline hover:text-cyan-900 dark:text-cyan-300" type="button" disabled={!target.id} on:click={() => openTargetDetails(target, 'database')}>View</button></td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}><button class="whitespace-nowrap text-cyan-700 underline hover:text-cyan-900 dark:text-cyan-300" type="button" disabled={!target.id} on:click={() => openTargetDetails(target, 'instance')}>View</button></td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}><button class="whitespace-nowrap text-cyan-700 underline hover:text-cyan-900 dark:text-cyan-300" type="button" disabled={!target.id} on:click={() => openTargetDetails(target, 'parameters')}>View</button></td>{/if}<td class="max-w-72 px-3 py-2 align-middle text-gray-500" rowspan={tableRow.rowSpan}>{status?.detail ?? '-'}</td><td class="px-3 py-2 align-middle" rowspan={tableRow.rowSpan}><button class="whitespace-nowrap rounded-lg bg-cyan-700 px-2.5 py-1 text-xs text-white hover:bg-cyan-800 disabled:opacity-50" type="button" disabled={!target.id || checkingTargetIds.includes(target.id)} on:click={() => checkTarget(target)}>{target.id && checkingTargetIds.includes(target.id) ? 'Checking...' : 'Check now'}</button></td>{/if}</tr>{/each}</tbody>
			</table>
		</div>
	{:else if !loading}
		<p class="text-xs text-gray-500 dark:text-gray-400">No databases are configured yet.</p>
	{/if}
</div>

{#if detailTarget}
	<Modal bind:show={detailsShow} size="xl" containerClassName="p-4" className="overflow-hidden rounded-2xl bg-white dark:bg-gray-900">
		<div class="mx-auto flex max-h-[80vh] min-h-[24rem] w-full max-w-[70rem] flex-col p-5">
			<div class="mb-4 flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 pb-3 dark:border-slate-700">
				<div class="min-w-0"><div class="font-semibold">{detailType === 'database' ? 'Database details' : detailType === 'instance' ? 'Instance details' : 'Database parameters'} — {detailTarget.name}</div><div class="text-xs text-slate-500">{detailTarget.service_name}</div></div>
				<button class="shrink-0 rounded-lg px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={() => (detailsShow = false)}>Close</button>
			</div>
			{#if detailType === 'parameters'}
				<div class="mb-3 flex shrink-0 items-end gap-2"><label class="block min-w-0 flex-1 text-xs font-medium text-slate-700 dark:text-slate-200">Filter parameter name<input class="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal dark:border-slate-600 dark:bg-slate-950" placeholder="Filter displayed parameters" value={parameterNameFilter} on:input={(event) => (parameterNameFilter = event.currentTarget.value)} /></label><button class="rounded-lg border border-cyan-700 px-3 py-2 text-xs text-cyan-800 hover:bg-cyan-50 disabled:opacity-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={detailsLoading} on:click={refreshParameters}>{detailsLoading ? 'Refreshing…' : 'Refresh data'}</button></div>
			{/if}
			<div class="min-h-0 min-w-0 flex-1 overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
				{#if detailsLoading}<div class="p-4 text-sm text-slate-500">Running Oracle query…</div>
				{:else if detailType === 'parameters'}
					<table class="min-w-max text-left text-xs"><thead class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400"><tr>{#each detailColumns as column}<th class="whitespace-nowrap px-3 py-2">{column}</th>{/each}</tr></thead><tbody>{#each filteredParameterRows as row, index (index)}<tr class="border-t border-gray-100 dark:border-gray-800">{#each detailColumns as column}<td class="max-w-96 px-3 py-2 align-top break-all">{row[column] ?? '-'}</td>{/each}</tr>{/each}{#if filteredParameterRows.length === 0}<tr><td class="px-3 py-4 text-slate-500" colspan={Math.max(detailColumns.length, 1)}>No parameters match this name filter.</td></tr>{/if}</tbody></table>
				{:else if detailRows[0]}
					<div class="divide-y divide-slate-100 dark:divide-slate-800">{#each detailColumns as column}<div class="grid grid-cols-[minmax(10rem,16rem)_minmax(0,1fr)] gap-4 px-4 py-2 text-xs"><div class="font-medium text-slate-500">{column}</div><div class="min-w-0 break-all [overflow-wrap:anywhere]">{detailRows[0][column] ?? '-'}</div></div>{/each}</div>
				{:else}<div class="p-4 text-sm text-slate-500">No data was returned.</div>{/if}
			</div>
		</div>
	</Modal>
{/if}

<Modal bind:show={logShow} size={logFullscreen ? 'full' : 'xl'} containerClassName={logFullscreen ? 'p-2' : 'p-4'} className="overflow-hidden rounded-2xl bg-white dark:bg-gray-900">
	<div class={`mx-auto flex min-h-[24rem] w-full flex-col p-5 ${logFullscreen ? 'h-[calc(100dvh-1rem)] max-w-none' : 'max-h-[80vh] max-w-[75rem]'}`}>
		<div class="mb-4 flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 pb-3 dark:border-slate-700"><div class="min-w-0"><div class="font-semibold">{logTitle}</div>{#if logPath}<div class="truncate text-xs text-slate-500">{logPath}</div>{/if}</div><div class="flex shrink-0 gap-2"><button class="rounded-lg border border-cyan-700 px-2 py-1 text-sm text-cyan-800 hover:bg-cyan-50 disabled:opacity-50 dark:text-cyan-200 dark:hover:bg-cyan-950/30" type="button" disabled={!logId} on:click={downloadLog}>Download</button><button class="rounded-lg px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={() => (logFullscreen = !logFullscreen)}>{logFullscreen ? 'Restore' : 'Full screen'}</button><button class="rounded-lg px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" type="button" on:click={() => (logShow = false)}>Close</button></div></div>
		<div class="mb-3 flex shrink-0 flex-wrap items-center gap-2 text-xs"><label class="flex min-w-56 flex-1 items-center gap-2 rounded-lg border border-slate-300 px-2 py-1.5 dark:border-slate-600">Search<input class="min-w-0 flex-1 bg-transparent outline-none" placeholder="Text in log" value={logSearch} on:input={(event) => { logSearch = event.currentTarget.value; activeSearchMatch = 0; }} /></label><span class="text-slate-500">{matchingLogLineIndexes.length} match{matchingLogLineIndexes.length === 1 ? '' : 'es'}</span><button class="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-800" type="button" disabled={matchingLogLineIndexes.length === 0} on:click={() => goToSearchMatch(-1)}>Previous</button><button class="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-800" type="button" disabled={matchingLogLineIndexes.length === 0} on:click={() => goToSearchMatch(1)}>Next</button><label class="flex items-center gap-2">Go to line<input class="w-20 rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-600" type="number" min="1" max={logLines.length || undefined} bind:value={goToLineNumber} on:keydown={(event) => event.key === 'Enter' && goToRequestedLine()} /></label><button class="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800" type="button" on:click={goToRequestedLine}>Go</button></div>
		<div class="min-h-0 flex-1 overflow-auto rounded-lg bg-slate-950 p-4 text-left font-mono text-xs text-slate-100">{#if logLoading}Retrieving log over SFTP…{:else if logContent}<div class="min-w-max whitespace-pre">{#each logLines as line, lineIndex}<div id={`oracle-log-line-${lineIndex}`} class={`flex ${logSearch.trim() && line.toLowerCase().includes(logSearch.trim().toLowerCase()) ? 'bg-amber-400/25' : ''}`}><span class="w-14 shrink-0 select-none pr-3 text-right text-slate-500">{lineIndex + 1}</span><span>{#each line.split(/(\/[\w./-]+\.(?:trc|trm)\b)/gi) as segment}{#if isTraceFilePath(segment)}<button class="font-mono text-cyan-300 underline decoration-cyan-300 underline-offset-2 hover:text-cyan-100" type="button" on:click={() => openTraceFile(segment)}>{segment}</button>{:else}{segment}{/if}{/each}</span></div>{/each}</div>{:else}No log content was returned.{/if}</div>
	</div>
</Modal>
