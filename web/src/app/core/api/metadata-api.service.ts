import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type { ImportStage } from '../models/ingest.models';
import {
  ColumnNode,
  EnumListItem,
  EnumNode,
  GraphSnapshot,
  JobCreatedResponse,
  JobSnapshot,
  KnowledgeDetail,
  KnowledgeNode,
  KnowledgeRelation,
  Page,
  SQLDetail,
  SQLNode,
  TableJoinRelation,
  TableNode,
} from '../models/metadata.models';
export interface ListQuery {
  page?: number;
  page_size?: number;
  q?: string;
  table_uuid?: string;
  column_uuid?: string;
  source?: string;
  enabled?: boolean | string;
  sort?: string;
}

export interface GraphQuery {
  depth?: number;
}

@Injectable({ providedIn: 'root' })
export class MetadataApiService {
  private readonly http = inject(HttpClient);

  listTables(query: ListQuery = {}): Observable<Page<TableNode>> {
    return this.http.get<Page<TableNode>>('/api/tables', { params: this.toParams(query) });
  }

  getTable(uuid: string): Observable<TableNode> {
    return this.http.get<TableNode>(`/api/tables/${uuid}`);
  }

  listTableJoinRelations(uuid: string): Observable<TableJoinRelation[]> {
    return this.http.get<TableJoinRelation[]>(`/api/tables/${uuid}/join-relations`);
  }

  listJoinRelations(query: ListQuery = {}): Observable<Page<TableJoinRelation>> {
    return this.http.get<Page<TableJoinRelation>>('/api/join-relations', {
      params: this.toParams(query),
    });
  }

  replaceTableJoinRelations(
    uuid: string,
    relations: Pick<TableJoinRelation, 'from_table_name' | 'to_table_name' | 'condition'>[],
  ): Observable<TableJoinRelation[]> {
    return this.http.put<TableJoinRelation[]>(`/api/tables/${uuid}/join-relations`, relations);
  }

  getGraph(uuid: string, query: GraphQuery = {}): Observable<GraphSnapshot> {
    return this.http.get<GraphSnapshot>(`/api/graph/${uuid}`, { params: this.toGraphParams(query) });
  }

  createTable(body: {
    name: string;
    comment?: string;
    description?: string;
  }): Observable<TableNode> {
    return this.http.post<TableNode>('/api/tables', body);
  }

  updateTable(
    uuid: string,
    body: Partial<{
      name: string;
      comment: string;
      description: string;
    }>,
  ): Observable<TableNode> {
    return this.http.put<TableNode>(`/api/tables/${uuid}`, body);
  }

  deleteTable(uuid: string): Observable<void> {
    return this.http.delete<void>(`/api/tables/${uuid}`);
  }

  listColumns(query: ListQuery = {}): Observable<Page<ColumnNode>> {
    return this.http.get<Page<ColumnNode>>('/api/columns', { params: this.toParams(query) });
  }

  createColumn(body: {
    table_uuid: string;
    name: string;
    comment?: string;
    dtype?: string;
  }): Observable<ColumnNode> {
    return this.http.post<ColumnNode>('/api/columns', body);
  }

  updateColumn(
    uuid: string,
    body: Partial<Pick<ColumnNode, 'name' | 'comment' | 'dtype'>>,
  ): Observable<ColumnNode> {
    return this.http.put<ColumnNode>(`/api/columns/${uuid}`, body);
  }

  deleteColumn(uuid: string): Observable<void> {
    return this.http.delete<void>(`/api/columns/${uuid}`);
  }

  listEnums(query: ListQuery = {}): Observable<Page<EnumListItem>> {
    return this.http.get<Page<EnumListItem>>('/api/enums', { params: this.toParams(query) });
  }

  createEnum(body: Pick<EnumNode, 'value'> & { column_uuid: string }): Observable<EnumNode> {
    return this.http.post<EnumNode>('/api/enums', body);
  }

  updateEnum(uuid: string, body: Pick<EnumNode, 'value'>): Observable<EnumNode> {
    return this.http.put<EnumNode>(`/api/enums/${uuid}`, body);
  }

  deleteEnum(uuid: string): Observable<void> {
    return this.http.delete<void>(`/api/enums/${uuid}`);
  }

  listKnowledge(query: ListQuery = {}): Observable<Page<KnowledgeNode>> {
    return this.http.get<Page<KnowledgeNode>>('/api/knowledge', { params: this.toParams(query) });
  }

  getKnowledge(uuid: string): Observable<KnowledgeDetail> {
    return this.http.get<KnowledgeDetail>(`/api/knowledge/${uuid}`);
  }

  createKnowledge(body: {
    name: string;
    description?: string;
    relations?: KnowledgeRelation[];
  }): Observable<KnowledgeNode> {
    return this.http.post<KnowledgeNode>('/api/knowledge', body);
  }

  updateKnowledge(
    uuid: string,
    body: {
      name?: string;
      description?: string;
      relations?: KnowledgeRelation[];
    },
  ): Observable<KnowledgeNode> {
    return this.http.put<KnowledgeNode>(`/api/knowledge/${uuid}`, body);
  }

  deleteKnowledge(uuid: string): Observable<void> {
    return this.http.delete<void>(`/api/knowledge/${uuid}`);
  }

  listSqls(query: ListQuery = {}): Observable<Page<SQLNode>> {
    return this.http.get<Page<SQLNode>>('/api/sqls', { params: this.toParams(query) });
  }

  getSql(uuid: string): Observable<SQLDetail> {
    return this.http.get<SQLDetail>(`/api/sqls/${uuid}`);
  }

  createSql(body: Pick<SQLNode, 'name' | 'logic' | 'content' | 'dialect'>): Observable<SQLNode> {
    return this.http.post<SQLNode>('/api/sqls', body);
  }

  updateSql(
    uuid: string,
    body: Partial<Pick<SQLNode, 'name' | 'logic' | 'content' | 'dialect' | 'enabled'>>,
  ): Observable<SQLNode> {
    return this.http.put<SQLNode>(`/api/sqls/${uuid}`, body);
  }

  deleteSql(uuid: string): Observable<void> {
    return this.http.delete<void>(`/api/sqls/${uuid}`);
  }

  listImportJobs(): Observable<JobSnapshot[]> {
    return this.http.get<JobSnapshot[]>('/api/ingest');
  }

  getImportJob(jobId: string): Observable<JobSnapshot> {
    return this.http.get<JobSnapshot>(`/api/ingest/${jobId}`);
  }

  createImportJob(type: ImportStage, file: File): Observable<JobCreatedResponse> {
    const body = new FormData();
    body.append('type', type);
    body.append('file', file, file.name);
    return this.http.post<JobCreatedResponse>('/api/ingest', body);
  }

  private toParams(query: ListQuery): HttpParams {
    let params = new HttpParams();
    if (query.page != null) params = params.set('page', String(query.page));
    if (query.page_size != null) params = params.set('page_size', String(query.page_size));
    if (query.q) params = params.set('q', query.q);
    if (query.table_uuid) params = params.set('table_uuid', query.table_uuid);
    if (query.column_uuid) params = params.set('column_uuid', query.column_uuid);
    if (query.source) params = params.set('source', query.source);
    if (query.enabled !== undefined && query.enabled !== '') {
      params = params.set('enabled', String(query.enabled));
    }
    if (query.sort) params = params.set('sort', query.sort);
    return params;
  }

  private toGraphParams(query: GraphQuery): HttpParams {
    let params = new HttpParams();
    if (query.depth != null) params = params.set('depth', String(query.depth));
    return params;
  }
}
