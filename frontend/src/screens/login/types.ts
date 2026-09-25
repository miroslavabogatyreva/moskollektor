// ДОГОВОР API MOS-39: GET /api/auth/info. demo_accounts пустой, если
// AUTH_DEMO_HINTS≠1 (в эксплуатации подсказка выключена).
export interface DemoAccount {
  login: string
  password: string
  role_code: string
  role_name: string
  sees: string
}

export interface AuthInfo {
  ldap_configured: boolean
  demo_accounts: DemoAccount[]
}
