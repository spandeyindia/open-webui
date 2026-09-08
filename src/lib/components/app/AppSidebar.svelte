<script lang="ts">
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';

	let selected = '';
</script>

<nav
	aria-label="App navigation"
	class="min-w-[4.5rem] bg-gray-50 dark:bg-gray-950 flex gap-2.5 flex-col pt-8"
>
	<div class="flex justify-center relative">
		{#if selected === 'home'}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}

		<Tooltip content="Home" placement="right">
			<button
				aria-label="Home"
				class=" cursor-pointer {selected === 'home' ? 'rounded-2xl' : 'rounded-full'}"
				on:click={() => {
					selected = 'home';

					if (window.electronAPI) {
						window.electronAPI.load('home');
					}
				}}
			>
				<span class="flex size-11 items-center justify-center rounded-md bg-[#c74634]" aria-label="Oracle">
					<svg viewBox="0 0 64 32" class="h-6 w-9" aria-hidden="true">
						<rect x="7" y="7" width="50" height="18" rx="9" fill="none" stroke="white" stroke-width="5" />
					</svg>
				</span>
			</button>
		</Tooltip>
	</div>

	<div class=" -mt-1 border-[1.5px] border-gray-100 dark:border-gray-900 mx-4"></div>

	<div class="flex justify-center relative group">
		{#if selected === ''}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}
		<button
			aria-label="Chat"
			class=" cursor-pointer bg-transparent"
			on:click={() => {
				selected = '';
			}}
		>
			<span class="flex size-10 items-center justify-center rounded-md bg-[#c74634]" aria-label="Oracle">
				<svg viewBox="0 0 64 32" class="h-5 w-8" aria-hidden="true">
					<rect x="7" y="7" width="50" height="18" rx="9" fill="none" stroke="white" stroke-width="5" />
				</svg>
			</span>
		</button>
	</div>

	<!-- <div class="flex justify-center relative group text-gray-400">
		<button class=" cursor-pointer p-2" on:click={() => {}}>
			<Plus className="size-4" strokeWidth="2" />
		</button>
	</div> -->
</nav>
