/** 工单身份字段可能是 users.id 或 username，当前用户两侧都认。 */
export function isSameUser(
  field?: string | null,
  ...identities: Array<string | null | undefined>
): boolean {
  if (!field) return false;
  return identities.some((id) => !!id && id === field);
}
