<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white text-sm">
    <div class="flex flex-wrap items-end gap-2 border-b border-[var(--border-main)] px-3 py-2">
      <label class="flex flex-col gap-1 text-xs"><span class="text-[var(--text-tertiary)]">参考序列</span><select v-model="reference" class="h-8 min-w-36 rounded border px-2" @change="selectReference"><option v-for="item in prepared?.references || []" :key="item.name" :value="item.name">{{ item.name }} · {{ formatNumber(item.length) }} bp</option></select></label>
      <label class="flex flex-col gap-1 text-xs"><span class="text-[var(--text-tertiary)]">起点（0-based）</span><input v-model.number="draftStart" type="number" min="0" class="h-8 w-32 rounded border px-2" /></label>
      <label class="flex flex-col gap-1 text-xs"><span class="text-[var(--text-tertiary)]">终点</span><input v-model.number="draftEnd" type="number" min="1" class="h-8 w-32 rounded border px-2" /></label>
      <button type="button" class="h-8 rounded bg-[#2b7659] px-3 text-xs text-white hover:bg-[#236249]" :disabled="loading" @click="loadRegion">定位</button>
      <button type="button" class="h-8 rounded border px-2 text-xs hover:bg-gray-50" :disabled="loading" @click="zoom(0.5)">放大</button>
      <button type="button" class="h-8 rounded border px-2 text-xs hover:bg-gray-50" :disabled="loading" @click="zoom(2)">缩小</button>
      <button type="button" class="h-8 rounded border px-2 text-xs hover:bg-gray-50" @click="logCoverage = !logCoverage">深度：{{ logCoverage ? '对数' : '线性' }}</button>
      <button type="button" class="h-8 rounded border px-2 text-xs hover:bg-gray-50" :disabled="!region" @click="exportRegion">导出当前区域</button>
      <label class="h-8 cursor-pointer rounded border px-2 py-1.5 text-xs hover:bg-gray-50">叠加 GFF/BED/VCF<input class="hidden" type="file" multiple accept=".gff,.gff3,.gtf,.bed,.vcf" @change="loadAnnotationFiles" /></label>
      <span class="ml-auto pb-1 text-xs text-[var(--text-tertiary)]">{{ prepared?.format || '' }} · {{ prepared?.sort_order || '排序未知' }} · {{ prepared?.read_groups || 0 }} 个读组</span>
    </div>

    <div v-if="status" class="flex flex-1 items-center justify-center p-8 text-sm text-[var(--text-tertiary)]">{{ status }}</div>
    <div v-else-if="region" class="min-h-0 flex-1 overflow-auto p-3" @wheel.ctrl.prevent="wheelZoom">
      <div class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-tertiary)]">
        <span>{{ region.reference }}:{{ formatNumber(region.start + 1) }}-{{ formatNumber(region.end) }}</span>
        <span>{{ region.returned_reads }} 条 reads</span>
        <span>窗口 {{ formatNumber(region.end - region.start) }} bp</span>
        <span v-if="region.truncated" class="text-amber-700">记录较多，已限制显示数量</span>
      </div>

      <section class="rounded-lg border border-[var(--border-main)] bg-[#fbfcfe] p-3">
        <div class="mb-1 flex justify-between text-xs font-medium"><span>覆盖深度</span><span>最大 {{ maxDepth }}×</span></div>
        <svg class="h-32 w-full overflow-visible" viewBox="0 0 1000 120" preserveAspectRatio="none" role="img" aria-label="覆盖深度图">
          <line x1="0" y1="110" x2="1000" y2="110" stroke="#cbd5e1" />
          <path :d="coveragePath" fill="rgba(43,118,89,.22)" stroke="#2b7659" stroke-width="2" vector-effect="non-scaling-stroke" />
        </svg>
      </section>

      <section class="mt-3 min-w-[760px] rounded-lg border border-[var(--border-main)] bg-white p-3">
        <div class="mb-2 flex justify-between text-xs font-medium"><span>比对轨道</span><span class="font-normal text-[var(--text-tertiary)]">点击 read 查看详情</span></div>
        <svg class="w-full" :style="{ height: `${trackHeight}px` }" :viewBox="`0 0 1000 ${trackHeight}`" preserveAspectRatio="none" @pointerdown="startPan" @pointerup="endPan" @pointerleave="endPan">
          <g class="select-none text-[11px]" fill="#64748b">
            <template v-for="tick in ticks" :key="tick.value">
              <line :x1="tick.x" y1="18" :x2="tick.x" :y2="trackHeight" stroke="#eef2f7" />
              <text :x="tick.x" y="12" text-anchor="middle" preserveAspectRatio="xMidYMid meet">{{ formatNumber(tick.value + 1) }}</text>
            </template>
          </g>
          <g>
            <line v-for="pair in pairLinks" :key="pair.key" :x1="pair.x1" :y1="pair.y1" :x2="pair.x2" :y2="pair.y2" stroke="#94a3b8" stroke-dasharray="3 2" />
            <g v-for="item in laidOutReads" :key="item.key" class="cursor-pointer hover:opacity-75" @click.stop="selectedRead = item.read">
              <rect :x="item.x" :y="item.y" :width="Math.max(item.width, 1.5)" height="12" rx="2" :fill="readColor(item.read)" fill-opacity=".25" />
              <rect v-for="(block, blockIndex) in item.blocks" :key="`b-${blockIndex}`" :x="block.x" :y="item.y" :width="Math.max(block.width, 1.5)" height="12" rx="2" :fill="readColor(item.read)" />
              <path v-for="(splice, spliceIndex) in item.splices" :key="`s-${spliceIndex}`" :d="splice.path" fill="none" stroke="#8b5cf6" stroke-width="1.5" />
              <circle v-for="(mismatch, mismatchIndex) in item.mismatches" :key="`m-${mismatchIndex}`" :cx="mismatch.x" :cy="item.y + 6" r="2.5" fill="#ef4444"><title>{{ mismatch.reference_base }}→{{ mismatch.query_base }}</title></circle>
              <path v-for="(insertion, insertionIndex) in item.insertions" :key="`i-${insertionIndex}`" :d="`M${insertion.x - 3},${item.y} L${insertion.x + 3},${item.y} L${insertion.x},${item.y + 6} Z`" fill="#2563eb"><title>插入 {{ insertion.length }} bp</title></path>
              <rect v-for="(deletion, deletionIndex) in item.deletions" :key="`d-${deletionIndex}`" :x="deletion.x" :y="item.y + 4" :width="Math.max(deletion.width, 2)" height="4" fill="#fff" stroke="#dc2626"><title>缺失 {{ deletion.length }} bp</title></rect>
              <title>{{ item.read.name }} · MAPQ {{ item.read.mapq }} · {{ item.read.cigar }}</title>
            </g>
          </g>
        </svg>
        <div v-if="!region.reads.length" class="py-8 text-center text-xs text-[var(--text-tertiary)]">当前区域没有比对记录</div>
      </section>

      <section v-if="visibleAnnotations.length" class="mt-3 min-w-[760px] rounded-lg border border-[var(--border-main)] bg-white p-3">
        <div class="mb-2 flex justify-between text-xs font-medium"><span>注释与变异轨道</span><button type="button" class="font-normal text-[var(--text-tertiary)]" @click="annotations = []">清空叠加</button></div>
        <svg class="h-20 w-full" viewBox="0 0 1000 80" preserveAspectRatio="none">
          <g v-for="(item, index) in visibleAnnotations" :key="`${item.source}-${index}`">
            <path v-if="item.kind === 'variant'" :d="`M${item.x},10 L${item.x - 5},20 L${item.x},30 L${item.x + 5},20 Z`" fill="#dc2626"><title>{{ item.label }}</title></path>
            <rect v-else :x="item.x" :y="10 + (index % 4) * 16" :width="Math.max(item.width, 2)" height="10" rx="2" fill="#0ea5e9"><title>{{ item.label }}</title></rect>
          </g>
        </svg>
      </section>

      <div v-if="selectedRead" class="sticky bottom-0 mt-3 rounded-lg border border-[#b9d7c8] bg-white/95 p-3 text-xs shadow-lg backdrop-blur">
        <div class="flex items-center justify-between"><strong class="break-all">{{ selectedRead.name }}</strong><button type="button" class="text-[var(--text-tertiary)]" @click="selectedRead = undefined">关闭</button></div>
        <dl class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-4"><div><dt class="text-[var(--text-tertiary)]">坐标</dt><dd>{{ selectedRead.start + 1 }}-{{ selectedRead.end }}</dd></div><div><dt class="text-[var(--text-tertiary)]">方向</dt><dd>{{ selectedRead.reverse ? '反向' : '正向' }}</dd></div><div><dt class="text-[var(--text-tertiary)]">MAPQ</dt><dd>{{ selectedRead.mapq }}</dd></div><div><dt class="text-[var(--text-tertiary)]">CIGAR</dt><dd>{{ selectedRead.cigar }}</dd></div><div><dt class="text-[var(--text-tertiary)]">NM / MD</dt><dd>{{ selectedRead.nm ?? '-' }} / {{ selectedRead.md ?? '-' }}</dd></div><div><dt class="text-[var(--text-tertiary)]">Read Group</dt><dd>{{ selectedRead.read_group ?? '-' }}</dd></div><div><dt class="text-[var(--text-tertiary)]">Mate</dt><dd>{{ selectedRead.mate_reference ?? '-' }}:{{ selectedRead.mate_start == null ? '-' : selectedRead.mate_start + 1 }}</dd></div><div><dt class="text-[var(--text-tertiary)]">模板长度</dt><dd>{{ selectedRead.template_length }}</dd></div></dl>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { prepareAlignmentPreview, previewAlignmentRegion, releaseAlignmentPreview, type AlignmentPreviewPreparation, type AlignmentPreviewRead, type AlignmentRegionPreview, type FileInfo } from '../../api/file';

const props = defineProps<{ file: FileInfo }>();
const prepared = ref<AlignmentPreviewPreparation>();
const region = ref<AlignmentRegionPreview>();
const reference = ref('');
const draftStart = ref(0);
const draftEnd = ref(100000);
const status = ref('正在读取比对文件...');
const loading = ref(false);
const logCoverage = ref(false);
const selectedRead = ref<AlignmentPreviewRead>();
type Annotation = { reference: string; start: number; end: number; label: string; source: string; kind: 'feature' | 'variant' };
const annotations = ref<Annotation[]>([]);
let panStart: { x: number; start: number; end: number } | undefined;

const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN').format(value);
const currentReference = computed(() => prepared.value?.references.find(item => item.name === reference.value));
const displayDepth = (value: number) => logCoverage.value ? Math.log10(value + 1) : value;
const maxDepth = computed(() => Math.max(0, ...(region.value?.coverage.map(item => item.depth) || [])));
const coveragePath = computed(() => {
  const values = region.value?.coverage || [];
  if (!values.length) return '';
  const denominator = Math.max(displayDepth(maxDepth.value), 1);
  const points = values.map((item, index) => `${index / Math.max(values.length - 1, 1) * 1000},${110 - displayDepth(item.depth) / denominator * 100}`);
  return `M0,110 L${points.join(' L')} L1000,110 Z`;
});
const ticks = computed(() => Array.from({ length: 6 }, (_, index) => ({ x: index * 200, value: Math.round((region.value?.start || 0) + ((region.value?.end || 0) - (region.value?.start || 0)) * index / 5) })));
const laidOutReads = computed(() => {
  if (!region.value) return [];
  const rows: number[] = [];
  const width = region.value.end - region.value.start;
  return [...region.value.reads].sort((a, b) => a.start - b.start).map((read, index) => {
    let row = rows.findIndex(end => end + Math.max(width * 0.004, 1) <= read.start);
    if (row < 0) { row = rows.length; rows.push(read.end); } else rows[row] = read.end;
    const scale = (position: number) => (position - region.value!.start) / width * 1000;
    const y = 24 + row * 16;
    return { key: `${read.name}-${index}`, read, row, x: scale(read.start), width: (read.end - read.start) / width * 1000, y,
      blocks: (read.blocks || []).map(block => ({ x: scale(block.start), width: (block.end - block.start) / width * 1000 })),
      mismatches: (read.mismatches || []).map(item => ({ ...item, x: scale(item.position) })),
      insertions: (read.insertions || []).map(item => ({ ...item, x: scale(item.position) })),
      deletions: (read.deletions || []).map(item => ({ ...item, x: scale(item.start), width: (item.end - item.start) / width * 1000 })),
      splices: (read.splices || []).map(item => { const x1 = scale(item.start); const x2 = scale(item.end); return { ...item, path: `M${x1},${y + 5} Q${(x1 + x2) / 2},${Math.max(2, y - 10)} ${x2},${y + 5}` }; }),
    };
  });
});
const pairLinks = computed(() => {
  const grouped = new Map<string, typeof laidOutReads.value>();
  for (const item of laidOutReads.value) grouped.set(item.read.name, [...(grouped.get(item.read.name) || []), item]);
  return [...grouped.entries()].flatMap(([key, items]) => items.length === 2 ? [{ key, x1: items[0].x + items[0].width, y1: items[0].y + 6, x2: items[1].x, y2: items[1].y + 6 }] : []);
});
const visibleAnnotations = computed(() => {
  if (!region.value) return [];
  const width = region.value.end - region.value.start;
  return annotations.value.filter(item => item.reference === region.value!.reference && item.end > region.value!.start && item.start < region.value!.end).slice(0, 1000).map(item => ({ ...item, x: (item.start - region.value!.start) / width * 1000, width: (item.end - item.start) / width * 1000 }));
});
const trackHeight = computed(() => Math.max(100, 38 + (Math.max(-1, ...laidOutReads.value.map(item => item.row)) + 1) * 16));

function readColor(read: AlignmentPreviewRead) { if (read.duplicate || read.secondary || read.supplementary) return '#94a3b8'; if (read.mapq < 10) return '#f59e0b'; return read.reverse ? '#7c6ee6' : '#2b8a6e'; }
function selectReference() { const length = currentReference.value?.length || 100000; const suggested = reference.value === prepared.value?.suggested_reference ? Math.max(0, prepared.value?.suggested_start || 0) : 0; draftStart.value = Math.min(suggested, Math.max(0, length - 1)); draftEnd.value = Math.min(length, draftStart.value + 100000); loadRegion(); }
async function loadRegion() {
  if (!reference.value) return;
  const limit = currentReference.value?.length || Number.MAX_SAFE_INTEGER;
  const start = Math.max(0, Math.floor(Number(draftStart.value) || 0));
  const end = Math.min(limit, Math.max(start + 1, Math.floor(Number(draftEnd.value) || start + 1)));
  if (end - start > 2_000_000) { status.value = '单次预览区域不能超过 2,000,000 bp'; return; }
  loading.value = true; status.value = '正在读取当前区域...'; selectedRead.value = undefined;
  try { region.value = await previewAlignmentRegion(props.file.file_id, prepared.value!.preview_id, reference.value, start, end); draftStart.value = start; draftEnd.value = end; status.value = ''; }
  catch (error: any) { console.error(error); status.value = error?.response?.data?.detail || '比对区域读取失败；CRAM 文件可能需要参考序列'; }
  finally { loading.value = false; }
}
function zoom(factor: number, center?: number) { const start = region.value?.start ?? draftStart.value; const end = region.value?.end ?? draftEnd.value; const midpoint = center ?? (start + end) / 2; const half = Math.max(50, Math.min(1_000_000, (end - start) * factor / 2)); const limit = currentReference.value?.length || Number.MAX_SAFE_INTEGER; draftStart.value = Math.max(0, Math.round(midpoint - half)); draftEnd.value = Math.min(limit, Math.round(midpoint + half)); loadRegion(); }
function wheelZoom(event: WheelEvent) { if (!region.value) return; const target = event.currentTarget as HTMLElement; const ratio = Math.min(1, Math.max(0, event.offsetX / Math.max(target.clientWidth, 1))); zoom(event.deltaY < 0 ? 0.7 : 1.4, region.value.start + ratio * (region.value.end - region.value.start)); }
function startPan(event: PointerEvent) { if (region.value) panStart = { x: event.clientX, start: region.value.start, end: region.value.end }; }
function endPan(event: PointerEvent) { if (!panStart || !region.value) return; const delta = event.clientX - panStart.x; const width = panStart.end - panStart.start; const shift = Math.round(-delta / Math.max((event.currentTarget as SVGElement).clientWidth, 1) * width); const limit = currentReference.value?.length || Number.MAX_SAFE_INTEGER; let start = Math.max(0, panStart.start + shift); let end = Math.min(limit, panStart.end + shift); if (end - start < width) start = Math.max(0, end - width); draftStart.value = start; draftEnd.value = end; panStart = undefined; if (Math.abs(delta) > 5) loadRegion(); }
function exportRegion() { if (!region.value) return; const blob = new Blob([JSON.stringify(region.value, null, 2)], { type: 'application/json;charset=utf-8' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${region.value.reference}_${region.value.start + 1}_${region.value.end}.alignment.json`; anchor.click(); URL.revokeObjectURL(url); }
async function loadAnnotationFiles(event: Event) {
  const files = [...((event.target as HTMLInputElement).files || [])]; const loaded: Annotation[] = [];
  for (const file of files) {
    const extension = file.name.toLowerCase().split('.').pop(); const lines = (await file.text()).split(/\r?\n/);
    for (const line of lines) {
      if (!line || line.startsWith('#')) continue; const fields = line.split('\t');
      if (extension === 'bed' && fields.length >= 3) loaded.push({ reference: fields[0], start: Number(fields[1]), end: Number(fields[2]), label: fields[3] || `${fields[0]}:${Number(fields[1]) + 1}-${fields[2]}`, source: file.name, kind: 'feature' });
      else if (extension === 'vcf' && fields.length >= 5) { const start = Number(fields[1]) - 1; loaded.push({ reference: fields[0], start, end: start + Math.max(1, fields[3].length), label: `${fields[3]}→${fields[4]}`, source: file.name, kind: 'variant' }); }
      else if (fields.length >= 9) loaded.push({ reference: fields[0], start: Number(fields[3]) - 1, end: Number(fields[4]), label: `${fields[2]} · ${fields[8]}`, source: file.name, kind: 'feature' });
    }
  }
  annotations.value = [...annotations.value, ...loaded].slice(0, 100000);
  (event.target as HTMLInputElement).value = '';
}
async function initialize() { status.value = '正在读取比对文件...'; try { prepared.value = await prepareAlignmentPreview(props.file.file_id); if (!prepared.value.references.length) { status.value = '文件头中没有参考序列'; return; } reference.value = prepared.value.suggested_reference || prepared.value.references[0].name; selectReference(); } catch (error: any) { console.error(error); status.value = error?.response?.data?.detail || 'SAM/BAM/CRAM 文件预览初始化失败'; } }
watch(() => props.file.file_id, async (_value, oldValue) => { if (prepared.value?.preview_id && oldValue) await releaseAlignmentPreview(oldValue, prepared.value.preview_id).catch(() => undefined); initialize(); });
onMounted(initialize);
onBeforeUnmount(() => { if (prepared.value?.preview_id) releaseAlignmentPreview(props.file.file_id, prepared.value.preview_id).catch(() => undefined); });
</script>
