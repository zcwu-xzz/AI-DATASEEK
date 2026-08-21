<template>
  <div class="flex min-h-0 flex-1 flex-col bg-[var(--background-gray-main)]">
    <div class="flex shrink-0 items-center justify-between border-b border-[var(--border-main)] px-4 py-2 text-xs text-[var(--text-tertiary)]">
      <span class="font-medium text-[var(--text-secondary)]">CSV 在线预览</span>
      <span v-if="rows.length">前 {{ rows.length }} 行{{ truncated ? '（已截断）' : '' }}</span>
    </div>
    <div v-if="status" class="flex min-h-0 flex-1 items-center justify-center p-4">
      <div class="rounded-xl border border-[var(--border-main)] bg-[var(--background-menu-white)] px-4 py-3 text-sm text-[var(--text-secondary)]">{{ status }}</div>
    </div>
    <div v-else class="min-h-0 flex-1 overflow-auto bg-[var(--background-menu-white)] p-3">
      <table class="min-w-full border-collapse text-left text-xs">
        <thead><tr><th v-for="(header, index) in headers" :key="index" class="sticky top-0 whitespace-nowrap border border-[var(--border-main)] bg-[var(--background-gray-main)] px-2 py-1.5 font-medium">{{ header || `列 ${index + 1}` }}</th></tr></thead>
        <tbody><tr v-for="(row, rowIndex) in rows" :key="rowIndex" class="hover:bg-[var(--background-gray-main)]"><td v-for="(value, columnIndex) in row" :key="columnIndex" class="max-w-[280px] whitespace-nowrap border border-[var(--border-main)] px-2 py-1.5" :title="value">{{ value }}</td></tr></tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';

const props = defineProps<{ file: FileInfo }>();
const headers = ref<string[]>([]);
const rows = ref<string[][]>([]);
const status = ref('');
const truncated = ref(false);
let loadVersion = 0;

function parseCsv(text: string, delimiter: string): string[][] {
  const result: string[][] = []; let row: string[] = []; let cell = ''; let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"') { if (quoted && text[i + 1] === '"') { cell += '"'; i += 1; } else quoted = !quoted; }
    else if (char === delimiter && !quoted) { row.push(cell); cell = ''; }
    else if ((char === '\n' || char === '\r') && !quoted) { if (char === '\r' && text[i + 1] === '\n') i += 1; row.push(cell); if (row.some(value => value.length)) result.push(row); row = []; cell = ''; }
    else cell += char;
  }
  if (cell || row.length) { row.push(cell); result.push(row); }
  return result;
}

async function load() {
  const version = ++loadVersion; status.value = '正在加载 CSV...'; headers.value = []; rows.value = []; truncated.value = false;
  try {
    const response = await fetch(await getFileDownloadUrl(props.file));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    if (version !== loadVersion) return;
    const sample = text.slice(0, 10000); const commas = (sample.match(/,/g) || []).length; const tabs = (sample.match(/\t/g) || []).length;
    const parsed = parseCsv(text, tabs > commas ? '\t' : ',');
    headers.value = (parsed.shift() || []).slice(0, 50);
    rows.value = parsed.slice(0, 100).map(row => row.slice(0, 50).concat(Array(Math.max(0, headers.value.length - row.length)).fill('')));
    truncated.value = parsed.length > 100;
    status.value = '';
  } catch (error) { if (version === loadVersion) status.value = `CSV 加载失败：${error instanceof Error ? error.message : '未知错误'}`; }
}

watch(() => props.file, load, { immediate: true, deep: false });
</script>
