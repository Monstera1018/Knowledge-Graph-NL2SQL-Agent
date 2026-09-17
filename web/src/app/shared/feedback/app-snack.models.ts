export type AppSnackKind = 'success' | 'error' | 'info';

export interface AppSnackData {
  message: string;
  kind: AppSnackKind;
}
