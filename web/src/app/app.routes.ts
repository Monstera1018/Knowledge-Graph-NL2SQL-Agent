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
      { path: '', redirectTo: 'catalog', pathMatch: 'full' },
      {
        path: 'catalog',
        loadComponent: () =>
          import('./features/catalog/catalog.page').then((m) => m.CatalogPage),
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
      {
        path: 'chat',
        loadComponent: () => import('./features/chat/chat.page').then((m) => m.ChatPage),
      },
    ],
  },
  { path: '**', redirectTo: 'catalog' },
];
