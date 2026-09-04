<template>
  <div class="flex min-h-0 flex-1 flex-col bg-slate-50 text-slate-700">
    <header class="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="rounded bg-emerald-50 px-2 py-1 font-semibold text-emerald-700">{{ formatLabel }} 序列浏览器</span>
        <span>共 {{ records.length.toLocaleString() }} 条</span>
        <span v-if="current">当前长度 {{ current.sequence.length.toLocaleString() }} bp</span>
        <select v-if="records.length > 1" v-model="selectedIndex" class="h-8 min-w-[180px] max-w-[320px] rounded border border-slate-300 bg-white px-2">
          <option v-for="(record, index) in records" :key="`${record.id}-${index}`" :value="index">{{ record.id }}</option>
        </select>
        <div class="ml-auto flex flex-wrap items-center gap-2">
          <button type="button" class="tool-button" :disabled="visibleStart <= 1" title="上一窗口" @click="moveWindow(-1)">←</button>
          <label class="flex items-center gap-1">位置
            <input v-model.number="startInput" type="number" min="1" :max="Math.max(1, current?.sequence.length || 1)" class="h-8 w-24 rounded border border-slate-300 px-2" @keyup.enter="jump" />
          </label>
          <button type="button" class="primary-button" @click="jump">跳转</button>
          <button type="button" class="tool-button" :disabled="visibleEnd >= (current?.sequence.length || 0)" title="下一窗口" @click="moveWindow(1)">→</button>
          <label class="flex items-center gap-1">窗口
            <select v-model.number="windowSize" class="h-8 rounded border border-slate-300 bg-white px-2">
              <option v-for="size in windowSizes" :key="size" :value="size">{{ size }} bp</option>
            </select>
          </label>
          <button type="button" class="tool-button px-3" @click="copyWindow">复制窗口</button>
        </div>
      </div>
    </header>

    <div v-if="status" class="flex flex-1 items-center justify-center p-6 text-sm text-slate-500">{{ status }}</div>
    <main v-else-if="current" class="min-h-0 flex-1 overflow-auto p-4">
      <div class="mx-auto max-w-[1400px] space-y-4">
        <section class="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
          <div class="stat-card"><span>序列标识</span><strong class="truncate" :title="current.id">{{ current.id }}</strong></div>
          <div class="stat-card"><span>长度</span><strong>{{ current.sequence.length.toLocaleString() }} bp</strong></div>
          <div class="stat-card"><span>GC 含量</span><strong>{{ gcPercent.toFixed(2) }}%</strong></div>
          <div class="stat-card"><span>N/未知碱基</span><strong>{{ nPercent.toFixed(2) }}%</strong></div>
          <div v-if="current.qualities" class="stat-card"><span>平均质量</span><strong>Q{{ meanQuality.toFixed(1) }}</strong></div>
          <div v-if="current.qualities" class="stat-card"><span>低质量碱基</span><strong>{{ lowQualityCount.toLocaleString() }}</strong></div>
        </section>

        <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span class="font-semibold text-slate-700">全序列概览</span>
            <span class="text-slate-400">点击轨道可快速定位；颜色越深表示窗口 GC 含量越高</span>
            <span class="ml-auto text-slate-500">{{ visibleStart.toLocaleString() }}–{{ visibleEnd.toLocaleString() }}</span>
          </div>
          <div ref="overviewElement" class="overview-track" title="点击跳转" @click="jumpFromOverview">
            <span v-for="(bin, index) in overviewBins" :key="index" class="overview-bin" :style="{ backgroundColor: gcColor(bin) }"></span>
            <span class="overview-window" :style="overviewWindowStyle"></span>
          </div>
          <div class="mt-1 flex justify-between font-mono text-[10px] text-slate-400"><span>1</span><span>{{ current.sequence.length.toLocaleString() }} bp</span></div>
        </section>

        <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <span class="font-semibold text-slate-700">序列内搜索</span>
            <input v-model.trim="motif" class="h-8 w-48 rounded border border-slate-300 px-2 font-mono uppercase" placeholder="输入碱基或模体，如 ATG" @keyup.enter="goToHit(0)" />
            <span v-if="motif" class="text-slate-500">找到 {{ motifHits.length.toLocaleString() }} 处</span>
            <button type="button" class="tool-button" :disabled="!motifHits.length" @click="moveHit(-1)">上一个</button>
            <button type="button" class="tool-button" :disabled="!motifHits.length" @click="moveHit(1)">下一个</button>
            <span v-if="activeHitIndex >= 0" class="font-mono text-emerald-700">{{ activeHitIndex + 1 }}/{{ motifHits.length }} · 位点 {{ motifHits[activeHitIndex]?.toLocaleString() }}</span>
          </div>
        </section>

        <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex flex-wrap items-center gap-3 text-xs">
            <span class="font-semibold text-slate-700">碱基与坐标</span>
            <span class="legend"><i class="bg-blue-500"></i>A</span><span class="legend"><i class="bg-emerald-500"></i>C</span>
            <span class="legend"><i class="bg-amber-500"></i>G</span><span class="legend"><i class="bg-red-500"></i>T/U</span>
            <span class="legend"><i class="bg-violet-500"></i>N</span><span v-if="current.qualities" class="ml-auto text-slate-400">质量轨道：红色 &lt; Q20，橙色 Q20–29，绿色 ≥ Q30</span>
          </div>
          <div class="space-y-5 overflow-x-auto pb-2 font-mono">
            <div v-for="(row, rowIndex) in sequenceRows" :key="rowIndex" class="sequence-row">
              <div class="coordinate-label">{{ row.start.toLocaleString() }}</div>
              <div class="flex gap-2">
                <div v-for="(group, groupIndex) in row.groups" :key="groupIndex" class="base-group">
                  <div class="mb-1 text-[9px] text-slate-400">{{ group[0]?.position.toLocaleString() }}</div>
                  <div class="flex">
                    <span v-for="base in group" :key="base.position" class="base-cell" :class="[baseClass(base.base, base.quality), { 'motif-hit': base.hit }]" :title="baseTitle(base)">{{ base.base }}</span>
                  </div>
                  <div v-if="current.qualities" class="mt-1 flex h-7 items-end" aria-label="碱基质量轨道">
                    <span v-for="base in group" :key="`q-${base.position}`" class="quality-bar" :class="qualityClass(base.quality)" :style="{ height: `${qualityHeight(base.quality)}%` }" :title="`位置 ${base.position}，质量 Q${base.quality ?? '-'}`"></span>
                  </div>
                </div>
              </div>
              <div class="coordinate-label text-left">{{ row.end.toLocaleString() }}</div>
            </div>
          </div>
          <div class="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
            当前显示 {{ visibleStart.toLocaleString() }}–{{ visibleEnd.toLocaleString() }}，共 {{ visibleSequence.length.toLocaleString() }} bp。
          </div>
        </section>
      </div>
    </main>
    <div v-else class="flex flex-1 items-center justify-center text-sm text-slate-500">未找到可解析的 FASTA/FASTQ 序列</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';
import { copyToClipboard } from '../../utils/dom';

interface SequenceRecord { id: string; sequence: string; qualities?: number[] }
interface DisplayBase { base: string; position: number; quality?: number; hit: boolean }

const props = defineProps<{ file: FileInfo }>();
const records = ref<SequenceRecord[]>([]);
const selectedIndex = ref(0);
const startInput = ref(1);
const status = ref('');
const windowSize = ref(250);
const windowSizes = [50, 100, 250, 500, 1000];
const motif = ref('');
const activeHitIndex = ref(-1);
const overviewElement = ref<HTMLElement | null>(null);
const formatLabel = ref('FASTA');

const current = computed(() => records.value[selectedIndex.value]);
const visibleStart = computed(() => Math.min(Math.max(1, Number(startInput.value) || 1), current.value?.sequence.length || 1));
const visibleEnd = computed(() => Math.min(visibleStart.value + windowSize.value - 1, current.value?.sequence.length || 0));
const visibleSequence = computed(() => current.value?.sequence.slice(visibleStart.value - 1, visibleEnd.value) || '');
const gcPercent = computed(() => percentage(current.value?.sequence || '', /[GC]/gi));
const nPercent = computed(() => percentage(current.value?.sequence || '', /N/gi));
const meanQuality = computed(() => average(current.value?.qualities || []));
const lowQualityCount = computed(() => (current.value?.qualities || []).filter(value => value < 20).length);
const motifHits = computed(() => {
  const query = motif.value.replace(/\s+/g, '').toUpperCase();
  const sequence = current.value?.sequence || '';
  if (!query || !/^[A-Z*.-]+$/.test(query)) return [];
  const hits: number[] = [];
  let from = 0;
  while (from <= sequence.length - query.length && hits.length < 10000) {
    const index = sequence.indexOf(query, from);
    if (index < 0) break;
    hits.push(index + 1);
    from = index + 1;
  }
  return hits;
});
const hitPositions = computed(() => {
  const positions = new Set<number>();
  const length = motif.value.replace(/\s+/g, '').length;
  for (const start of motifHits.value) for (let offset = 0; offset < length; offset += 1) positions.add(start + offset);
  return positions;
});
const sequenceRows = computed(() => {
  const bases: DisplayBase[] = [...visibleSequence.value].map((base, index) => {
    const position = visibleStart.value + index;
    return { base, position, quality: current.value?.qualities?.[position - 1], hit: hitPositions.value.has(position) };
  });
  const rows: { start: number; end: number; groups: DisplayBase[][] }[] = [];
  for (let offset = 0; offset < bases.length; offset += 60) {
    const row = bases.slice(offset, offset + 60);
    const groups: DisplayBase[][] = [];
    for (let groupOffset = 0; groupOffset < row.length; groupOffset += 10) groups.push(row.slice(groupOffset, groupOffset + 10));
    rows.push({ start: row[0]?.position || 0, end: row[row.length - 1]?.position || 0, groups });
  }
  return rows;
});
const overviewBins = computed(() => {
  const sequence = current.value?.sequence || '';
  if (!sequence) return [];
  const binCount = Math.min(120, sequence.length);
  return Array.from({ length: binCount }, (_, index) => {
    const start = Math.floor(index * sequence.length / binCount);
    const end = Math.max(start + 1, Math.floor((index + 1) * sequence.length / binCount));
    return percentage(sequence.slice(start, end), /[GC]/gi);
  });
});
const overviewWindowStyle = computed(() => {
  const length = Math.max(1, current.value?.sequence.length || 1);
  return { left: `${((visibleStart.value - 1) / length) * 100}%`, width: `${Math.max(0.8, (visibleSequence.value.length / length) * 100)}%` };
});

function percentage(sequence: string, pattern: RegExp) { return sequence ? ((sequence.match(pattern) || []).length / sequence.length) * 100 : 0; }
function average(values: number[]) { return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0; }
function gcColor(value: number) { const opacity = 0.18 + (Math.max(0, Math.min(100, value)) / 100) * 0.72; return `rgba(16, 185, 129, ${opacity})`; }
function baseClass(base: string, quality?: number) {
  if (typeof quality === 'number' && quality < 20) return 'base-low-quality';
  return ({ A: 'base-a', C: 'base-c', G: 'base-g', T: 'base-t', U: 'base-t', N: 'base-n' }[base.toUpperCase()] || 'base-other');
}
function qualityClass(quality?: number) { return typeof quality !== 'number' || quality < 20 ? 'quality-low' : quality < 30 ? 'quality-medium' : 'quality-high'; }
function qualityHeight(quality?: number) { return typeof quality === 'number' ? Math.max(8, Math.min(100, quality / 42 * 100)) : 8; }
function baseTitle(base: DisplayBase) { return `位置 ${base.position}，碱基 ${base.base}${typeof base.quality === 'number' ? `，质量 Q${base.quality}` : ''}`; }
function jump() { startInput.value = visibleStart.value; }
function moveWindow(direction: number) { startInput.value = Math.max(1, Math.min(current.value?.sequence.length || 1, visibleStart.value + direction * windowSize.value)); }
function jumpFromOverview(event: MouseEvent) {
  if (!overviewElement.value || !current.value) return;
  const bounds = overviewElement.value.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
  startInput.value = Math.max(1, Math.round(ratio * current.value.sequence.length - windowSize.value / 2));
  jump();
}
function goToHit(index: number) {
  if (!motifHits.value.length) return;
  activeHitIndex.value = Math.max(0, Math.min(index, motifHits.value.length - 1));
  startInput.value = motifHits.value[activeHitIndex.value];
  jump();
}
function moveHit(direction: number) {
  if (!motifHits.value.length) return;
  const next = activeHitIndex.value < 0 ? 0 : (activeHitIndex.value + direction + motifHits.value.length) % motifHits.value.length;
  goToHit(next);
}
async function copyWindow() { if (visibleSequence.value) await copyToClipboard(visibleSequence.value); }
function parse(text: string) {
  const result: SequenceRecord[] = [];
  let item: SequenceRecord | null = null;
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  const firstContent = lines.find(line => line.trim());
  if (firstContent?.startsWith('@')) {
    formatLabel.value = 'FASTQ';
    for (let i = 0; i + 3 < lines.length; i += 4) {
      if (!lines[i].startsWith('@') || !lines[i + 2].startsWith('+')) continue;
      const sequence = lines[i + 1].replace(/\s+/g, '').toUpperCase();
      if (sequence) result.push({ id: lines[i].slice(1).trim() || `序列 ${result.length + 1}`, sequence, qualities: [...lines[i + 3]].map(character => character.charCodeAt(0) - 33).slice(0, sequence.length) });
    }
    return result;
  }
  formatLabel.value = 'FASTA';
  for (const line of lines) {
    if (line.startsWith('>')) { if (item) result.push(item); item = { id: line.slice(1).trim() || `序列 ${result.length + 1}`, sequence: '' }; }
    else if (item) item.sequence += line.replace(/\s+/g, '').toUpperCase();
  }
  if (item) result.push(item);
  return result.filter(record => record.sequence);
}
async function load() {
  records.value = []; selectedIndex.value = 0; startInput.value = 1; motif.value = ''; status.value = '正在加载 FASTA/FASTQ...';
  try {
    const response = await fetch(await getFileDownloadUrl(props.file));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    records.value = parse(await response.text());
    status.value = records.value.length ? '' : '未找到可解析的 FASTA/FASTQ 序列';
  } catch (error) {
    console.error(error);
    status.value = 'FASTA/FASTQ 预览失败，请确认文件可访问且格式正确';
  }
}

watch(() => props.file, load, { immediate: true });
watch(selectedIndex, () => { startInput.value = 1; motif.value = ''; activeHitIndex.value = -1; });
watch(motif, () => { activeHitIndex.value = -1; });
</script>

<style scoped>
.tool-button { @apply h-8 rounded border border-slate-300 bg-white px-2 text-xs hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40; }
.primary-button { @apply h-8 rounded bg-slate-800 px-3 text-xs text-white hover:bg-slate-700; }
.stat-card { @apply flex min-w-0 flex-col rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm; }
.stat-card span { @apply text-[10px] uppercase tracking-wide text-slate-400; }
.stat-card strong { @apply mt-1 text-sm font-semibold text-slate-800; }
.overview-track { @apply relative flex h-8 cursor-crosshair overflow-hidden rounded border border-slate-200 bg-slate-100; }
.overview-bin { @apply h-full min-w-0 flex-1; }
.overview-window { @apply pointer-events-none absolute inset-y-0 rounded border-2 border-slate-900 bg-white/20 shadow-sm; }
.legend { @apply inline-flex items-center gap-1 text-slate-500; }
.legend i { @apply h-2.5 w-2.5 rounded-sm; }
.sequence-row { display: grid; grid-template-columns: 76px max-content 76px; align-items: start; min-width: max-content; }
.coordinate-label { @apply pr-3 pt-5 text-right text-[11px] text-slate-400; }
.base-group { @apply border-l border-slate-100 pl-1; }
.base-cell { @apply inline-flex h-6 w-[15px] items-center justify-center rounded-sm text-[14px] font-semibold; }
.motif-hit { @apply bg-yellow-200 ring-1 ring-inset ring-yellow-500; }
.quality-bar { @apply mx-[2.5px] block w-[10px] rounded-t-sm opacity-80; }
.quality-low { @apply bg-red-400; }.quality-medium { @apply bg-amber-400; }.quality-high { @apply bg-emerald-500; }
.base-a { color: #2563eb; }.base-c { color: #16a34a; }.base-g { color: #d97706; }.base-t { color: #dc2626; }
.base-n { color: #7c3aed; background: #f3e8ff; }.base-other { color: #64748b; }
.base-low-quality { color: #be185d; background: #fce7f3; text-decoration: underline wavy #be185d; }
</style>
