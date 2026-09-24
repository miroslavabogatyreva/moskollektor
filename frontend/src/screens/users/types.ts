// ДОГОВОР API MOS-226 (Q4.19), Ф-66. auth_source и roles — те же коды, что
// в lib/auth.ts (AuthUser), is_active — ref.app_user.is_active (008_rbac.sql:38).
export interface AppUser {
  login: string
  full_name: string
  auth_source: 'local' | 'ldap'
  is_active: boolean
  roles: string[]
}
