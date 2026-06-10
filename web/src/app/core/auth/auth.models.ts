export interface AuthUser {
  id: string;
  username: string;
  display_name: string;
  workspace_ids: string[];
  default_workspace_id: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  user: AuthUser;
}

export interface MeResponse {
  user: AuthUser;
}
