<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import OracleMonitor from '$lib/components/admin/Settings/OracleMonitor.svelte';
	import OracleCurrentUsage from '$lib/components/admin/Settings/OracleCurrentUsage.svelte';
	import OraclePatchLevel from '$lib/components/admin/Settings/OraclePatchLevel.svelte';
	import OracleBackupStatus from '$lib/components/admin/Settings/OracleBackupStatus.svelte';
	import DbHealthTable from './DbHealthTable.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	type Card = {
		id: string;
		title: string;
		url: string;
		targetType: 'api' | 'dashboard' | 'oracle_monitor' | 'oracle_connectivity' | 'oracle_current_usage' | 'oracle_patch_level' | 'oracle_backup_status' | 'space_analysis' | 'knowledge_resources';
		description?: string;
		hasStatus: boolean;
		refreshSeconds: number;
	};
	type Status = { status?: string; detail?: string; updatedAt?: string; error?: boolean };

	let cards: Card[] = [];
	let statuses: Record<string, Status> = {};
	let activeCard: Card | null = null;
	let intervals: ReturnType<typeof setInterval>[] = [];

	const statusClass = (status?: string) => {
		const value = (status ?? '').toLowerCase();
		if (value.includes('warn') || value.includes('degrad')) return 'bg-amber-500/15 text-amber-700 dark:text-amber-300';
		if (value.includes('error') || value.includes('down') || value.includes('fail')) return 'bg-red-500/15 text-red-700 dark:text-red-300';
		return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300';
	};

	const loadStatus = async (card: Card) => {
		if (!card.hasStatus) return;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/jdeops/cards/${encodeURIComponent(card.id)}/status`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) throw new Error('Status endpoint unavailable');
			statuses = { ...statuses, [card.id]: await response.json() };
		} catch {
			statuses = { ...statuses, [card.id]: { status: 'Unavailable', error: true } };
		}
	};

	const openCard = (card: Card) => {
		if (card.targetType === 'api' && card.url) window.open(card.url, '_blank', 'noopener,noreferrer');
		else activeCard = card;
	};

	onMount(async () => {
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/jdeops/cards`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) return;
			cards = await response.json();
			cards.filter((card) => card.hasStatus).forEach((card) => {
				loadStatus(card);
				intervals.push(setInterval(() => loadStatus(card), card.refreshSeconds * 1000));
			});
		} catch {
			// A missing optional JDE Ops configuration must not affect chat.
		}
	});

	onDestroy(() => intervals.forEach(clearInterval));
</script>

<section class="flex h-full min-h-0 w-full max-w-none flex-col gap-3 text-left">
	<div class="overflow-hidden border-y border-cyan-500/30 bg-linear-to-r from-slate-950 via-slate-900 to-cyan-950 px-5 py-4 text-white shadow-lg">
		<div class="text-xl font-semibold tracking-tight">dbaOps</div>
		<div class="mt-0.5 text-sm text-cyan-100/80">Live health, readiness, and operational actions</div>
	</div>
	{#if cards.length > 0}
		<div class="shrink-0 pb-2">
			<div class="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-6">
				{#each cards as card (card.id)}
					<button class="min-h-28 rounded-xl border border-slate-200 border-t-2 border-t-cyan-500 bg-white p-2.5 text-left shadow-[0_5px_0_#0891b2] transition duration-150 hover:-translate-y-1 hover:border-cyan-400 hover:shadow-[0_7px_0_#0e7490] active:translate-y-[3px] active:scale-[0.985] active:shadow-[0_2px_0_#0891b2] dark:border-slate-700 dark:border-t-cyan-400 dark:bg-slate-900" on:click={() => openCard(card)}>
						<div class="flex size-7 rotate-[-6deg] items-center justify-center rounded-lg bg-cyan-500/15 text-sm text-cyan-700 shadow-sm dark:text-cyan-300">◈</div>
						<div class="mt-2 line-clamp-2 text-sm font-semibold text-slate-800 dark:text-slate-100">{card.title}</div>
						{#if card.description}<div class="mt-0.5 line-clamp-1 text-[0.6875rem] text-slate-500 dark:text-slate-400">{card.description}</div>{/if}
						{#if card.hasStatus}
							<div class={`mt-2 inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[0.6875rem] font-medium ${statusClass(statuses[card.id]?.status)}`}>
								<span class="size-1.5 shrink-0 rounded-full bg-current"></span>{statuses[card.id]?.status ?? 'Checking'}
							</div>
							{#if statuses[card.id]?.detail}<div class="mt-2 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">{statuses[card.id]?.detail}</div>{/if}
						{/if}
					</button>
				{/each}
			</div>
		</div>
	{/if}
	{#if activeCard}
		<section class="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
			{#if !['oracle_current_usage', 'oracle_patch_level', 'oracle_backup_status'].includes(activeCard.targetType)}
				<div class="mb-4 flex shrink-0 items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-700"><div class="font-semibold">{activeCard.title}</div><button class="rounded-lg px-3 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800" on:click={() => (activeCard = null)}>Back to health</button></div>
			{/if}
			{#if activeCard.targetType === 'oracle_monitor' || activeCard.targetType === 'oracle_connectivity'}
				<OracleMonitor mode="dashboard" showInstanceTiming={activeCard.targetType === 'oracle_monitor'} />
			{:else if activeCard.targetType === 'oracle_current_usage'}
				<OracleCurrentUsage onBack={() => (activeCard = null)} />
			{:else if activeCard.targetType === 'oracle_patch_level'}
				<OraclePatchLevel onBack={() => (activeCard = null)} />
			{:else if activeCard.targetType === 'oracle_backup_status'}
				<OracleBackupStatus onBack={() => (activeCard = null)} />
			{:else if activeCard.targetType === 'space_analysis'}
				<div class="flex flex-1 items-center justify-center p-6 text-center">
					<div class="max-w-xl">
						<div class="text-lg font-semibold">Space Analysis</div>
						<p class="mt-2 text-sm text-slate-600 dark:text-slate-300">The card is ready for the Oracle storage analysis report. Provide the SQL or the required measures, and the report will be added for each configured database.</p>
					</div>
				</div>
			{:else if activeCard.targetType === 'knowledge_resources'}
				<div class="flex flex-1 items-center justify-center p-6 text-center">
					<div class="max-w-xl">
						<div class="text-lg font-semibold">Knowledge Resources</div>
						<p class="mt-2 text-sm text-slate-600 dark:text-slate-300">The card is ready for DBA runbooks and reference material. Provide the folder, URL, or document source that should be made available here.</p>
					</div>
				</div>
			{:else if activeCard.targetType === 'dashboard'}
				<iframe class="min-h-0 flex-1 bg-white" src={activeCard.url} title={activeCard.title}></iframe>
			{/if}
		</section>
	{:else}
		<DbHealthTable />
	{/if}
</section>
