<template>
  <section class="matrix-preview">
    <header>
      <strong>高维矩阵</strong>
      <select v-model="variable" aria-label="数组变量" @change="selectVariable">
        <option v-for="item in preparation?.arrays" :key="item.name" :value="item.name">{{ item.name }} · {{ item.shape.join(' × ') || '标量' }}</option>
      </select>
      <template v-if="(active?.shape.length || 0) >= 2">
        <label>行轴 <select v-model.number="axes[0]" @change="scheduleRender"><option v-for="(_, i) in active?.shape" :key="i" :value="i">{{ i }}</option></select></label>
        <label>列轴 <select v-model.number="axes[1]" @change="scheduleRender"><option v-for="(_, i) in active?.shape" :key="i" :value="i">{{ i }}</option></select></label>
      </template>
      <select v-model="component" aria-label="复数分量" @change="scheduleRender"><option value="real">实部</option><option value="imaginary">虚部</option><option value="magnitude">幅值</option><option value="phase">相位（弧度）</option></select>
      <select v-model="mode" aria-label="显示方式"><option value="heatmap">热力图</option><option value="table">数值表格</option></select>
      <select v-model="palette" aria-label="色带"><option value="blue">蓝红色带</option><option value="gray">灰度</option></select>
      <label><input v-model="logScale" type="checkbox" />对称对数</label>
      <button @click="zoom = Math.min(8, zoom * 1.5)">放大</button><button @click="zoom = Math.max(1, zoom / 1.5)">缩小</button><button @click="zoom = 1">适应窗口</button>
      <button :disabled="busy" @click="render">刷新</button>
    </header>
    <div v-if="active" class="slices">
      <span>{{ active.dtype }} · {{ active.sparse ? '稀疏矩阵' : `${active.shape.length} 维数组` }} · 索引从 0 开始</span>
      <label v-for="axis in fixedAxes" :key="axis">轴 {{ axis }}
        <input v-model.number="indices[axis]" type="range" min="0" :max="active.shape[axis] - 1" @input="scheduleRender" />
        <input v-model.number="indices[axis]" type="number" min="0" :max="active.shape[axis] - 1" @change="scheduleRender" />
      </label>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="busy" role="status">正在读取矩阵切片…</p>
    <div v-if="plane" class="viewport">
      <div v-if="mode === 'heatmap'" class="heatmap" :style="{ width: `${zoom * 100}%` }">
        <canvas ref="canvas" @mousemove="hover" @mouseleave="hoverText = ''" aria-label="矩阵切片热力图，移动指针查看坐标和值" />
      </div>
      <table v-else>
        <thead><tr><th>行 / 列</th><th v-for="column in plane.columns" :key="column">{{ column }}</th></tr></thead>
        <tbody><tr v-for="(row, i) in plane.values" :key="i"><th>{{ plane.rows[i] }}</th><td v-for="(value, j) in row" :key="j">{{ format(value) }}</td></tr></tbody>
      </table>
    </div>
    <footer v-if="plane">
      <div class="legend" :style="{ background: palette === 'gray' ? 'linear-gradient(to right,#000,#fff)' : 'linear-gradient(to right,hsl(240,75%,48%),hsl(120,75%,48%),hsl(0,75%,48%))' }" />
      <span>最小 {{ format(plane.minimum) }} · 最大 {{ format(plane.maximum) }} · 均值 {{ format(plane.mean) }} · 非有限值 {{ plane.non_finite }}</span>
      <span>{{ hoverText || '移动指针查看原始索引与所选分量数值；放大后可滚动浏览。' }}</span>
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
let generation = 0; let renderGeneration = 0; let ownerFile = ''; let timer: ReturnType<typeof setTimeout> | undefined;
function format(value: number | null) { return value == null ? '—' : Number(value.toPrecision(7)).toString(); }
function release() {
  if (preparation.value) void releaseMatrixPreview(ownerFile, preparation.value.preview_id).catch(() => undefined);
  preparation.value = null;
}
function selectVariable() {
  indices.value = active.value?.shape.map(() => 0) || [];
  const count = active.value?.shape.length || 0;
  axes.value = count >= 2 ? [count - 2, count - 1] : [0, 1];
  plane.value = null; zoom.value = 1; scheduleRender();
}
function scheduleRender() { clearTimeout(timer); renderGeneration++; plane.value = null; timer = setTimeout(render, 200); }
async function render() {
  if (!preparation.value) return;
  const version = ++renderGeneration;
  busy.value = true; error.value = ''; hoverText.value = '';
  try {
    const result = await renderMatrixPreview(ownerFile, preparation.value.preview_id, variable.value, [...axes.value], [...indices.value], component.value);
    if (version !== renderGeneration) return;
    plane.value = result;
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
  const version = ++generation; renderGeneration++; clearTimeout(timer); release();
  ownerFile = fileId; plane.value = null; error.value = ''; busy.value = true;
  try {
    const result = await prepareMatrixPreview(fileId);
    if (version !== generation) { void releaseMatrixPreview(fileId, result.preview_id).catch(() => undefined); return; }
    preparation.value = result; variable.value = result.arrays[0].name; selectVariable();
  } catch (err: any) { if (version === generation) { error.value = err?.response?.data?.detail || '文件无法预览'; busy.value = false; } }
}, { immediate: true });
onBeforeUnmount(() => { generation++; renderGeneration++; clearTimeout(timer); release(); });
</script>

<style scoped>
.matrix-preview { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; height:100%; background:#111827; color:#e5e7eb; font-size:12px; }
header,.slices { display:flex; flex-wrap:wrap; gap:8px; padding:10px; align-items:center; flex-shrink:0; border-bottom:1px solid #374151; }
label { display:flex; align-items:center; gap:4px; } select,button,input[type=number] { background:#1f2937; color:#f3f4f6; border:1px solid #4b5563; border-radius:4px; padding:4px; max-width:230px; } button:disabled { opacity:.5; }
input[type=number] { width:70px; } input[type=range] { width:100px; }
.viewport { flex:1; min-height:100px; min-width:0; overflow:auto; padding:12px; }
.heatmap { min-width:100%; } canvas { display:block; width:100%; min-height:100px; image-rendering:pixelated; }
table { border-collapse:collapse; font-variant-numeric:tabular-nums; } td,th { padding:5px 8px; border:1px solid #374151; white-space:nowrap; } th { background:#1f2937; position:sticky; top:0; }
footer { flex-shrink:0; display:flex; flex-direction:column; gap:4px; padding:10px; border-top:1px solid #374151; overflow-wrap:anywhere; } .legend { height:8px; width:160px; } p { padding:8px; } .error { color:#fca5a5; }
</style>
