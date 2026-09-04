<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white text-slate-700">
    <div class="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 text-xs">
      <span class="font-semibold text-slate-800">基因组区域浏览器</span>
      <select v-model="chromosome" class="h-7 max-w-[180px] rounded border border-slate-300 bg-white px-2"><option v-for="name in chromosomes" :key="name" :value="name">{{ name }}</option></select>
      <input v-model.number="regionStart" type="number" min="1" class="h-7 w-24 rounded border border-slate-300 px-2" aria-label="起始坐标" />
      <span>—</span><input v-model.number="regionEnd" type="number" min="1" class="h-7 w-24 rounded border border-slate-300 px-2" aria-label="结束坐标" />
      <button type="button" class="rounded bg-slate-800 px-2.5 py-1.5 text-white" @click="applyRegion">定位</button>
      <button type="button" class="rounded border border-slate-300 px-2.5 py-1.5" @click="zoom(-1)">−</button><button type="button" class="rounded border border-slate-300 px-2.5 py-1.5" @click="zoom(1)">＋</button>
      <div class="ml-auto flex items-center gap-2"><label v-for="track in trackDefinitions" :key="track.id" class="flex items-center gap-1"><input v-model="visibleTracks" type="checkbox" :value="track.id" />{{ track.label }}</label></div>
    </div>
    <div v-if="status" class="flex flex-1 items-center justify-center text-sm text-slate-500">{{ status }}</div>
    <div v-else class="min-h-0 flex-1 overflow-auto p-4">
      <div class="mb-2 flex justify-between text-[11px] text-slate-400"><span>{{ chromosome }}:{{ format(regionStart) }}–{{ format(regionEnd) }}</span><span>{{ visibleFeatures.length }} 个区域/变异</span></div>
      <svg :viewBox="`0 0 ${width} ${height}`" class="h-auto min-w-[760px] w-full rounded border border-slate-200 bg-slate-50" @mousedown="beginPan" @mousemove="pan" @mouseup="endPan" @mouseleave="endPan" @wheel.prevent="onWheel">
        <g v-for="tick in ticks" :key="tick.value"><line :x1="scaleX(tick.value)" y1="18" :x2="scaleX(tick.value)" :y2="height - 10" stroke="#cbd5e1" stroke-width="1" /><text :x="scaleX(tick.value)" y="14" text-anchor="middle" font-size="10" fill="#64748b">{{ format(tick.value) }}</text></g>
        <g v-for="(track, trackIndex) in trackDefinitions" :key="track.id" v-show="visibleTracks.includes(track.id)"><text x="8" :y="trackY(trackIndex) + 16" font-size="11" fill="#475569">{{ track.label }}</text><line :x1="labelWidth" :x2="width - 12" :y1="trackY(trackIndex) + 25" :y2="trackY(trackIndex) + 25" stroke="#94a3b8" />
          <g v-for="feature in featureForTrack(track.id)" :key="feature.key" @click.stop="selected = feature"><rect :x="scaleX(feature.start)" :y="trackY(trackIndex) + 10" :width="Math.max(3, scaleX(feature.end) - scaleX(feature.start))" height="28" rx="3" :fill="feature.color" opacity=".82" /><text v-if="scaleX(feature.end) - scaleX(feature.start) > 52" :x="scaleX(feature.start) + 4" :y="trackY(trackIndex) + 28" font-size="10" fill="white">{{ feature.label }}</text></g>
        </g>
      </svg>
      <div v-if="selected" class="mt-3 rounded border border-slate-200 bg-white p-3 text-xs shadow-sm"><div class="mb-1 flex items-center justify-between font-medium"><span>{{ selected.label }}</span><button type="button" class="text-slate-400" @click="selected = null">×</button></div><div>位置：{{ selected.chromosome }}:{{ selected.start }}–{{ selected.end }}</div><div v-if="selected.detail" class="mt-1 break-all text-slate-500">{{ selected.detail }}</div></div>
      <div class="mt-2 text-[11px] text-slate-400">拖拽图面可平移；滚轮缩放；点击轨道元素查看详细字段。</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';

const props = defineProps<{ file: FileInfo }>();
type Feature = { key: string; chromosome: string; start: number; end: number; label: string; track: string; color: string; detail?: string };
const source = ref(''); const status = ref('正在加载基因组数据...'); const features = ref<Feature[]>([]); const selected = ref<Feature | null>(null);
const chromosome = ref(''); const regionStart = ref(1); const regionEnd = ref(1000); const appliedStart = ref(1); const appliedEnd = ref(1000); const visibleTracks = ref<string[]>([]);
const width = 1100; const labelWidth = 118; const trackHeight = 58; let panStart = 0; let panOrigin = 0; let dragging = false;
const extension = computed(() => String(props.file.filename || '').toLowerCase().split('.').pop() || '');
const trackDefinitions = computed(() => extension.value === 'vcf' ? [{ id: 'variant', label: '变异', color: '#dc2626' }] : extension.value === 'sam' ? [{ id: 'alignment', label: '比对', color: '#2563eb' }] : [{ id: 'annotation', label: '注释', color: '#16a34a' }, { id: 'interval', label: '区间', color: '#7c3aed' }]);
const height = computed(() => 70 + trackHeight * Math.max(1, trackDefinitions.value.length));
const chromosomes = computed(() => [...new Set(features.value.map(item => item.chromosome))]);
const visibleFeatures = computed(() => features.value.filter(item => item.chromosome === chromosome.value && item.end >= appliedStart.value && item.start <= appliedEnd.value));
const ticks = computed(() => { const span = Math.max(1, appliedEnd.value - appliedStart.value); const step = Math.pow(10, Math.max(0, Math.floor(Math.log10(span / 5)))); const list = []; for (let value = Math.ceil(appliedStart.value / step) * step; value <= appliedEnd.value; value += step) list.push({ value }); return list; });
const scaleX = (value: number) => labelWidth + ((value - appliedStart.value) / Math.max(1, appliedEnd.value - appliedStart.value)) * (width - labelWidth - 18);
const trackY = (index: number) => 35 + index * trackHeight;
const featureForTrack = (track: string) => visibleFeatures.value.filter(item => item.track === track);
const format = (value: number) => Number(value || 0).toLocaleString();
const cigarReferenceLength = (cigar: string) => {
  let length = 0;
  for (const match of cigar.matchAll(/(\d+)([MIDNSHP=X])/g)) {
    if (['M', 'D', 'N', '=', 'X'].includes(match[2])) length += Number(match[1]);
  }
  return length;
};
const parse = (text: string): Feature[] => { const ext = extension.value; const out: Feature[] = []; let index = 0; for (const line of text.split(/\r?\n/)) { if (!line || line.startsWith('#') || (ext === 'sam' && line.startsWith('@'))) continue; const v = line.split('\t'); let chr = ''; let start = 0; let end = 0; let label = ''; let track = ''; let color = ''; if (ext === 'vcf' && v.length >= 5) { chr = v[0]; start = Number(v[1]); end = start; label = v[2] && v[2] !== '.' ? v[2] : v[3] + '>' + v[4]; track = 'variant'; color = '#dc2626'; } else if (['gff', 'gff3', 'gtf'].includes(ext) && v.length >= 5) { chr = v[0]; start = Number(v[3]); end = Number(v[4]); label = v[2] || 'feature'; track = 'annotation'; color = '#16a34a'; } else if (['bed', 'bedgraph'].includes(ext) && v.length >= 3) { chr = v[0]; start = Number(v[1]) + 1; end = Number(v[2]); label = v[3] || '区间'; track = 'interval'; color = '#7c3aed'; } else if (ext === 'sam' && v.length >= 6 && v[2] !== '*' && Number(v[3]) > 0) { chr = v[2]; start = Number(v[3]); end = start + Math.max(1, cigarReferenceLength(v[5])) - 1; label = v[0]; track = 'alignment'; color = Number(v[4]) < 20 ? '#f59e0b' : '#2563eb'; } if (chr && Number.isFinite(start) && Number.isFinite(end) && end >= start) { out.push({ key: `${chr}-${start}-${end}-${index++}`, chromosome: chr, start, end, label, track, color, detail: line }); } if (out.length >= 3000) break; } return out; };
const applyRegion = () => { if (!chromosome.value) chromosome.value = chromosomes.value[0] || ''; appliedStart.value = Math.max(1, Number(regionStart.value) || 1); appliedEnd.value = Math.max(appliedStart.value + 1, Number(regionEnd.value) || appliedStart.value + 1); };
const zoom = (direction: number) => { const center = (appliedStart.value + appliedEnd.value) / 2; const span = Math.max(2, appliedEnd.value - appliedStart.value) * (direction < 0 ? 2 : .5); appliedStart.value = Math.max(1, Math.floor(center - span / 2)); appliedEnd.value = Math.ceil(center + span / 2); regionStart.value = appliedStart.value; regionEnd.value = appliedEnd.value; };
const beginPan = (event: MouseEvent) => { dragging = true; panStart = event.clientX; panOrigin = appliedStart.value; };
const pan = (event: MouseEvent) => { if (!dragging) return; const span = appliedEnd.value - appliedStart.value; const delta = Math.round((panStart - event.clientX) / (width - labelWidth) * span); appliedStart.value = Math.max(1, panOrigin + delta); appliedEnd.value = appliedStart.value + span; regionStart.value = appliedStart.value; regionEnd.value = appliedEnd.value; };
const endPan = () => { dragging = false; };
const onWheel = (event: WheelEvent) => zoom(event.deltaY > 0 ? -1 : 1);
const load = async () => { status.value = '正在加载基因组数据...'; try { const response = await fetch(await getFileDownloadUrl(props.file)); if (!response.ok) throw new Error(String(response.status)); source.value = await response.text(); features.value = parse(source.value); visibleTracks.value = trackDefinitions.value.map(track => track.id); chromosome.value = chromosomes.value[0] || ''; const chromosomeFeatures = features.value.filter(item => item.chromosome === chromosome.value); const min = Math.min(...chromosomeFeatures.map(item => item.start)); const max = Math.max(...chromosomeFeatures.map(item => item.end)); if (chromosomeFeatures.length) { const span = Math.max(1, max - min + 1); const padding = Math.max(10, Math.floor(span * .03)); regionStart.value = Math.max(1, min - padding); regionEnd.value = Math.max(regionStart.value + 1, Math.min(max + padding, regionStart.value + 100000)); } else { regionStart.value = 1; regionEnd.value = 1000; } appliedStart.value = regionStart.value; appliedEnd.value = regionEnd.value; status.value = features.value.length ? '' : '未找到可绘制的基因组记录'; } catch (error) { console.error(error); status.value = '基因组数据加载失败'; } };
watch(() => props.file, load, { immediate: true });
</script>
