// File API service
import { apiClient, API_CONFIG, BASE_URL } from './client.ts';
import type { ApiResponse } from './client.ts';
import type { SignedUrlResponse } from '../types/response.ts';

export interface MatrixArrayInfo { name: string; shape: number[]; dtype: string; sparse: boolean }
export interface MatrixPreparation { preview_id: string; source_name: string; arrays: MatrixArrayInfo[] }
export interface MatrixPlane {
  values: (number | null)[][]; rows: number[]; columns: number[]; shape: number[];
  sampled: boolean; minimum: number | null; maximum: number | null; mean: number | null; non_finite: number;
}
export async function prepareMatrixPreview(fileId: string): Promise<MatrixPreparation> {
  return (await apiClient.post<ApiResponse<MatrixPreparation>>('/files/matrix-preview/prepare', { file_id: fileId })).data.data;
}
export async function renderMatrixPreview(fileId: string, previewId: string, variable: string, axes: number[], indices: number[], component: string): Promise<MatrixPlane> {
  return (await apiClient.post<ApiResponse<MatrixPlane>>('/files/matrix-preview/render', { file_id: fileId, preview_id: previewId, variable, axes, indices, component })).data.data;
}
export async function releaseMatrixPreview(fileId: string, previewId: string): Promise<void> {
  await apiClient.post('/files/matrix-preview/release', { file_id: fileId, preview_id: previewId });
}

/**
 * File info type
 */
export interface FileInfo {
  file_id: string;
  filename: string;
  relative_path?: string;
  content_type?: string;
  size?: number;
  upload_date: string;
  metadata?: Record<string, any>;
  file_url?: string;
}

export interface ShapefilePreviewLayer {
  name: string;
  relative_path: string;
  complete: boolean;
  missing_components: string[];
  components: FileInfo[];
}

export async function prepareShapefilePreview(fileId: string): Promise<{ source_name: string; layers: ShapefilePreviewLayer[] }> {
  const response = await apiClient.post<ApiResponse<{ source_name: string; layers: ShapefilePreviewLayer[] }>>('/files/shapefile-preview/prepare', { file_id: fileId });
  return response.data.data;
}

export interface MolecularPreviewPreparation {
  source_name: string;
  source_format: 'cif' | 'pdb' | 'sdf' | 'xyz' | 'mol2' | 'vasp';
  content_type?: string;
  size_bytes?: number;
  periodic: boolean;
  supports_unit_cell: boolean;
}

export async function prepareMolecularPreview(fileId: string): Promise<MolecularPreviewPreparation> {
  const response = await apiClient.post<ApiResponse<MolecularPreviewPreparation>>(
    '/files/molecular-preview/prepare',
    { file_id: fileId },
  );
  return response.data.data;
}

export interface AlignmentReference {
  name: string;
  length: number;
}

export interface AlignmentPreviewPreparation {
  source_name: string;
  preview_id: string;
  format: 'SAM' | 'BAM' | 'CRAM';
  references: AlignmentReference[];
  sort_order?: string;
  read_groups: number;
  suggested_reference?: string;
  suggested_start?: number;
}

export interface AlignmentPreviewRead {
  name: string;
  start: number;
  end: number;
  reverse: boolean;
  mapq: number;
  cigar: string;
  paired: boolean;
  duplicate: boolean;
  secondary: boolean;
  supplementary: boolean;
  read1: boolean;
  read2: boolean;
  mate_start?: number;
  mate_reference?: string;
  template_length: number;
  read_group?: string;
  nm?: number;
  md?: string;
  blocks: Array<{ start: number; end: number }>;
  mismatches: Array<{ position: number; query_base: string; reference_base: string }>;
  insertions: Array<{ position: number; length: number; sequence: string }>;
  deletions: Array<{ start: number; end: number; length: number }>;
  splices: Array<{ start: number; end: number; length: number }>;
}

export interface AlignmentRegionPreview {
  reference: string;
  start: number;
  end: number;
  bin_width: number;
  coverage: Array<{ start: number; end: number; depth: number }>;
  reads: AlignmentPreviewRead[];
  returned_reads: number;
  scanned_overlapping_reads: number;
  truncated: boolean;
}

export async function prepareAlignmentPreview(fileId: string): Promise<AlignmentPreviewPreparation> {
  const response = await apiClient.post<ApiResponse<AlignmentPreviewPreparation>>(
    '/files/alignment-preview/prepare',
    { file_id: fileId },
  );
  return response.data.data;
}

export async function previewAlignmentRegion(
  fileId: string,
  previewId: string,
  reference: string,
  start: number,
  end: number,
  maxReads = 500,
): Promise<AlignmentRegionPreview> {
  const response = await apiClient.post<ApiResponse<AlignmentRegionPreview>>(
    '/files/alignment-preview/region',
    { file_id: fileId, preview_id: previewId, reference, start, end, max_reads: maxReads, bin_count: 500 },
  );
  return response.data.data;
}

export async function releaseAlignmentPreview(fileId: string, previewId: string): Promise<void> {
  await apiClient.post<ApiResponse<null>>('/files/alignment-preview/release', {
    file_id: fileId,
    preview_id: previewId,
  });
}

export interface AstronomyDataset {
  index: number;
  name: string;
  type?: string;
  kind: 'image' | 'spectrum' | 'table' | 'empty';
  shape: number[];
  dtype?: string;
  samples?: number;
  columns?: Array<{ name: string; format: string; unit?: string }>;
  row_count?: number;
  table_preview?: Array<Record<string, any>>;
  spectrum_preview?: Array<{ index: number; value: number | null }>;
  wcs?: { celestial: boolean; axis_types?: string[]; units?: string[] };
}

export interface AstronomyPreviewPreparation {
  source_name: string;
  preview_id: string;
  format: 'FITS' | 'TIFF' | 'GeoTIFF';
  datasets: AstronomyDataset[];
  selected_dataset?: number;
  geospatial?: Record<string, any>;
}

export interface AstronomySelection {
  file_id: string;
  preview_id: string;
  dataset_index: number;
  slice_indices: number[];
  band: number;
}

export interface AstronomyRenderResult {
  image_base64: string;
  source_width: number;
  source_height: number;
  render_width: number;
  render_height: number;
  display_min: number;
  display_max: number;
  statistics: Record<string, number | null>;
  histogram: { counts: number[]; edges: number[] };
  wcs?: { celestial: boolean; axis_types?: string[]; units?: string[]; corners?: number[][] };
}

export async function prepareAstronomyPreview(fileId: string): Promise<AstronomyPreviewPreparation> {
  const response = await apiClient.post<ApiResponse<AstronomyPreviewPreparation>>(
    '/files/astronomy-preview/prepare', { file_id: fileId },
  );
  return response.data.data;
}

export async function renderAstronomyPreview(
  selection: AstronomySelection,
  options: { stretch: string; interval: string; low?: number; high?: number; colour_map: string; invert: boolean },
): Promise<AstronomyRenderResult> {
  const response = await apiClient.post<ApiResponse<AstronomyRenderResult>>(
    '/files/astronomy-preview/render', { ...selection, ...options },
  );
  return response.data.data;
}

export async function inspectAstronomyPixel(selection: AstronomySelection, x: number, y: number): Promise<Record<string, number | null>> {
  const response = await apiClient.post<ApiResponse<Record<string, number | null>>>(
    '/files/astronomy-preview/pixel', { ...selection, x, y },
  );
  return response.data.data;
}

export async function inspectAstronomyRegion(
  selection: AstronomySelection, x0: number, y0: number, x1: number, y1: number,
): Promise<Record<string, any>> {
  const response = await apiClient.post<ApiResponse<Record<string, any>>>(
    '/files/astronomy-preview/region', { ...selection, x0, y0, x1, y1 },
  );
  return response.data.data;
}

export interface AstronomyDetectedSource {
  id: number; x: number; y: number; peak: number; snr: number; ra_deg?: number; dec_deg?: number;
}

export async function detectAstronomySources(selection: AstronomySelection, thresholdSigma = 5): Promise<{ source_count: number; truncated: boolean; background: number; noise: number; sources: AstronomyDetectedSource[] }> {
  const response = await apiClient.post<ApiResponse<any>>('/files/astronomy-preview/sources', { ...selection, threshold_sigma: thresholdSigma });
  return response.data.data;
}

export async function releaseAstronomyPreview(fileId: string, previewId: string): Promise<void> {
  await apiClient.post<ApiResponse<null>>('/files/astronomy-preview/release', {
    file_id: fileId, preview_id: previewId, dataset_index: 0, slice_indices: [], band: 1,
  });
}

export interface LargeUploadInitResponse {
  upload_id: string;
  file_id: string;
  filename: string;
  size: number;
  part_size: number;
  status: string;
  expires_at: string;
}

export interface LargeUploadPartUploadResponse {
  upload_id: string;
  part_number: number;
  etag: string;
  size: number;
}

export interface LargeUploadPart {
  part_number: number;
  etag: string;
  size?: number;
}

export async function initLargeUpload(file: File, metadata?: Record<string, any>): Promise<LargeUploadInitResponse> {
  const response = await apiClient.post<ApiResponse<LargeUploadInitResponse>>('/files/large-uploads/init', {
    filename: file.name,
    size: file.size,
    content_type: file.type || 'application/octet-stream',
    metadata: metadata || {},
  });
  return response.data.data;
}

export async function completeLargeUpload(uploadId: string, parts: LargeUploadPart[]): Promise<FileInfo> {
  const response = await apiClient.post<ApiResponse<FileInfo>>(`/files/large-uploads/${uploadId}/complete`, {
    parts,
  });
  return response.data.data;
}

export async function abortLargeUpload(uploadId: string): Promise<void> {
  await apiClient.post<ApiResponse<void>>(`/files/large-uploads/${uploadId}/abort`);
}

export async function uploadLargeFilePart(
  uploadId: string,
  partNumber: number,
  blob: Blob,
  onProgress?: (loaded: number) => void,
): Promise<LargeUploadPartUploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `${BASE_URL}/files/large-uploads/${encodeURIComponent(uploadId)}/parts/${partNumber}`, true);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress?.(event.loaded);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const payload = JSON.parse(xhr.responseText || '{}') as ApiResponse<LargeUploadPartUploadResponse>;
        if (!payload.data?.etag) {
          reject(new Error('Missing ETag from large upload part response'));
          return;
        }
        resolve(payload.data);
        return;
      }
      reject(new Error(`Large upload part failed: ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('Large upload part network error'));
    xhr.send(blob);
  });
}



/**
 * Upload file
 * @param file File to upload
 * @param metadata Optional metadata
 * @returns Upload result
 */
export async function uploadFile(file: File, metadata?: Record<string, any>): Promise<FileInfo> {
  const formData = new FormData();
  formData.append('file', file);
  
  if (metadata) {
    formData.append('metadata', JSON.stringify(metadata));
  }

  const response = await apiClient.post<ApiResponse<FileInfo>>('/files', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data.data;
}

/**
 * Download file
 * @param fileId File ID
 * @returns File download result
 */
export async function downloadFile(fileId: string): Promise<Blob> {
  const response = await apiClient.get(`/files/${fileId}/download`, {
    responseType: 'blob',
  });
  
  return response.data;
}

/**
 * Delete file
 * @param fileId File ID
 * @returns Success status
 */
export async function deleteFile(fileId: string): Promise<boolean> {
  try {
    await apiClient.delete<ApiResponse<void>>(`/files/${fileId}`);
    return true;
  } catch (error) {
    console.error('Failed to delete file:', error);
    return false;
  }
}

/**
 * Get file information
 * @param fileId File ID
 * @returns File information or null if not found
 */
export async function getFileInfo(fileId: string): Promise<FileInfo | null> {
  try {
    const response = await apiClient.get<ApiResponse<FileInfo>>(
      `/files/${encodeURIComponent(fileId)}/info`,
    );
    return response.data.data;
  } catch (error) {
    console.error('Failed to get file info:', error);
    return null;
  }
}

/**
 * Create file signed URL
 * @param fileId File ID to create signed URL for
 * @param expireMinutes URL expiration time in minutes (default: 15)
 * @returns Signed URL response for file download
 */
export async function createFileSignedUrl(fileId: string, expireMinutes: number = 15): Promise<SignedUrlResponse> {
  const response = await apiClient.post<ApiResponse<SignedUrlResponse>>(`/files/${fileId}/signed-url`, {
    expire_minutes: expireMinutes
  });
  return response.data.data;
}

/**
 * Get file download URL
 * @param file File info
 * @returns Promise resolving to file download URL string
 */
export async function getFileDownloadUrl(
  fileInfo: FileInfo,
): Promise<string> {
  if (fileInfo.file_url) {
    if (/^https?:\/\//i.test(fileInfo.file_url)) {
      return fileInfo.file_url;
    }
    return `${API_CONFIG.host}${fileInfo.file_url}`;
  }
  const signedUrlResponse = await createFileSignedUrl(fileInfo.file_id);
  if (/^https?:\/\//i.test(signedUrlResponse.signed_url)) {
    return signedUrlResponse.signed_url;
  }
  return `${API_CONFIG.host}${signedUrlResponse.signed_url}`;
}
