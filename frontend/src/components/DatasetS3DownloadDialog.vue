<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4" @click.self="$emit('close')" @keydown.esc="$emit('close')">
      <section role="dialog" aria-modal="true" aria-labelledby="s3-download-title" class="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl bg-[var(--background-white-main)] p-5 text-[var(--text-primary)] shadow-xl">
        <div class="mb-4 flex items-center justify-between"><h2 id="s3-download-title" class="font-semibold">下载数据文件</h2><button aria-label="关闭" class="rounded p-1 hover:bg-black/5" @click="$emit('close')"><X class="size-5" /></button></div>
        <p class="break-all text-sm">{{ path }}</p>
        <div v-if="loading" class="my-8 flex items-center justify-center gap-2 text-sm"><LoaderCircle class="size-4 animate-spin" />正在准备下载地址</div>
        <div v-else-if="error" class="my-4 text-sm text-red-600">{{ error }} <button class="underline" @click="load">重试</button></div>
        <template v-else-if="info">
          <p class="my-3 text-xs text-[var(--text-secondary)]">{{ info.size.toLocaleString() }} 字节 · {{ expired ? '下载凭证已过期，请重新生成' : `凭证有效至 ${new Date(info.expires_at * 1000).toLocaleTimeString()}` }}</p>
          <div v-for="field in fields" :key="field.label" class="my-3">
            <label class="mb-1 block text-xs text-[var(--text-secondary)]">{{ field.label }}</label>
            <div class="flex items-start gap-2 rounded-md border border-[var(--border-main)] p-2"><code class="min-w-0 flex-1 break-all text-xs">{{ field.value }}</code><button :aria-label="`复制${field.label}`" title="复制" class="shrink-0" @click="copy(field.value)"><Copy class="size-4" /></button></div>
          </div>
          <details class="my-4 text-xs">
            <summary class="cursor-pointer">使用 S3 客户端下载</summary>
            <p class="my-2 leading-5">使用下列临时凭证；区域为 {{ info.region }}，寻址方式选择 Path Style。凭证仅能读取此文件，过期后可重新生成。</p>
            <div class="my-2 break-all">Access Key：<code>{{ info.access_key_id }}</code><button class="ml-2 underline" @click="copy(info.access_key_id)">复制</button></div>
            <div class="my-2">Secret Key：<code>••••••••••••</code><button class="ml-2 underline" @click="copy(info.secret_access_key)">复制</button></div>
            <p class="my-2">将凭证配置到名为 dataseek 的 AWS CLI profile 后运行：</p>
            <pre class="whitespace-pre-wrap break-all rounded bg-[var(--background-gray-main)] p-2">{{ command }}</pre>
            <button class="mt-2 underline" @click="copy(command)">复制下载命令</button>
          </details>
          <div class="mt-5 flex justify-end gap-2"><button class="rounded border border-[var(--border-main)] px-3 py-2 text-sm" @click="load">重新生成</button><a v-if="!expired" :href="info.download_url" rel="noreferrer" class="rounded bg-[#225f48] px-3 py-2 text-sm text-white">下载文件</a></div>
        </template>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { Copy, LoaderCircle, X } from 'lucide-vue-next';
import { createDatasetS3Download, type DatasetS3Download } from '@/api/dataset';
import { copyToClipboard } from '@/utils/dom';
import { showErrorToast, showSuccessToast } from '@/utils/toast';

const props = defineProps<{ datasetId: string; path: string }>();
defineEmits<{ close: [] }>();
const info = ref<DatasetS3Download>();
const loading = ref(false);
const error = ref('');
const now = ref(Date.now());
const expired = computed(() => !!info.value && now.value >= info.value.expires_at * 1000);
const fields = computed(() => info.value ? [{ label: 'S3 路径', value: info.value.s3_uri }, { label: 'S3 Endpoint', value: info.value.endpoint }] : []);
const shellQuote = (value: string) => "'" + value.replace(/'/g, "'\\''") + "'";
const command = computed(() => info.value ? `aws --profile dataseek --endpoint-url ${shellQuote(info.value.endpoint)} --region ${shellQuote(info.value.region)} s3 cp ${shellQuote(info.value.s3_uri)} .` : '');
let disposed = false;
let timer: ReturnType<typeof setInterval>;
async function load() {
  if (loading.value) return;
  loading.value = true; error.value = ''; info.value = undefined;
  try { const value = await createDatasetS3Download(props.datasetId, props.path); if (!disposed) info.value = value; }
  catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : '生成下载地址失败'; }
  finally { loading.value = false; }
}
async function copy(value: string) { if (await copyToClipboard(value)) showSuccessToast('已复制'); else showErrorToast('复制失败'); }
onMounted(() => { void load(); timer = setInterval(() => { now.value = Date.now(); }, 1000); });
onUnmounted(() => { disposed = true; clearInterval(timer); info.value = undefined; });
</script>
