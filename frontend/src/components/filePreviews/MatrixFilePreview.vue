<template>
  <section class="matrix-preview">
    <header>
      <strong>高维矩阵</strong>
      <select v-model="variable" aria-label="数组变量" @change="selectVariable">
        <option v-for="item in preparation?.arrays" :key="item.name" :value="item.name">{{ item.name }} · {{ item.shape.join(' × ') || '标量' }}</option>
      </select>
      <template v-if="(active?.shape.length || 0) >= 2">
        <label>行轴 <select v-model.number="axes[0]" @change="resetRegion"><option v-for="(_, i) in active?.shape" :key="i" :value="i">{{ i }}</option></select></label>
        <label>列轴 <select v-model.number="axes[1]" @change="resetRegion"><option v-for="(_, i) in active?.shape" :key="i" :value="i">{{ i }}</option></select></label>
      </template>
      <select v-model="component" aria-label="复数分量" @change="scheduleRender"><option value="real">实部</option><option value="imaginary">虚部</option><option value="magnitude">幅值</option><option value="phase">相位（弧度）</option></select>
      <select v-model="mode" aria-label="显示方式"><option value="heatmap">热力图</option><option value="table">数值表格</option></select>
      <select v-model="palette" aria-label="色带"><option value="blue">蓝红色带</option><option value="gray">灰度</option></select>
      <label><input v-model="logScale" type="checkbox" />对称对数</label>
      <label><input v-model="structure" type="checkbox" @change="scheduleRender" />非零结构</label>
      <button @click="resetRegion">恢复完整区域</button>
      <button @click="zoom = Math.min(8, zoom * 1.5)">放大</button><button @click="zoom = Math.max(1, zoom / 1.5)">缩小</button><button @click="zoom = 1">适应窗口</button>
      <button :disabled="busy" @click="render">刷新</button>
    </header>
    <div v-if="active" class="slices">
      <span>{{ active.dtype }} · {{ active.sparse ? '稀疏矩阵' : `${active.shape.length} 维数组` }} · 索引从 0 开始</span>
      <label v-for="axis in fixedAxes" :key="axis">轴 {{ axis }}
        <input v-model.number="indices[axis]" type="range" min="0" :max="active.shape[axis] - 1" @input="scheduleRender" />
        <input v-model.number="indices[axis]" type="number" min="0" :max="active.shape[axis] - 1" @change="scheduleRender" />
      </label>
      <template v-if="fixedAxes.length">
        <button @click="togglePlay">{{ playing ? '暂停切片' : '播放切片' }}</button>
        <select v-model.number="playDelay" aria-label="播放间隔"><option :value="500">0.5 秒</option><option :value="1000">1 秒</option><option :value="2000">2 秒</option></select>
      </template>
      <template v-if="active.shape.length === 3">
        <button v-for="pair in [[0, 1], [0, 2], [1, 2]]" :key="pair.join()" @click="axes = pair; resetRegion()">切面 {{ pair.join(' / ') }}</button>
      </template>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="busy" role="status">正在读取矩阵切片…</p>
    <div v-if="plane" class="viewport">
      <div v-if="orthogonalPlanes.length" class="profiles">
        <figure v-for="slice in orthogonalPlanes" :key="slice.axes.join()">
          <figcaption>联动切面 {{ slice.axes.join(' / ') }}（点击定位）</figcaption>
          <svg viewBox="0 0 128 128" role="img" aria-label="张量正交切面" @click="locateSlice($event, slice)">
            <template v-for="(row, y) in slice.plane.values" :key="y">
              <rect v-for="(value, x) in row" :key="x" :x="Number(x) * 128 / row.length" :y="y * 128 / slice.plane.values.length" :width="128 / row.length + .1" :height="128 / slice.plane.values.length + .1" :fill="cellColor(value, slice.plane)" />
            </template>
          </svg>
        </figure>
      </div>
      <div v-if="mode === 'heatmap'" class="heatmap" :style="{ width: `${zoom * 100}%` }">
        <canvas ref="canvas" @mousemove="hover" @pointerdown="startSelection" @pointerup="finishSelection" @pointercancel="selectionStart = null" @mouseleave="hoverText = ''" aria-label="矩阵切片热力图，拖动框选原始区域，移动指针查看坐标和值" />
      </div>
      <table v-else>
        <thead><tr><th>行 / 列</th><th v-for="column in plane.columns" :key="column">{{ column }}</th></tr></thead>
        <tbody><tr v-for="(row, i) in plane.values" :key="i"><th>{{ plane.rows[i] }}</th><td v-for="(value, j) in row" :key="j">{{ format(value) }}</td></tr></tbody>
      </table>
      <div class="profiles">
        <figure v-for="profile in [{ title: '行均值剖面', values: plane.row_profile }, { title: '列均值剖面', values: plane.column_profile }]" :key="profile.title">
          <figcaption>{{ profile.title }}</figcaption>
          <svg viewBox="0 0 400 100" role="img" :aria-label="profile.title"><polyline :points="profilePoints(profile.values)" fill="none" stroke="#38bdf8" stroke-width="2" /></svg>
        </figure>
      </div>
      <div v-if="preparation?.curves?.length" class="profiles">
        <figure v-for="curve in preparation.curves" :key="curve.title">
          <figcaption>{{ curve.title }}</figcaption>
          <svg viewBox="0 0 400 100" role="img" :aria-label="curve.title">
            <template v-if="curve.kind === 'scatter'">
              <circle v-for="(point, i) in scatterPoints(curve.x, curve.y)" :key="i" :cx="point[0]" :cy="point[1]" r="3" fill="#38bdf8"><title>{{ curve.x[i] }} + {{ curve.y[i] }}i</title></circle>
            </template>
            <polyline v-else :points="profilePoints(curve.y)" fill="none" stroke="#38bdf8" stroke-width="2" />
          </svg>
        </figure>
      </div>
    </div>
    <footer v-if="plane">
      <div class="legend" :style="{ background: palette === 'gray' ? 'linear-gradient(to right,#000,#fff)' : 'linear-gradient(to right,hsl(240,75%,48%),hsl(120,75%,48%),hsl(0,75%,48%))' }" />
      <span>最小 {{ format(plane.minimum) }} · 最大 {{ format(plane.maximum) }} · 均值 {{ format(plane.mean) }} · 标准差 {{ format(plane.standard_deviation) }} · 有效点 {{ plane.finite_count }}/{{ plane.count }}</span>
      <span>{{ hoverText || '拖动框选区域后重新读取原始数据；移动指针查看索引和值。' }}</span>
      <span>行 [{{ plane.row_range.join(', ') }}) · 列 [{{ plane.column_range.join(', ') }}) · 抽样步长 {{ plane.row_step }} × {{ plane.column_step }}</span>
      <span>{{ plane.sampled ? '等步长抽样预览，每轴最多 256 点；统计仅基于显示样本，可能遗漏局部极值。' : '显示当前完整切片，统计仅针对当前切片。' }} 灰色表示非有限值。</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { prepareMatrixPreview, renderMatrixPreview, releaseMatrixPreview, type FileInfo, type MatrixPreparation, type MatrixPlane } from '@/api/file';
const props = defineProps<{ file: FileInfo }>();
const preparation = ref<MatrixPreparation | null>(null);
const plane = ref<MatrixPlane | null>(null);
const variable = ref('');
const active = computed(() => preparation.value?.arrays.find(item => item.name === variable.value));
const axes = ref([0, 1]);
const indices = ref<number[]>([]);
const fixedAxes = computed(() => active.value?.shape.map((_, i) => i).filter(i => active.value!.shape.length > 1 && !axes.value.includes(i)) || []);
const component = ref('real'); const mode = ref('heatmap'); const palette = ref('blue'); const logScale = ref(false);
const zoom = ref(1); const busy = ref(false); const error = ref(''); const hoverText = ref(''); const canvas = ref<HTMLCanvasElement>();
const structure = ref(false); const rowRange = ref<number[]>(); const columnRange = ref<number[]>();
const playing = ref(false); const playDelay = ref(1000);
const selectionStart = ref<{ x: number; y: number } | null>(null);
const orthogonalPlanes = ref<{axes: number[]; plane: MatrixPlane}[]>([]);
let playTimer: ReturnType<typeof setTimeout> | undefined;
let generation = 0; let renderGeneration = 0; let ownerFile = ''; let timer: ReturnType<typeof setTimeout> | undefined;
function format(value: number | null) { return value == null ? '—' : Number(value.toPrecision(7)).toString(); }
function release() {
  if (preparation.value) void releaseMatrixPreview(ownerFile, preparation.value.preview_id).catch(() => undefined);
  preparation.value = null;
}
function selectVariable() {
  stopPlay(); rowRange.value = undefined; columnRange.value = undefined; orthogonalPlanes.value = [];
  indices.value = active.value?.shape.map(() => 0) || [];
  const count = active.value?.shape.length || 0;
  axes.value = count >= 2 ? [count - 2, count - 1] : [0, 1];
  plane.value = null; zoom.value = 1; scheduleRender();
}
function resetRegion() { rowRange.value = undefined; columnRange.value = undefined; zoom.value = 1; scheduleRender(); }
function stopPlay() { playing.value = false; clearTimeout(playTimer); }
function togglePlay() {
  if (playing.value) { stopPlay(); return; }
  playing.value = true; playNext();
}
function playNext() {
  clearTimeout(playTimer);
  if (!playing.value || !active.value || !fixedAxes.value.length) return;
  playTimer = setTimeout(async () => {
    if (!playing.value || !active.value) return;
    if (!busy.value) {
      const axis = fixedAxes.value[0];
      indices.value[axis] = (indices.value[axis] + 1) % active.value.shape[axis];
      await render();
    }
    playNext();
  }, playDelay.value);
}
function profilePoints(values: (number | null)[]) {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (!finite.length) return '';
  const low = Math.min(...finite), span = Math.max(...finite) - low || 1;
  return values.map((v, i) => v === null ? '' : `${10 + i * 380 / Math.max(1, values.length - 1)},${90 - (v - low) / span * 80}`).filter(Boolean).join(' ');
}
function scatterPoints(x: number[], y: number[]) {
  const xmin = Math.min(...x), xmax = Math.max(...x), ymin = Math.min(...y), ymax = Math.max(...y);
  return x.map((v, i) => [10 + (v - xmin) / (xmax - xmin || 1) * 380, 90 - (y[i] - ymin) / (ymax - ymin || 1) * 80]);
}
function cellColor(value: number | null, p: MatrixPlane) {
  if (value === null) return '#71717a';
  const fraction = ((value - (p.minimum ?? 0)) / ((p.maximum ?? 1) - (p.minimum ?? 0) || 1));
  return `hsl(${240 * (1 - fraction)} 75% 48%)`;
}
function locateSlice(event: MouseEvent, slice: {axes: number[]; plane: MatrixPlane}) {
  const rect = (event.currentTarget as SVGElement).getBoundingClientRect();
  const x = Math.min(slice.plane.columns.length - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * slice.plane.columns.length)));
  const y = Math.min(slice.plane.rows.length - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * slice.plane.rows.length)));
  indices.value[slice.axes[0]] = slice.plane.rows[y]; indices.value[slice.axes[1]] = slice.plane.columns[x];
  scheduleRender();
}
function canvasPoint(event: PointerEvent) {
  if (!canvas.value || !plane.value) return null;
  const rect = canvas.value.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(plane.value.columns.length - 1, Math.floor((event.clientX - rect.left) / rect.width * plane.value.columns.length))),
    y: Math.max(0, Math.min(plane.value.rows.length - 1, Math.floor((event.clientY - rect.top) / rect.height * plane.value.rows.length))),
  };
}
function startSelection(event: PointerEvent) { if (event.button !== 0) return; selectionStart.value = canvasPoint(event); canvas.value?.setPointerCapture(event.pointerId); }
function finishSelection(event: PointerEvent) {
  const start = selectionStart.value, end = canvasPoint(event), p = plane.value;
  selectionStart.value = null;
  if (!start || !end || !p || (start.x === end.x && start.y === end.y)) return;
  stopPlay();
  rowRange.value = [p.rows[Math.min(start.y, end.y)], Math.min(p.row_range[1], p.rows[Math.max(start.y, end.y)] + p.row_step)];
  columnRange.value = [p.columns[Math.min(start.x, end.x)], Math.min(p.column_range[1], p.columns[Math.max(start.x, end.x)] + p.column_step)];
  scheduleRender();
}
function scheduleRender() { clearTimeout(timer); renderGeneration++; plane.value = null; timer = setTimeout(render, 200); }
async function render() {
  if (!preparation.value) return;
  const version = ++renderGeneration;
  busy.value = true; error.value = ''; hoverText.value = '';
  try {
    const result = await renderMatrixPreview(ownerFile, preparation.value.preview_id, variable.value, [...axes.value], [...indices.value], component.value, { rowRange: rowRange.value, columnRange: columnRange.value, structure: structure.value });
    if (version !== renderGeneration) return;
    plane.value = result;
    if (active.value?.shape.length === 3) {
      const slices = await Promise.all([[0, 1], [0, 2], [1, 2]].map(async pair => ({
        axes: pair, plane: await renderMatrixPreview(ownerFile, preparation.value!.preview_id, variable.value, pair, [...indices.value], component.value, {maxPoints: 32, structure: structure.value}),
      })));
      if (version !== renderGeneration) return;
      orthogonalPlanes.value = slices;
    }
    await nextTick(); paint();
  } catch (err: any) { if (version === renderGeneration) { plane.value = null; error.value = err?.response?.data?.detail || '矩阵预览失败，请重试'; } }
  finally { if (version === renderGeneration) busy.value = false; }
}
function paint() {
  if (!canvas.value || !plane.value) return;
  const p = plane.value; const c = canvas.value;
  c.width = p.columns.length; c.height = p.rows.length;
  const ctx = c.getContext('2d'); if (!ctx) return;
  const transform = (v: number) => logScale.value ? Math.sign(v) * Math.log1p(Math.abs(v)) : v;
  const low = transform(p.minimum ?? 0), high = transform(p.maximum ?? 1);
  p.values.forEach((row, y) => row.forEach((value, x) => {
    const fraction = value == null ? 0 : high === low ? 0.5 : (transform(value) - low) / (high - low);
    ctx.fillStyle = value == null ? '#71717a' : palette.value === 'gray' ? `hsl(0 0% ${fraction * 100}%)` : `hsl(${240 * (1 - fraction)} 75% 48%)`;
    ctx.fillRect(x, y, 1, 1);
  }));
}
function hover(event: MouseEvent) {
  if (!canvas.value || !plane.value) return;
  const rect = canvas.value.getBoundingClientRect(); const p = plane.value;
  const x = Math.min(p.columns.length - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * p.columns.length)));
  const y = Math.min(p.rows.length - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * p.rows.length)));
  const point = [...indices.value];
  if (point.length >= 2) { point[axes.value[0]] = p.rows[y]; point[axes.value[1]] = p.columns[x]; }
  else if (point.length) point[0] = p.columns[x];
  hoverText.value = `[${point.join(', ')}] = ${format(p.values[y][x])}`;
}
watch([mode, palette, logScale], async () => { await nextTick(); paint(); });
watch(() => props.file.file_id, async fileId => {
  const version = ++generation; renderGeneration++; clearTimeout(timer); stopPlay(); release();
  ownerFile = fileId; plane.value = null; orthogonalPlanes.value = []; error.value = ''; busy.value = true;
  try {
    const result = await prepareMatrixPreview(fileId);
    if (version !== generation) { void releaseMatrixPreview(fileId, result.preview_id).catch(() => undefined); return; }
    preparation.value = result; variable.value = result.arrays[0].name; selectVariable();
  } catch (err: any) { if (version === generation) { error.value = err?.response?.data?.detail || '文件无法预览'; busy.value = false; } }
}, { immediate: true });
onBeforeUnmount(() => { generation++; renderGeneration++; clearTimeout(timer); stopPlay(); release(); });
</script>

<style scoped>
.matrix-preview { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; height:100%; background:#111827; color:#e5e7eb; font-size:12px; }
header,.slices { display:flex; flex-wrap:wrap; gap:8px; padding:10px; align-items:center; flex-shrink:0; border-bottom:1px solid #374151; }
label { display:flex; align-items:center; gap:4px; } select,button,input[type=number] { background:#1f2937; color:#f3f4f6; border:1px solid #4b5563; border-radius:4px; padding:4px; max-width:230px; } button:disabled { opacity:.5; }
input[type=number] { width:70px; } input[type=range] { width:100px; }
.viewport { flex:1; min-height:100px; min-width:0; overflow:auto; padding:12px; }
.heatmap { min-width:100%; } canvas { display:block; width:100%; min-height:100px; image-rendering:pixelated; touch-action:none; cursor:crosshair; }
.profiles { display:flex; flex-wrap:wrap; gap:12px; } figure { flex:1 1 220px; min-width:0; margin:12px 0; } figure svg { width:100%; max-height:120px; }
table { border-collapse:collapse; font-variant-numeric:tabular-nums; } td,th { padding:5px 8px; border:1px solid #374151; white-space:nowrap; } th { background:#1f2937; position:sticky; top:0; }
footer { flex-shrink:0; display:flex; flex-direction:column; gap:4px; padding:10px; border-top:1px solid #374151; overflow-wrap:anywhere; } .legend { height:8px; width:160px; } p { padding:8px; } .error { color:#fca5a5; }
</style>
