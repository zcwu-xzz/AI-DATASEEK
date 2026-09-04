<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white text-slate-700">
    <div class="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 text-xs">
      <span class="font-semibold text-slate-800">BLAST 比对结果</span><span>命中 {{ filtered.length }} 条</span>
      <select v-model="query" class="h-7 max-w-[180px] rounded border border-slate-300 bg-white px-2"><option value="">全部 Query</option><option v-for="name in queries" :key="name" :value="name">{{ name }}</option></select>
      <label class="flex items-center gap-1">最低一致性 <input v-model.number="minIdentity" type="number" min="0" max="100" class="h-7 w-16 rounded border border-slate-300 px-1" />%</label>
      <label class="flex items-center gap-1">最低覆盖度 <input v-model.number="minCoverage" type="number" min="0" max="100" class="h-7 w-16 rounded border border-slate-300 px-1" />%</label>
      <button type="button" class="ml-auto rounded border border-slate-300 px-2.5 py-1.5" @click="copyTable">复制结果</button>
    </div>
    <div v-if="status" class="flex flex-1 items-center justify-center text-sm text-slate-500">{{ status }}</div>
    <div v-else class="min-h-0 flex-1 overflow-auto p-4">
      <div v-if="selected" class="mb-3 rounded border border-blue-200 bg-blue-50 p-3 text-xs"><div class="flex justify-between font-medium"><span>{{ selected.query }} → {{ selected.subject }}</span><button @click="selected = null">×</button></div><div class="mt-1 grid grid-cols-2 gap-1 md:grid-cols-4"><span>一致性 {{ selected.identity.toFixed(2) }}%</span><span>覆盖度 {{ coverage(selected).toFixed(2) }}%</span><span>E-value {{ selected.evalue }}</span><span>比对长度 {{ selected.alignmentLength }}</span></div><div class="mt-1 break-all text-slate-500">Query {{ selected.qstart }}–{{ selected.qend }}；Subject {{ selected.sstart }}–{{ selected.send }}</div></div>
      <svg :viewBox="`0 0 ${width} ${Math.max(100, filtered.length * 44 + 36)}`" class="h-auto min-w-[760px] w-full rounded border border-slate-200 bg-slate-50">
        <text x="8" y="18" font-size="11" fill="#475569">Query / Subject</text><text :x="labelWidth" y="18" font-size="11" fill="#475569">命中区间（按 Query 坐标）</text>
        <g v-for="(hit, index) in filtered" :key="hit.key" class="cursor-pointer" @click="selected = hit"><text x="8" :y="46 + index * 44" font-size="11" fill="#334155">{{ truncate(hit.query, 16) }}</text><text x="8" :y="61 + index * 44" font-size="10" fill="#64748b">{{ truncate(hit.subject, 18) }}</text><line :x1="labelWidth" :x2="width - 16" :y1="46 + index * 44" :y2="46 + index * 44" stroke="#cbd5e1"/><rect :x="labelWidth + rangeX(hit.qstart, hit.queryLength)" :y="35 + index * 44" :width="Math.max(3, rangeWidth(hit.qstart, hit.qend, hit.queryLength))" height="20" rx="3" :fill="identityColor(hit.identity)"/><text :x="labelWidth + rangeX(hit.qstart, hit.queryLength) + 4" :y="49 + index * 44" font-size="10" fill="white">{{ hit.identity.toFixed(1) }}%</text></g>
      </svg>
      <table class="mt-3 w-full min-w-[760px] border-collapse text-left text-xs"><thead><tr class="border-b bg-slate-50"><th v-for="column in ['Query','Subject','一致性','覆盖度','E-value','比对长度']" :key="column" class="px-2 py-2">{{ column }}</th></tr></thead><tbody><tr v-for="hit in filtered" :key="`${hit.key}-row`" class="cursor-pointer border-b border-slate-100 hover:bg-blue-50" @click="selected = hit"><td class="max-w-[200px] truncate px-2 py-1.5">{{ hit.query }}</td><td class="max-w-[220px] truncate px-2 py-1.5">{{ hit.subject }}</td><td class="px-2 py-1.5">{{ hit.identity.toFixed(2) }}%</td><td class="px-2 py-1.5">{{ coverage(hit).toFixed(2) }}%</td><td class="px-2 py-1.5">{{ hit.evalue }}</td><td class="px-2 py-1.5">{{ hit.alignmentLength }}</td></tr></tbody></table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';
import { copyToClipboard } from '../../utils/dom';

type Hit = { key: string; query: string; subject: string; identity: number; alignmentLength: number; qstart: number; qend: number; sstart: number; send: number; evalue: string; bitscore: number; queryLength: number };
const props = defineProps<{ file: FileInfo }>(); const source = ref(''); const status = ref('正在加载比对结果...'); const hits = ref<Hit[]>([]); const query = ref(''); const minIdentity = ref(0); const minCoverage = ref(0); const selected = ref<Hit | null>(null); const width = 1100; const labelWidth = 260;
const queries = computed(() => [...new Set(hits.value.map(item => item.query))]);
const filtered = computed(() => hits.value.filter(item => (!query.value || item.query === query.value) && item.identity >= minIdentity.value && coverage(item) >= minCoverage.value).slice(0, 500));
const coverage = (hit: Hit) => hit.queryLength > 0 ? Math.min(100, Math.abs(hit.qend - hit.qstart) + 1) / hit.queryLength * 100 : 0;
const rangeX = (value: number, length: number) => (Math.max(0, value - 1) / Math.max(1, length)) * (width - labelWidth - 16);
const rangeWidth = (start: number, end: number, length: number) => Math.abs(end - start) / Math.max(1, length) * (width - labelWidth - 16);
const identityColor = (value: number) => value >= 95 ? '#15803d' : value >= 80 ? '#2563eb' : value >= 60 ? '#d97706' : '#dc2626';
const truncate = (value: string, max: number) => value.length > max ? `${value.slice(0, max - 1)}…` : value;
const copyTable = async () => { await copyToClipboard(filtered.value.map(item => `${item.query}\t${item.subject}\t${item.identity.toFixed(2)}\t${coverage(item).toFixed(2)}\t${item.evalue}\t${item.alignmentLength}`).join('\n')); };
const parse = (text: string): Hit[] => { const out: Hit[] = []; let index = 0; for (const line of text.split(/\r?\n/)) { if (!line.trim() || line.startsWith('#')) continue; const v = line.split(/\t|\s+/); if (v.length < 12) continue; const nums = v.slice(2, 12).map(Number); if (nums.some(value => !Number.isFinite(value))) continue; const [identity, aln, _mismatch, _gap, qstart, qend, sstart, send, _evalue, bitscore] = nums; const queryLength = Math.max(Math.abs(qend - qstart) + 1, Number((props.file.metadata as any)?.query_lengths?.[v[0]]) || Math.abs(qend - qstart) + 1); out.push({ key: `${v[0]}-${v[1]}-${index++}`, query: v[0], subject: v[1], identity, alignmentLength: aln, qstart, qend, sstart, send, evalue: v[10], bitscore, queryLength }); if (out.length >= 2000) break; } return out; };
const load = async () => { status.value = '正在加载比对结果...'; try { const response = await fetch(await getFileDownloadUrl(props.file)); if (!response.ok) throw new Error(String(response.status)); source.value = await response.text(); hits.value = parse(source.value); status.value = hits.value.length ? '' : '未找到可解析的 BLAST tabular 记录'; } catch (error) { console.error(error); status.value = '比对结果加载失败'; } };
watch(() => props.file, load, { immediate: true });
</script>
