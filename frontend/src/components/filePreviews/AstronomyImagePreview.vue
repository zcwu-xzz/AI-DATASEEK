<template>
  <div class="flex min-h-0 flex-1 flex-col bg-[#11151b] text-slate-100">
    <header class="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/10 bg-[#171c24] px-3 py-2 text-xs">
      <strong class="mr-2">{{ preparation?.format || 'FITS/TIFF' }} 科学影像</strong>
      <select v-model.number="datasetIndex" class="control max-w-[220px]" title="HDU 或页面">
        <option v-for="item in availableDatasets" :key="item.index" :value="item.index">
          {{ item.name }} · {{ item.shape.join('×') }} · {{ item.dtype }}
        </option>
      </select>
      <template v-if="leadingAxes.length">
        <label v-for="axis in leadingAxes" :key="axis.position" class="flex items-center gap-1">
          轴{{ axis.position + 1 }}
          <input v-model.number="sliceIndices[axis.position]" type="range" min="0" :max="axis.size - 1" class="w-20" />
          <span class="w-8 tabular-nums">{{ sliceIndices[axis.position] || 0 }}</span>
        </label>
      </template>
      <label v-if="channelCount > 1" class="flex items-center gap-1">通道
        <select v-model.number="band" class="control">
          <option v-if="channelCount === 3 || channelCount === 4" :value="0">RGB 合成</option>
          <option v-for="value in channelCount" :key="value" :value="value">{{ value }}</option>
        </select>
      </label>
      <select v-model="interval" class="control" title="显示范围">
        <option value="zscale">ZScale</option><option value="percentile">1–99%</option><option value="manual">手动</option>
      </select>
      <select v-model="stretch" class="control" title="拉伸">
        <option value="linear">线性</option><option value="log">对数</option><option value="sqrt">平方根</option><option value="asinh">Asinh</option>
      </select>
      <select v-model="colourMap" class="control" title="色带">
        <option value="gray">灰度</option><option value="viridis">Viridis</option><option value="heat">热力</option><option value="cool">冷色</option>
      </select>
      <label class="flex items-center gap-1"><input v-model="invert" type="checkbox" />反色</label>
      <button class="button" @click="resetView">复位</button>
      <button class="button" :class="regionMode ? 'active' : ''" @click="regionMode = !regionMode">框选统计</button>
      <button class="button" :class="detectedSources.length ? 'active' : ''" @click="toggleSources">{{ detectedSources.length ? `隐藏 ${detectedSources.length} 个源` : '检测并叠加源' }}</button>
      <button v-if="renderResult?.wcs?.celestial" class="button" :class="skyContext ? 'active' : ''" @click="toggleSkyContext">{{ skyContext ? '返回影像' : '天空底图' }}</button>
      <button v-if="preparation?.format === 'GeoTIFF'" class="button" :class="mapContext ? 'active' : ''" @click="toggleMapContext">{{ mapContext ? '返回影像' : '地图底图' }}</button>
      <button class="button" @click="render">重新渲染</button>
    </header>

    <div v-if="status" class="flex min-h-0 flex-1 items-center justify-center p-6 text-sm text-slate-300">{{ status }}</div>
    <div v-else class="preview-layout grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(220px,280px)]">
      <main
        ref="viewportRef"
        class="relative min-h-0 overflow-hidden bg-black"
        @wheel="onWheel"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointerleave="onPointerUp"
      >
        <div v-show="skyContext" ref="aladinRef" class="aladin-viewport absolute inset-0 z-30" />
        <div v-show="mapContext" ref="mapRef" class="absolute inset-0 z-30" />
        <div v-if="activeDataset?.kind === 'table'" class="absolute inset-0 overflow-auto bg-[#11151b] p-4">
          <table class="min-w-full border-collapse text-xs">
            <thead class="sticky top-0 bg-[#242b36]"><tr><th v-for="column in activeDataset.columns" :key="column.name" class="border border-white/10 px-2 py-1.5 text-left">{{ column.name }}<small v-if="column.unit" class="ml-1 text-slate-500">{{ column.unit }}</small></th></tr></thead>
            <tbody><tr v-for="(row, rowIndex) in activeDataset.table_preview" :key="rowIndex"><td v-for="column in activeDataset.columns" :key="column.name" class="max-w-[280px] truncate border border-white/10 px-2 py-1.5 text-slate-300" :title="String(row[column.name] ?? '')">{{ row[column.name] ?? '—' }}</td></tr></tbody>
          </table>
          <p class="mt-2 text-slate-500">显示前 {{ activeDataset.table_preview?.length || 0 }} / {{ activeDataset.row_count || 0 }} 行；完整表可使用 FITS 表提取工具导出。</p>
        </div>
        <div v-else-if="activeDataset?.kind === 'spectrum'" class="absolute inset-0 flex flex-col bg-[#11151b] p-5">
          <h3 class="mb-3 text-sm font-semibold">一维 FITS 数据 · {{ activeDataset.shape[0] }} 个采样点</h3>
          <svg viewBox="0 0 1000 420" preserveAspectRatio="none" class="min-h-0 flex-1 rounded border border-white/10 bg-black">
            <polyline :points="spectrumPoints" fill="none" stroke="#67e8f9" stroke-width="1.5" vector-effect="non-scaling-stroke" />
            <line x1="0" y1="400" x2="1000" y2="400" stroke="#64748b" /><line x1="0" y1="20" x2="0" y2="400" stroke="#64748b" />
          </svg>
          <p class="mt-2 text-[11px] text-slate-500">为保证浏览性能最多抽样显示 2000 点；科学分析和导出使用完整原始数据。</p>
        </div>
        <template v-else-if="!skyContext && !mapContext">
        <div class="absolute left-3 top-3 z-20 flex overflow-hidden rounded border border-white/20 bg-black/70">
          <button class="tool" @click.stop="zoomBy(1.3)">＋</button><button class="tool" @click.stop="zoomBy(0.77)">－</button>
        </div>
        <div
          class="absolute left-1/2 top-1/2 origin-center select-none"
          :style="{ transform: `translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px)) scale(${zoom})` }"
        >
          <img
            ref="imageRef"
            :src="imageUrl"
            class="block max-h-[calc(100vh-170px)] max-w-[calc(100vw-420px)] select-none"
            draggable="false"
            @load="imageLoaded = true"
          />
          <div v-if="crosshair" class="pointer-events-none absolute inset-0">
            <i class="absolute h-px w-full bg-amber-300/50" :style="{ top: `${crosshair.y * 100}%` }" />
            <i class="absolute h-full w-px bg-amber-300/50" :style="{ left: `${crosshair.x * 100}%` }" />
          </div>
          <button v-for="source in detectedSources" :key="source.id" type="button" class="absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-fuchsia-300 bg-transparent shadow-[0_0_4px_#f0abfc]" :style="{ left: `${source.x * 100 / Math.max(1, renderResult?.source_width || 1)}%`, top: `${source.y * 100 / Math.max(1, renderResult?.source_height || 1)}%` }" :title="`源 ${source.id} · S/N ${formatNumber(source.snr)}`" @pointerdown.stop @click.stop="selectedSource = source" />
          <div v-if="selectionRect" class="pointer-events-none absolute border border-cyan-300 bg-cyan-300/15" :style="selectionStyle" />
        </div>
        <div class="absolute bottom-3 left-3 rounded bg-black/70 px-2 py-1 text-[11px] text-slate-300">
          {{ renderResult?.source_width }} × {{ renderResult?.source_height }} · {{ Math.round(zoom * 100) }}%
          <template v-if="renderResult?.wcs?.celestial"> · 天球 WCS</template>
        </div>
        </template>
      </main>

      <aside class="min-h-0 overflow-auto border-l border-white/10 bg-[#171c24] p-3 text-xs">
        <section class="panel">
          <h3>显示范围</h3>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <label>最小值<input v-model.number="manualLow" type="number" class="control mt-1 w-full" /></label>
            <label>最大值<input v-model.number="manualHigh" type="number" class="control mt-1 w-full" /></label>
          </div>
          <svg v-if="histogramPoints" viewBox="0 0 240 72" class="mt-3 h-[72px] w-full overflow-visible">
            <polyline :points="histogramPoints" fill="none" stroke="#67e8f9" stroke-width="1.5" />
          </svg>
        </section>
        <section class="panel">
          <h3>像素与天球坐标</h3>
          <dl v-if="pixelInfo" class="mt-2 grid grid-cols-[70px_1fr] gap-y-1 text-slate-300">
            <dt>像素</dt><dd>{{ pixelInfo.x }}, {{ pixelInfo.y }}</dd>
            <dt>数值</dt><dd>{{ formatNumber(pixelInfo.value) }}</dd>
            <template v-if="pixelInfo.ra_deg !== undefined"><dt>RA</dt><dd>{{ formatNumber(pixelInfo.ra_deg) }}°</dd><dt>Dec</dt><dd>{{ formatNumber(pixelInfo.dec_deg) }}°</dd></template>
          </dl>
          <p v-else class="mt-2 text-slate-500">点击图像查询原始像元值。</p>
        </section>
        <section class="panel">
          <h3>图像统计</h3>
          <dl class="mt-2 grid grid-cols-[76px_1fr] gap-y-1 text-slate-300">
            <template v-for="(value, key) in renderResult?.statistics" :key="key"><dt>{{ statisticLabel(key) }}</dt><dd>{{ formatNumber(value) }}</dd></template>
          </dl>
        </section>
        <section v-if="regionInfo" class="panel">
          <h3>选区统计</h3>
          <dl class="mt-2 grid grid-cols-[76px_1fr] gap-y-1 text-slate-300">
            <template v-for="(value, key) in regionInfo" :key="key"><dt>{{ statisticLabel(key) }}</dt><dd>{{ Array.isArray(value) ? value.join(', ') : formatNumber(value) }}</dd></template>
          </dl>
        </section>
        <section v-if="selectedSource" class="panel">
          <h3>选中天体源</h3>
          <dl class="mt-2 grid grid-cols-[70px_1fr] gap-y-1 text-slate-300"><dt>编号</dt><dd>{{ selectedSource.id }}</dd><dt>像素</dt><dd>{{ selectedSource.x }}, {{ selectedSource.y }}</dd><dt>峰值</dt><dd>{{ formatNumber(selectedSource.peak) }}</dd><dt>信噪比</dt><dd>{{ formatNumber(selectedSource.snr) }}</dd><template v-if="selectedSource.ra_deg !== undefined"><dt>RA</dt><dd>{{ formatNumber(selectedSource.ra_deg) }}°</dd><dt>Dec</dt><dd>{{ formatNumber(selectedSource.dec_deg) }}°</dd></template></dl>
        </section>
        <section v-if="preparation?.geospatial" class="panel">
          <h3>空间信息</h3>
          <pre class="mt-2 whitespace-pre-wrap break-all text-[10px] leading-4 text-slate-400">{{ JSON.stringify(preparation.geospatial, null, 2) }}</pre>
        </section>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import {
  detectAstronomySources, inspectAstronomyPixel, inspectAstronomyRegion, prepareAstronomyPreview,
  releaseAstronomyPreview, renderAstronomyPreview,
  type AstronomyDetectedSource, type AstronomyPreviewPreparation, type AstronomyRenderResult, type AstronomySelection, type FileInfo,
} from '../../api/file';

const props = defineProps<{ file: FileInfo }>();
const preparation = ref<AstronomyPreviewPreparation | null>(null);
const renderResult = ref<AstronomyRenderResult | null>(null);
const status = ref('');
const datasetIndex = ref(0);
const sliceIndices = ref<number[]>([]);
const band = ref(1);
const stretch = ref('linear');
const interval = ref('zscale');
const colourMap = ref('gray');
const invert = ref(false);
const manualLow = ref<number>();
const manualHigh = ref<number>();
const zoom = ref(1);
const panX = ref(0);
const panY = ref(0);
const viewportRef = ref<HTMLElement | null>(null);
const imageRef = ref<HTMLImageElement | null>(null);
const imageLoaded = ref(false);
const pixelInfo = ref<Record<string, any> | null>(null);
const regionInfo = ref<Record<string, any> | null>(null);
const regionMode = ref(false);
const detectedSources = ref<AstronomyDetectedSource[]>([]);
const selectedSource = ref<AstronomyDetectedSource | null>(null);
const selectionStart = ref<{ x: number; y: number } | null>(null);
const selectionRect = ref<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
const crosshair = ref<{ x: number; y: number } | null>(null);
const skyContext = ref(false);
const aladinRef = ref<HTMLElement | null>(null);
let aladinInstance: any = null;
let aladinResizeObserver: ResizeObserver | null = null;
let aladinRefreshTimers: number[] = [];
const mapContext = ref(false);
const mapRef = ref<HTMLElement | null>(null);
let mapInstance: any = null;
const dragStart = ref<{ x: number; y: number; panX: number; panY: number } | null>(null);
let loadVersion = 0;
let renderVersion = 0;

const availableDatasets = computed(() => preparation.value?.datasets.filter(item => item.kind !== 'empty') || []);
const activeDataset = computed(() => availableDatasets.value.find(item => item.index === datasetIndex.value));
const leadingAxes = computed(() => (activeDataset.value?.shape || []).slice(0, -2).map((size, position) => ({ size, position })));
const channelCount = computed(() => preparation.value?.format === 'GeoTIFF' ? Number(preparation.value.geospatial?.bands || 1) : preparation.value?.format === 'TIFF' ? (activeDataset.value?.samples || 1) : 1);
const imageUrl = computed(() => renderResult.value ? `data:image/png;base64,${renderResult.value.image_base64}` : '');
const selectionStyle = computed(() => {
  const rect = selectionRect.value;
  if (!rect) return {};
  return { left: `${Math.min(rect.x0, rect.x1) * 100}%`, top: `${Math.min(rect.y0, rect.y1) * 100}%`, width: `${Math.abs(rect.x1 - rect.x0) * 100}%`, height: `${Math.abs(rect.y1 - rect.y0) * 100}%` };
});
const histogramPoints = computed(() => {
  const counts = renderResult.value?.histogram.counts || [];
  const maximum = Math.max(...counts, 0);
  if (!maximum) return '';
  return counts.map((value, index) => `${index * 240 / Math.max(1, counts.length - 1)},${70 - value * 68 / maximum}`).join(' ');
});
const spectrumPoints = computed(() => {
  const values = (activeDataset.value?.spectrum_preview || []).filter(item => typeof item.value === 'number') as Array<{ index: number; value: number }>;
  if (!values.length) return '';
  const minimum = Math.min(...values.map(item => item.value)), maximum = Math.max(...values.map(item => item.value));
  const maxIndex = Math.max(...values.map(item => item.index), 1);
  return values.map(item => `${item.index * 1000 / maxIndex},${400 - (item.value - minimum) * 380 / Math.max(1e-12, maximum - minimum)}`).join(' ');
});

const selection = (): AstronomySelection => ({ file_id: props.file.file_id, preview_id: preparation.value!.preview_id, dataset_index: datasetIndex.value, slice_indices: sliceIndices.value, band: band.value });
const formatNumber = (value: any) => typeof value === 'number' ? Number(value.toPrecision(7)).toLocaleString() : (value ?? '—');
const statisticLabel = (key: string) => ({ minimum: '最小值', maximum: '最大值', mean: '均值', median: '中位数', std: '标准差', sum: '总和', valid_count: '有效像元', missing_count: '无效像元', pixel_count: '像元数', bounds: '范围' }[key] || key);

async function render() {
  if (!preparation.value) return;
  const version = ++renderVersion;
  const previewId = preparation.value.preview_id;
  status.value = '正在按所选 HDU、切片和显示参数渲染…';
  try {
    const result = await renderAstronomyPreview(selection(), { stretch: stretch.value, interval: interval.value, low: interval.value === 'manual' ? manualLow.value : undefined, high: interval.value === 'manual' ? manualHigh.value : undefined, colour_map: colourMap.value, invert: invert.value });
    if (version !== renderVersion || preparation.value?.preview_id !== previewId) return;
    mapInstance?.setTarget(undefined); mapInstance = null; mapContext.value = false;
    renderResult.value = result;
    detectedSources.value = []; selectedSource.value = null;
    manualLow.value = result.display_min;
    manualHigh.value = result.display_max;
    imageLoaded.value = false;
    status.value = '';
  } catch (error: any) { if (version === renderVersion && preparation.value?.preview_id === previewId) status.value = error?.response?.data?.detail || '科学影像渲染失败'; }
}

async function loadFile(file: FileInfo) {
  const version = ++loadVersion;
  if (preparation.value) void releaseAstronomyPreview(props.file.file_id, preparation.value.preview_id).catch(() => undefined);
  status.value = '正在读取 FITS/TIFF 结构与科学元数据…';
  preparation.value = null; renderResult.value = null;
  renderVersion += 1;
  try {
    const result = await prepareAstronomyPreview(file.file_id);
    if (version !== loadVersion) return;
    preparation.value = result;
    datasetIndex.value = result.selected_dataset ?? result.datasets.find(item => item.kind !== 'empty')?.index ?? 0;
    sliceIndices.value = [];
    if (activeDataset.value?.kind === 'image') await render(); else status.value = '';
  } catch (error: any) { if (version === loadVersion) status.value = error?.response?.data?.detail || '无法打开 FITS/TIFF 文件'; }
}

function resetView() { zoom.value = 1; panX.value = 0; panY.value = 0; selectionRect.value = null; regionInfo.value = null; }
function clearAladinRefreshTimers() {
  aladinRefreshTimers.forEach(timer => window.clearTimeout(timer));
  aladinRefreshTimers = [];
}
function refreshAladinLayout() {
  if (!skyContext.value || !aladinInstance || !aladinRef.value) return;
  const rect = aladinRef.value.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  aladinInstance.view?.fixLayoutDimensions?.();
  aladinInstance.view?.requestRedraw?.();
}
function scheduleAladinLayoutRefresh() {
  clearAladinRefreshTimers();
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      refreshAladinLayout();
      window.requestAnimationFrame(refreshAladinLayout);
    });
    // The file panel has an animated width transition. Refresh once during and
    // once after it so WebGL never keeps the initial hidden/zero-sized canvas.
    aladinRefreshTimers = [120, 360].map(delay => window.setTimeout(refreshAladinLayout, delay));
  });
}
async function toggleSkyContext() {
  mapContext.value = false;
  skyContext.value = !skyContext.value;
  if (!skyContext.value) return;
  await nextTick();
  if (aladinInstance) {
    scheduleAladinLayoutRefresh();
    return;
  }
  if (!aladinRef.value) return;
  const corners = renderResult.value?.wcs?.corners || [];
  const centerRa = corners.length ? corners.reduce((sum, item) => sum + item[0], 0) / corners.length : 0;
  const centerDec = corners.length ? corners.reduce((sum, item) => sum + item[1], 0) / corners.length : 0;
  const span = corners.length ? Math.max(0.05, ...corners.map(item => Math.hypot(item[0] - centerRa, item[1] - centerDec))) * 2.5 : 10;
  try {
    const module = await import('aladin-lite');
    const A = module.default;
    await A.init;
    aladinInstance = A.aladin(aladinRef.value, { target: `${centerRa} ${centerDec}`, fov: Math.min(180, span), survey: 'P/DSS2/color', cooFrame: 'equatorial', showCooGrid: true, showCooGridControl: true, showFullscreenControl: true, showProjectionControl: true });
    aladinResizeObserver?.disconnect();
    aladinResizeObserver = new ResizeObserver(scheduleAladinLayoutRefresh);
    aladinResizeObserver.observe(aladinRef.value);
    if (corners.length) {
      const overlay = A.graphicOverlay({ color: '#22d3ee', lineWidth: 2, name: 'FITS footprint' });
      aladinInstance.addOverlay(overlay);
      overlay.add(A.polygon([...corners, corners[0]]));
    }
    scheduleAladinLayoutRefresh();
  } catch (error) {
    console.error('Unable to initialize Aladin Lite:', error);
    skyContext.value = false;
  }
}
async function toggleMapContext() {
  skyContext.value = false;
  mapContext.value = !mapContext.value;
  if (!mapContext.value || mapInstance || !mapRef.value || !imageUrl.value) return;
  await nextTick();
  const bounds = preparation.value?.geospatial?.bounds_wgs84;
  if (!Array.isArray(bounds) || bounds.length !== 4) { mapContext.value = false; return; }
  try {
    const [{ default: Map }, { default: View }, { default: TileLayer }, { default: ImageLayer }, { default: OSM }, { default: ImageStatic }, projection, extentUtils] = await Promise.all([
      import('ol/Map.js'), import('ol/View.js'), import('ol/layer/Tile.js'), import('ol/layer/Image.js'), import('ol/source/OSM.js'), import('ol/source/ImageStatic.js'), import('ol/proj.js'), import('ol/extent.js'),
    ]);
    const extent = projection.transformExtent(bounds as [number, number, number, number], 'EPSG:4326', 'EPSG:3857');
    mapInstance = new Map({
      target: mapRef.value,
      layers: [new TileLayer({ source: new OSM() }), new ImageLayer({ opacity: 0.78, source: new ImageStatic({ url: imageUrl.value, imageExtent: extent, projection: 'EPSG:3857' }) })],
      view: new View({ center: extentUtils.getCenter(extent), zoom: 2 }),
    });
    mapInstance.getView().fit(extent, { padding: [40, 40, 40, 40], maxZoom: 18 });
  } catch (error) { console.error('Unable to initialize GeoTIFF map:', error); mapContext.value = false; }
}
function zoomBy(factor: number) { zoom.value = Math.min(20, Math.max(0.1, zoom.value * factor)); }
async function toggleSources() {
  if (detectedSources.value.length) { detectedSources.value = []; selectedSource.value = null; return; }
  try { detectedSources.value = (await detectAstronomySources(selection(), 5)).sources; } catch { detectedSources.value = []; }
}
function onWheel(event: WheelEvent) {
  if (skyContext.value || mapContext.value) return;
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.18 : 0.85);
}
function normalizedPoint(event: PointerEvent) {
  const image = imageRef.value;
  if (!image) return null;
  const rect = image.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
}
function onPointerDown(event: PointerEvent) {
  if (skyContext.value || mapContext.value) return;
  const point = normalizedPoint(event);
  if (regionMode.value && point) { selectionStart.value = point; selectionRect.value = { x0: point.x, y0: point.y, x1: point.x, y1: point.y }; return; }
  dragStart.value = { x: event.clientX, y: event.clientY, panX: panX.value, panY: panY.value };
}
function onPointerMove(event: PointerEvent) {
  if (skyContext.value || mapContext.value) return;
  const point = normalizedPoint(event);
  crosshair.value = point;
  if (selectionStart.value && point) selectionRect.value = { x0: selectionStart.value.x, y0: selectionStart.value.y, x1: point.x, y1: point.y };
  else if (dragStart.value) { panX.value = dragStart.value.panX + event.clientX - dragStart.value.x; panY.value = dragStart.value.panY + event.clientY - dragStart.value.y; }
}
async function onPointerUp(event: PointerEvent) {
  if (skyContext.value || mapContext.value) return;
  const point = normalizedPoint(event);
  dragStart.value = null;
  if (!renderResult.value || !point) { selectionStart.value = null; return; }
  const width = renderResult.value.source_width, height = renderResult.value.source_height;
  if (selectionStart.value && selectionRect.value) {
    const rect = selectionRect.value; selectionStart.value = null;
    try { regionInfo.value = await inspectAstronomyRegion(selection(), Math.floor(rect.x0 * width), Math.floor(rect.y0 * height), Math.ceil(rect.x1 * width), Math.ceil(rect.y1 * height)); } catch { regionInfo.value = null; }
    return;
  }
  try { pixelInfo.value = await inspectAstronomyPixel(selection(), Math.min(width - 1, Math.floor(point.x * width)), Math.min(height - 1, Math.floor(point.y * height))); } catch { pixelInfo.value = null; }
}

watch(() => props.file.file_id, () => loadFile(props.file), { immediate: true });
watch(datasetIndex, () => { sliceIndices.value = leadingAxes.value.map(() => 0); band.value = 1; if (activeDataset.value?.kind === 'image') void render(); else { renderResult.value = null; status.value = ''; } });
watch([sliceIndices, band, stretch, interval, colourMap, invert], () => { if (preparation.value && interval.value !== 'manual') void render(); }, { deep: true });
onBeforeUnmount(() => {
  clearAladinRefreshTimers();
  aladinResizeObserver?.disconnect();
  mapInstance?.setTarget(undefined);
  if (preparation.value) void releaseAstronomyPreview(props.file.file_id, preparation.value.preview_id).catch(() => undefined);
});
</script>

<style src="ol/ol.css"></style>

<style scoped>
.control { border: 1px solid rgb(255 255 255 / .16); border-radius: .35rem; background: #242b36; padding: .25rem .45rem; color: #e2e8f0; }
.button { border: 1px solid rgb(255 255 255 / .16); border-radius: .35rem; background: #242b36; padding: .3rem .55rem; }
.button:hover,.button.active { background: #155e75; border-color: #22d3ee; }
.tool { width: 2rem; height: 2rem; border-right: 1px solid rgb(255 255 255 / .15); }
.tool:last-child { border-right: 0; }
.panel { margin-bottom: .75rem; border: 1px solid rgb(255 255 255 / .1); border-radius: .5rem; background: #1d2430; padding: .65rem; }
.panel h3 { font-weight: 600; color: #f1f5f9; }
.aladin-viewport { width: 100%; height: 100%; min-width: 1px; min-height: 1px; contain: layout size; }
@media (max-width: 900px) {
  .preview-layout { grid-template-columns: minmax(0, 1fr) minmax(200px, 240px); }
}
</style>
