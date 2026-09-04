import type { Component } from 'vue';
import FileIcon from '@/components/icons/FileIcon.vue';
import ImageFilePreview from '@/components/filePreviews/ImageFilePreview.vue';
import ObjFilePreview from '@/components/filePreviews/ObjFilePreview.vue';
import TiffFilePreview from '@/components/filePreviews/TiffFilePreview.vue';
import ShapefilePreview from '@/components/filePreviews/ShapefilePreview.vue';
import HtmlFilePreview from '@/components/filePreviews/HtmlFilePreview.vue';
import MolecularStructurePreview from '@/components/filePreviews/MolecularStructurePreview.vue';
import FastaSequencePreview from '@/components/filePreviews/FastaSequencePreview.vue';
import GenomeBrowserPreview from '@/components/filePreviews/GenomeBrowserPreview.vue';
import BlastAlignmentPreview from '@/components/filePreviews/BlastAlignmentPreview.vue';
import AlignmentFilePreview from '@/components/filePreviews/AlignmentFilePreview.vue';
import type { RendererInfo } from '@/api/renderer';

export type RendererKind = 'builtin' | 'api' | 'component';

export interface RendererDefinition {
  id: string;
  name: string;
  description: string;
  kind: RendererKind;
  extensions: string[];
  preview: Component;
  icon: Component;
  enabled: boolean;
  scope?: 'global' | 'user';
  user_id?: string | null;
  owner_user_id?: string | null;
  api_url?: string | null;
  entry?: string | null;
  config?: Record<string, any>;
  editable?: boolean;
  installed?: boolean;
  source?: 'official' | 'personal';
}

const builtinRenderers: RendererDefinition[] = [
  {
    id: 'builtin-binary-alignment', name: 'SAM/BAM/CRAM Alignment Renderer', description: 'Interactive regional coverage and read alignment browser.', kind: 'builtin',
    extensions: ['sam', 'bam', 'cram'], preview: AlignmentFilePreview, icon: FileIcon, enabled: true, scope: 'global', editable: false, installed: true, source: 'official',
  },
  {
    id: 'builtin-fasta-sequence', name: 'FASTA Sequence Renderer', description: 'Ruler-based FASTA sequence browser.', kind: 'builtin',
    extensions: ['fasta', 'fa', 'fna', 'ffn', 'frn', 'fastq', 'fq'], preview: FastaSequencePreview, icon: FileIcon, enabled: true, scope: 'global', editable: false, installed: true, source: 'official',
  },
  {
    id: 'builtin-bio-text', name: 'Biological Text Renderer', description: 'Structured preview for VCF, GFF/GTF, BED and SAM files.', kind: 'builtin',
    extensions: ['vcf', 'gff', 'gff3', 'gtf', 'bed', 'wig', 'bedgraph'], preview: GenomeBrowserPreview, icon: FileIcon, enabled: true, scope: 'global', editable: false, installed: true, source: 'official',
  },
  {
    id: 'builtin-blast-alignment', name: 'BLAST Alignment Renderer', description: 'Interactive BLAST tabular hit browser.', kind: 'builtin',
    extensions: ['blast', 'blast6', 'm8', 'blasttab', 'tab'], preview: BlastAlignmentPreview, icon: FileIcon, enabled: true, scope: 'global', editable: false, installed: true, source: 'official',
  },
  {
    id: 'builtin-molecular-structure',
    name: 'Molecular Structure 3D Renderer',
    description: 'Interactive molecular and crystal structure renderer powered by 3Dmol.js.',
    kind: 'builtin',
    extensions: ['cif', 'mmcif', 'pdb', 'ent', 'mol', 'sdf', 'xyz', 'mol2', 'vasp'],
    preview: MolecularStructurePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
  {
    id: 'builtin-png-image',
    name: 'PNG Image Renderer',
    description: 'Built-in renderer for PNG images using a signed file URL.',
    kind: 'builtin',
    extensions: ['png'],
    preview: ImageFilePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
  {
    id: 'builtin-obj-online3dviewer',
    name: 'OBJ 3D Model Renderer',
    description: 'Built-in renderer for OBJ models using Online3DViewer.',
    kind: 'builtin',
    extensions: ['obj'],
    preview: ObjFilePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
  {
    id: 'builtin-tiff-image',
    name: 'TIFF Image Renderer',
    description: 'Built-in renderer for TIFF and GeoTIFF images using browser-side decoding.',
    kind: 'builtin',
    extensions: ['tif', 'tiff'],
    preview: TiffFilePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
  {
    id: 'builtin-shapefile',
    name: 'Shapefile Renderer',
    description: 'Built-in renderer for Shapefile geometry and DBF attributes.',
    kind: 'builtin',
    extensions: ['shp', 'shx', 'dbf', 'prj', 'cpg'],
    preview: ShapefilePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
  {
    id: 'builtin-html',
    name: 'HTML Renderer',
    description: 'Built-in renderer for HTML files with task-local relative asset resolution.',
    kind: 'builtin',
    extensions: ['html', 'htm'],
    preview: HtmlFilePreview,
    icon: FileIcon,
    enabled: true,
    scope: 'global',
    editable: false,
    installed: true,
    source: 'official',
  },
];

let configuredRenderers: RendererDefinition[] = [];

export function listRenderers(): RendererDefinition[] {
  return [...builtinRenderers, ...configuredRenderers];
}

export function rendererDefinitionsFromConfigs(configs: RendererInfo[]): RendererDefinition[] {
  return configs.map((config) => ({
    id: config.id,
    name: config.name,
    description: config.description,
    kind: config.kind,
    extensions: config.extensions,
    preview: ImageFilePreview,
    icon: FileIcon,
    enabled: config.enabled,
    scope: config.scope,
    user_id: config.user_id,
    owner_user_id: config.owner_user_id,
    api_url: config.api_url,
    entry: config.entry,
    config: config.config,
    editable: true,
    installed: config.installed,
    source: config.source,
  }));
}

export function mergeRendererConfigs(configs: RendererInfo[]): RendererDefinition[] {
  configuredRenderers = rendererDefinitionsFromConfigs(configs);
  return listRenderers();
}

export function listBuiltinRenderers(): RendererDefinition[] {
  return [...builtinRenderers];
}

export function findRendererByFilename(filename: string): RendererDefinition | null {
  if (['poscar', 'contcar'].includes(filename.trim().toLowerCase())) {
    return listRenderers().find((renderer) => renderer.id === 'builtin-molecular-structure') || null;
  }
  const extension = filename.split('.').pop()?.toLowerCase();
  if (!extension) return null;
  return listRenderers().find((renderer) => renderer.enabled && renderer.extensions.includes(extension)) || null;
}
