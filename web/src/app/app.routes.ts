import { Routes } from '@angular/router';

import { authGuard, guestGuard } from './core/auth/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    canActivate: [guestGuard],
    loadComponent: () => import('./features/auth/login.page').then((m) => m.LoginPage),
  },
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./core/layout/app-shell.component').then((m) => m.AppShellComponent),
    children: [
      { path: '', redirectTo: 'chat', pathMatch: 'full' },
      {
        path: 'chat',
        loadComponent: () => import('./features/chat/chat.page').then((m) => m.ChatPage),
      },
      {
        path: 'catalog',
        loadComponent: () =>
          import('./features/catalog/catalog.page').then((m) => m.CatalogPage),
      },
      {
        path: 'enums',
        loadComponent: () => import('./features/enums/enums.page').then((m) => m.EnumsPage),
      },
      {
        path: 'relations',
        loadComponent: () =>
          import('./features/relations/relations.page').then((m) => m.RelationsPage),
      },
      {
        path: 'knowledge',
        loadComponent: () =>
          import('./features/knowledge/knowledge.page').then((m) => m.KnowledgePage),
      },
      {
        path: 'sql',
        loadComponent: () => import('./features/sql/sql.page').then((m) => m.SqlPage),
      },
      {
        path: 'ingest',
        loadComponent: () => import('./features/ingest/ingest.page').then((m) => m.IngestPage),
      },
    ],
  },
  { path: '**', redirectTo: 'chat' },
];
