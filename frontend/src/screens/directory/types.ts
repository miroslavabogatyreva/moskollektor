// ДОГОВОР API MOS-39. Строка role_map даёт РОЛЬ или УЗЕЛ области видимости,
// не оба сразу (db/migrations/047_auth.sql, ref.ldap_role_map).
export interface RoleMapRow {
  group_cn: string
  role_code: string | null
  object_id: number | null
}

export interface DirectoryInfo {
  uri: string
  base_dn: string
  user_template: string
  role_map: RoleMapRow[]
}

export interface DirectoryCheckResult {
  ok: boolean
  ms: number
  message: string
}
