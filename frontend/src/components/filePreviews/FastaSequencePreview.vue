<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white text-slate-700">
    <div class="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3 text-xs">
      <span class="font-semibold text-slate-800">FASTA/FASTQ 序列预览</span>
      <span>序列 {{ records.length }} 条</span>
      <span v-if="current">长度 {{ current.sequence.length.toLocaleString() }} bp</span>
      <span v-if="current">GC {{ gcPercent.toFixed(2) }}%</span>
      <select v-if="records.length > 1" v-model="selectedIndex" class="h-7 max-w-[260px] rounded border border-slate-300 bg-white px-2 text-xs">
        <option v-for="(record, index) in records" :key="`${record.id}-${index}`" :value="index">{{ record.id }}</option>
      </select>
      <label class="ml-auto flex items-center gap-1">起始位置
        <input v-model.number="startInput" type="number" min="1" :max="Math.max(1, current?.sequence.length || 1)" class="h-7 w-24 rounded border border-slate-300 px-2" @keyup.enter="jump" />
      </label>
      <button type="button" class="rounded bg-slate-800 px-3 py-1.5 text-white hover:bg-slate-700" @click="jump">跳转</button>
      <button type="button" class="rounded border border-slate-300 px-3 py-1.5 hover:bg-slate-50" @click="copyWindow">复制当前窗口</button>
    </div>
    <div v-if="status" class="flex flex-1 items-center justify-center p-6 text-sm text-slate-500">{{ status }}</div>
    <div v-else-if="current" class="min-h-0 flex-1 overflow-auto p-4">
      <div class="mx-auto min-w-[760px] max-w-[1200px] font-mono text-[13px] leading-7">
        <div class="mb-1 flex text-[11px] text-slate-400">
          <span class="w-24 shrink-0"></span><span class="sequence-ruler">{{ ruler }}</span>
        </div>
        <div class="mb-3 flex border-b border-slate-200 pb-2 text-[11px] text-slate-400">
          <span class="w-24 shrink-0 text-right pr-3">位置</span><span class="sequence-ruler">{{ tickLine }}</span>
        </div>
        <div class="flex">
          <span class="w-24 shrink-0 select-none pr-3 text-right text-slate-400">{{ visibleStart.toLocaleString() }}</span>
          <pre class="sequence-line" aria-label="FASTA/FASTQ 序列"><span v-for="(base, index) in visibleSequence" :key="index" :class="baseClass(base, index)" :title="`位置 ${visibleStart + index}${current?.qualities ? `，质量 ${current.qualities[visibleStart - 1 + index] ?? '-'} (Phred)` : ''}`">{{ base }}</span></pre>
        </div>
        <div class="mt-4 text-xs text-slate-400">显示 {{ visibleStart.toLocaleString() }}–{{ visibleEnd.toLocaleString() }} / {{ current.sequence.length.toLocaleString() }} bp；每 10 个碱基分组。<span v-if="current.qualities">低于 Q20 的碱基已标记。</span></div>
      </div>
    </div>
    <div v-else class="flex flex-1 items-center justify-center text-sm text-slate-500">未找到可解析的 FASTA 序列</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';
import { copyToClipboard } from '../../utils/dom';

const props = defineProps<{ file: FileInfo }>();
const records = ref<{ id: string; sequence: string; qualities?: number[] }[]>([]);
const selectedIndex = ref(0);
const startInput = ref(1);
const status = ref('');
const windowSize = 500;
const current = computed(() => records.value[selectedIndex.value]);
const visibleStart = computed(() => Math.min(Math.max(1, Number(startInput.value) || 1), current.value?.sequence.length || 1));
const visibleEnd = computed(() => Math.min(visibleStart.value + windowSize - 1, current.value?.sequence.length || 0));
const visibleSequence = computed(() => current.value?.sequence.slice(visibleStart.value - 1, visibleEnd.value) || '');
const gcPercent = computed(() => { const s = current.value?.sequence || ''; return s ? ((s.match(/[GC]/gi) || []).length / s.length) * 100 : 0; });
const ruler = computed(() => Array.from({ length: Math.ceil(visibleSequence.value.length / 10) }, (_, i) => `${visibleStart.value + i * 10}`.padEnd(10, ' ')).join(''));
const tickLine = computed(() => Array.from({ length: Math.ceil(visibleSequence.value.length / 10) }, () => '|--------- ').join('').slice(0, visibleSequence.value.length));
const baseClass = (base: string, index = 0) => {
  const quality = current.value?.qualities?.[visibleStart.value - 1 + index];
  if (typeof quality === 'number' && quality < 20) return 'base-low-quality';
  return ({ A: 'base-a', C: 'base-c', G: 'base-g', T: 'base-t', U: 'base-t', N: 'base-n' }[base.toUpperCase()] || 'base-other');
};
const jump = () => { startInput.value = visibleStart.value; };
const copyWindow = async () => { if (visibleSequence.value) await copyToClipboard(visibleSequence.value); };
const parse = (text: string) => {
  const result: { id: string; sequence: string; qualities?: number[] }[] = []; let item: { id: string; sequence: string; qualities?: number[] } | null = null;
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  if (lines[0]?.startsWith('@') && lines.length >= 4 && lines[2] === '+') {
    for (let i = 0; i + 3 < lines.length; i += 4) {
      if (!lines[i].startsWith('@')) continue;
      const sequence = lines[i + 1].replace(/\s+/g, '').toUpperCase();
      if (sequence) result.push({ id: lines[i].slice(1).trim() || `序列 ${result.length + 1}`, sequence, qualities: [...lines[i + 3]].map(c => c.charCodeAt(0) - 33).slice(0, sequence.length) });
    }
    return result;
  }
  for (const line of lines) {
    if (line.startsWith('>')) { if (item) result.push(item); item = { id: line.slice(1).trim() || `序列 ${result.length + 1}`, sequence: '' }; }
    else if (item) item.sequence += line.replace(/\s+/g, '').toUpperCase();
  }
  if (item) result.push(item); return result.filter(x => x.sequence);
};
const load = async () => {
  records.value = []; selectedIndex.value = 0; status.value = '正在加载 FASTA...';
  try { const response = await fetch(await getFileDownloadUrl(props.file)); if (!response.ok) throw new Error(`HTTP ${response.status}`); records.value = parse(await response.text()); status.value = records.value.length ? '' : '未找到可解析的 FASTA 序列'; }
  catch (error) { console.error(error); status.value = 'FASTA 预览失败，请确认文件可访问且格式正确'; }
};
watch(() => props.file, load, { immediate: true });
</script>

<style scoped>
.sequence-ruler, .sequence-line { white-space: pre; letter-spacing: .08em; }
.sequence-line { margin: 0; user-select: text; }
.base-a { color: #2563eb; }.base-c { color: #16a34a; }.base-g { color: #d97706; }.base-t { color: #dc2626; }.base-n { color: #7c3aed; background: #f3e8ff; }.base-other { color: #64748b; }.base-low-quality { color: #be185d; background: #fce7f3; text-decoration: underline wavy #be185d; }
</style>
