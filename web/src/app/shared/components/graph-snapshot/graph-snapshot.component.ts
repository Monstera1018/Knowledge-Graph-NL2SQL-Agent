import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  effect,
  input,
  output,
  viewChild,
} from '@angular/core';
import { NVL } from '@neo4j-nvl/base';
import {
  DragNodeInteraction,
  PanInteraction,
  ZoomInteraction,
} from '@neo4j-nvl/interaction-handlers';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { GraphNodeView, GraphSnapshot } from '../../../core/models/graph.models';
import { EmptyStateComponent } from '../empty-state.component';
import { toNvlElements } from './graph-snapshot.nvl';

type NvlInteraction = DragNodeInteraction | PanInteraction | ZoomInteraction;

const MIN_HOST_SIZE = 48;

@Component({
  selector: 'app-graph-snapshot',
  imports: [
    MatIconModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
  ],
  templateUrl: './graph-snapshot.component.html',
  styleUrl: './graph-snapshot.component.css',
})
export class GraphSnapshotComponent implements AfterViewInit, OnDestroy {
  readonly snapshot = input<GraphSnapshot | null>(null);
  readonly loading = input(false);
  readonly active = input(true);
  readonly highlightUuid = input<string | null>(null);
  readonly depth = input(1);
  readonly depthOptions = input<number[]>([1, 2]);
  readonly showLegend = input(true);
  readonly showDepthControl = input(true);
  readonly emptyTitle = input('暂无图谱数据');
  readonly emptyDescription = input('当前节点在指定跳数内没有可展示的关联');
  readonly ariaLabel = input('关系图谱');

  readonly depthChange = output<number>();

  private readonly graphHost = viewChild<ElementRef<HTMLDivElement>>('graphHost');

  private nvl: NVL | null = null;
  private interactions: NvlInteraction[] = [];
  private nodeIndex = new Map<string, GraphNodeView>();
  private fitNodeIds: string[] = [];
  private resizeObserver: ResizeObserver | null = null;
  private resizeFrameId: number | null = null;
  private viewReady = false;
  private renderedSnapshotKey = '';
  private hostReady = false;
  private pendingInitialFit = false;
  private initialFitTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      this.graphHost();
      this.syncGraph(this.snapshot(), this.loading(), this.active());
    });

    effect(() => {
      this.applyHighlight(this.highlightUuid());
    });
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    this.syncGraph(this.snapshot(), this.loading(), this.active());
  }

  ngOnDestroy(): void {
    this.destroyGraph();
  }

  protected onDepthSelect(depth: number): void {
    if (depth !== this.depth()) {
      this.depthChange.emit(depth);
    }
  }

  protected hasGraphData(snapshot: GraphSnapshot | null): boolean {
    return !!snapshot && snapshot.edges.length > 0;
  }

  protected refit(): void {
    this.fitToGraph();
  }

  private syncGraph(snapshot: GraphSnapshot | null, loading: boolean, active: boolean): void {
    if (!this.viewReady || !active) {
      this.destroyGraph();
      return;
    }

    if (loading) {
      this.destroyGraph();
      return;
    }

    const host = this.graphHost()?.nativeElement;
    if (!host || !this.hasGraphData(snapshot)) {
      this.destroyGraph();
      return;
    }

    if (!this.isHostSized(host)) {
      this.hostReady = false;
      this.ensureHostObserver(host);
      return;
    }

    this.hostReady = true;
    const key = graphSnapshotKey(snapshot!, this.depth());
    if (key === this.renderedSnapshotKey && this.nvl) {
      this.applyHighlight(this.highlightUuid());
      return;
    }

    this.destroyGraph();
    const elements = toNvlElements(snapshot!);
    this.nodeIndex = elements.nodeIndex;
    this.fitNodeIds = elements.fitNodeIds;
    this.pendingInitialFit = true;

    this.nvl = new NVL(host, elements.nodes, elements.relationships, {
      layout: 'd3Force',
      renderer: 'canvas',
      disableTelemetry: true,
      disableWebWorkers: true,
      allowDynamicMinZoom: true,
      layoutTimeLimit: 8_000,
      styling: {
        defaultNodeColor: '#90A4AE',
        defaultRelationshipColor: '#BDBDBD',
        nodeDefaultBorderColor: 'rgba(0, 0, 0, 0.08)',
        selectedBorderColor: '#FF8F00',
        selectedInnerBorderColor: '#FFE082',
        dropShadowColor: 'rgba(0, 0, 0, 0.12)',
      },
    }, {
      onLayoutDone: () => this.runInitialFitOnce(),
    });

    this.bindInteractions();
    this.ensureHostObserver(host);
    this.renderedSnapshotKey = key;
  }

  private runInitialFitOnce(): void {
    if (!this.pendingInitialFit) {
      return;
    }

    if (this.initialFitTimer) {
      clearTimeout(this.initialFitTimer);
    }

    this.initialFitTimer = setTimeout(() => {
      this.initialFitTimer = null;
      if (!this.pendingInitialFit || !this.nvl) {
        return;
      }
      this.pendingInitialFit = false;
      this.fitToGraph();
    }, 500);
  }

  private bindInteractions(): void {
    if (!this.nvl) {
      return;
    }

    const pan = new PanInteraction(this.nvl);
    const zoom = new ZoomInteraction(this.nvl);
    const drag = new DragNodeInteraction(this.nvl);

    this.interactions = [pan, zoom, drag];
  }

  private ensureHostObserver(host: HTMLElement): void {
    if (this.resizeObserver) {
      return;
    }

    this.resizeObserver = new ResizeObserver(() => {
      if (!this.active() || !this.isHostSized(host) || this.hostReady) {
        return;
      }
      if (this.resizeFrameId != null) {
        return;
      }
      this.resizeFrameId = requestAnimationFrame(() => {
        this.resizeFrameId = null;
        if (!this.active() || !this.isHostSized(host) || this.hostReady) {
          return;
        }
        this.hostReady = true;
        this.syncGraph(this.snapshot(), this.loading(), this.active());
      });
    });
    this.resizeObserver.observe(host);
  }

  private fitToGraph(): void {
    if (!this.nvl) {
      return;
    }

    const nodeIds = this.fitNodeIds.length > 0 ? this.fitNodeIds : [...this.nodeIndex.keys()];
    if (nodeIds.length === 0) {
      return;
    }

    this.nvl.fit(nodeIds, { animated: false });
  }

  private isHostSized(host: HTMLElement): boolean {
    return host.clientWidth >= MIN_HOST_SIZE && host.clientHeight >= MIN_HOST_SIZE;
  }

  private applyHighlight(uuid: string | null): void {
    if (!this.nvl) {
      return;
    }

    this.nvl.deselectAll();
    if (!uuid || !this.nodeIndex.has(uuid)) {
      return;
    }

    this.nvl.updateElementsInGraph([{ id: uuid, selected: true }], []);
  }

  private destroyGraph(): void {
    if (this.initialFitTimer) {
      clearTimeout(this.initialFitTimer);
      this.initialFitTimer = null;
    }

    if (this.resizeFrameId != null) {
      cancelAnimationFrame(this.resizeFrameId);
      this.resizeFrameId = null;
    }

    for (const interaction of this.interactions) {
      interaction.destroy();
    }
    this.interactions = [];

    this.resizeObserver?.disconnect();
    this.resizeObserver = null;

    this.nvl?.destroy();
    this.nvl = null;
    this.nodeIndex.clear();
    this.fitNodeIds = [];
    this.renderedSnapshotKey = '';
    this.hostReady = false;
    this.pendingInitialFit = false;
  }
}

function graphSnapshotKey(snapshot: GraphSnapshot, depth: number): string {
  const nodeIds = snapshot.nodes.map((node) => node.uuid).join(',');
  const edgeIds = snapshot.edges
    .map((edge) => `${edge.source}-${edge.target}-${edge.type}`)
    .join(',');
  return `${snapshot.focus_uuid}|d${depth}|${nodeIds}|${edgeIds}`;
}
