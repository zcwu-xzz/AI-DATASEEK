export const DATASET_CHAT_PLACEHOLDER = '针对当前数据集提问';

export interface DatasetChatCapabilities {
  attachments: Array<{ file_id: string; filename: string }>;
  skills: string[];
  mcpServers: string[];
  datasetIds: string[];
}

export function buildDatasetChatCapabilities(
  datasetId: string,
  skills: string[],
  mcpServers: string[] = [],
  attachments: Array<{ file_id: string; filename: string }> = [],
): DatasetChatCapabilities {
  return {
    attachments: [...attachments],
    skills: [...skills],
    mcpServers: [...mcpServers],
    datasetIds: datasetId ? [datasetId] : [],
  };
}
