<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white text-slate-700">
    <div class="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3 text-xs">
      <span class="font-semibold text-slate-800">{{ formatLabel }} 文件预览</span>
      <span>记录 {{ rows.length.toLocaleString() }} 条</span>
      <span v-if="columns.length">字段 {{ columns.length }} 个</span>
      <button type="button" class="ml-auto rounded border border-slate-300 px-3 py-1.5 hover:bg-slate-50" @click="copySource">复制内容</button>
    </div>
    <div v-if="status" class="flex flex-1 items-center justify-center p-6 text-sm text-slate-500">{{ status }}</div>
    <div v-else class="min-h-0 flex-1 overflow-auto p-4">
      <div v-if="commentLines.length" class="mb-3 rounded bg-slate-50 p-3 text-xs text-slate-500">
        <div v-for="line in commentLines.slice(0, 8)" :key="line" class="truncate">{{ line }}</div>
      </div>
      <table v-if="rows.length" class="w-full min-w-[720px] border-collapse text-left text-xs">
        <thead><tr class="border-b border-slate-200 bg-slate-50">
          <th v-for="column in columns" :key="column" class="sticky top-0 px-2 py-2 font-semibold text-slate-600">{{ column }}</th>
        </tr></thead>
        <tbody><tr v-for="(row, index) in rows" :key="index" class="border-b border-slate-100 hover:bg-slate-50">
          <td v-for="column in columns" :key="column" class="max-w-[360px] whitespace-nowrap px-2 py-1.5 align-top">{{ row[column] }}</td>
        </tr></tbody>
      </table>
      <pre v-else class="whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs">{{ source.slice(0, 200000) }}</pre>
      <div class="mt-3 text-xs text-slate-400">{{ source.length.toLocaleString() }} 字符；最多展示前 {{ rows.length.toLocaleString() }} 条记录。</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';
import { copyToClipboard } from '../../utils/dom';

const props = defineProps<{ file: FileInfo }>();
const source = ref(''); const status = ref('正在加载文件...');
const extension = computed(() => String(props.file.filename || '').toLowerCase().split('.').pop() || '');
const formatLabel = computed(() => ({ vcf: 'VCF 变异', gff: 'GFF 注释', gff3: 'GFF3 注释', gtf: 'GTF 注释', bed: 'BED 区间', sam: 'SAM 比对', wig: 'WIG 信号', bedgraph: 'BedGraph 信号' }[extension.value] || extension.value.toUpperCase()));
const commentPrefix = computed(() => extension.value === 'vcf' ? '#' : extension.value === 'sam' ? '@' : '#');
const commentLines = computed(() => source.value.split(/\r?\n/).filter(line => line.startsWith(commentPrefix.value)));
const columns = computed(() => {
  if (extension.value === 'vcf') return ['CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER', 'INFO'];
  if (['gff', 'gff3', 'gtf'].includes(extension.value)) return ['seqid', 'source', 'type', 'start', 'end', 'score', 'strand', 'phase', 'attributes'];
  if (['bed', 'bedgraph'].includes(extension.value)) return extension.value === 'bedgraph' ? ['chrom', 'start', 'end', 'value'] : ['chrom', 'start', 'end', 'name', 'score', 'strand'];
  if (extension.value === 'sam') return ['QNAME', 'FLAG', 'RNAME', 'POS', 'MAPQ', 'CIGAR', 'RNEXT', 'PNEXT', 'TLEN', 'SEQ', 'QUAL'];
  return [];
});
const rows = computed(() => {
  const out: Record<string, string>[] = [];
  for (const line of source.value.split(/\r?\n/)) {
    if (!line.trim() || line.startsWith(commentPrefix.value)) continue;
    const values = line.split(/\t|\s+/);
    if (!columns.value.length) continue;
    const row: Record<string, string> = {}; columns.value.forEach((key, i) => { row[key] = values[i] ?? ''; }); out.push(row);
    if (out.length >= 500) break;
  }
  return out;
});
const copySource = async () => { if (source.value) await copyToClipboard(source.value); };
const load = async () => { status.value = '正在加载文件...'; source.value = ''; try { const response = await fetch(await getFileDownloadUrl(props.file)); if (!response.ok) throw new Error(String(response.status)); source.value = await response.text(); status.value = source.value ? '' : '文件为空'; } catch (error) { console.error(error); status.value = '文件预览失败，请确认文件可访问且为文本格式'; } };
watch(() => props.file, load, { immediate: true });
</script>
