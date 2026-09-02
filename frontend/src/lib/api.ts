import axios from "axios";

export const api = axios.create({ baseURL: "/api" });

let _accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  _accessToken = token;
}

export function getAccessToken() {
  return _accessToken;
}

// Attach access token to every request
api.interceptors.request.use((config) => {
  if (_accessToken) {
    config.headers.Authorization = `Bearer ${_accessToken}`;
  }
  return config;
});

// On 401, try silent refresh then retry once
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry && !original.url?.includes("/auth/")) {
      original._retry = true;
      try {
        const { data } = await axios.post("/api/auth/refresh", {}, { withCredentials: true });
        setAccessToken(data.access_token);
        original.headers.Authorization = `Bearer ${data.access_token}`;
        return api(original);
      } catch {
        setAccessToken(null);
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// Auth API
export const authApi = {
  login: (username: string, password: string) =>
    api.post("/auth/login", { username, password }),

  verify2fa: (temp_token: string, code: string) =>
    api.post("/auth/verify-2fa", { temp_token, code }),

  refresh: () =>
    axios.post("/api/auth/refresh", {}, { withCredentials: true }),

  logout: () =>
    api.post("/auth/logout", {}, { withCredentials: true }),

  me: () =>
    api.get("/auth/me"),

  setup2fa: () =>
    api.post("/auth/setup-2fa"),

  enable2fa: (secret: string, code: string) =>
    api.post("/auth/enable-2fa-confirm", { secret, code }),

  disable2fa: (code: string) =>
    api.post("/auth/disable-2fa", { code }),

  changePassword: (current_password: string, new_password: string) =>
    api.post("/auth/change-password", { current_password, new_password }),

  changeUsername: (new_username: string, current_password: string) =>
    api.post("/auth/change-username", { new_username, current_password }),

  updateMe: (patch: { audible_account_id?: string }) =>
    api.patch("/auth/me", patch),

  listSessions: () =>
    api.get("/auth/sessions"),

  revokeSession: (id: number) =>
    api.delete(`/auth/sessions/${id}`),

  revokeAllSessions: () =>
    api.delete("/auth/sessions"),
};

// Users API (admin only)
export const usersApi = {
  list: () =>
    api.get("/users"),

  create: (username: string, password: string, is_admin: boolean) =>
    api.post("/users", { username, password, is_admin }),

  update: (id: number, patch: { is_active?: boolean; is_admin?: boolean; new_password?: string; owner_name?: string; audible_account_id?: string | null }) =>
    api.patch(`/users/${id}`, patch),

  updatePermissions: (id: number, patch: {
    can_download?: boolean;
    can_scan?: boolean;
    can_manage_accounts?: boolean;
    can_liberate?: boolean;
    can_remove_downloads?: boolean;
    download_cap?: number | null;
  }) =>
    api.patch(`/users/${id}/permissions`, patch),

  delete: (id: number) =>
    api.delete(`/users/${id}`),
};

// Settings API
export const settingsApi = {
  getLibation: () =>
    api.get("/settings/libation"),

  updateLibation: (data: Record<string, unknown>) =>
    api.put("/settings/libation", data),

  getStats: () =>
    api.get("/settings/stats"),
};

// Chaptarr API
export interface ChaptarrSettingsData {
  enabled: boolean;
  url: string;
  import_mode: "auto" | "copy" | "move";
  auto_import: boolean;
  path_from: string;
  path_to: string;
  api_key_set: boolean;
}

export interface ChaptarrImportRecord {
  id: number;
  book_id: string;
  book_title: string | null;
  status: "running" | "complete" | "error" | "skipped";
  matched_by: string | null;
  command_id: number | null;
  file_path: string | null;
  message: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export const chaptarrApi = {
  // Readable by any signed-in user; getSettings is admin-only.
  status: () =>
    api.get<{ enabled: boolean; configured: boolean }>("/chaptarr/status"),

  getSettings: () =>
    api.get<ChaptarrSettingsData>("/chaptarr/settings"),

  // Omit api_key to keep the stored key; send "" to clear it.
  updateSettings: (patch: Partial<ChaptarrSettingsData> & { api_key?: string }) =>
    api.put<ChaptarrSettingsData>("/chaptarr/settings", patch),

  test: () =>
    api.post<{ app_name: string; version: string; root_folders: { id: number | null; path: string | null; name: string | null }[] }>(
      "/chaptarr/test"
    ),

  importBooks: (book_ids: string[]) =>
    api.post<ChaptarrImportRecord[]>("/chaptarr/import", { book_ids }),

  listImports: (limit = 25) =>
    api.get<ChaptarrImportRecord[]>("/chaptarr/imports", { params: { limit } }),
};
