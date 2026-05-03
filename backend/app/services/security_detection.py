SECURITY_KEYWORDS = {
    "permission": "potential_access_check",
    "authorize": "potential_access_check",
    "authorization": "potential_access_check",
    "authentication": "potential_access_check",
    "access": "potential_access_check",
    "role": "potential_access_check",
    "policy": "potential_policy_file",
    "SecurityException": "potential_access_check",
    "checkPermission": "potential_access_check",
    "enforcePermission": "potential_access_check",
    "getCallingUid": "potential_entry_point",
    "getCallingUserId": "potential_entry_point",
    "hasSignatureCapability": "potential_helper",
    "Binder": "potential_entry_point",
    "SELinux": "potential_policy_file",
    "RBAC": "potential_access_check",
    "SubjectAccessReview": "potential_access_check",
    "AccessDenied": "potential_access_check",
    "Forbidden": "potential_access_check",
    "hasPermission": "potential_access_check",
    "hasRole": "potential_access_check",
    "hasAuthority": "potential_access_check",
    "authenticated": "potential_access_check",
    "requestMatchers": "potential_entry_point",
    "antMatchers": "potential_entry_point",
    "permitAll": "potential_access_check",
    "denyAll": "potential_access_check",
}


def detect_security_tags(text: str, file_path: str = "") -> list[str]:
    haystack = f"{file_path}\n{text}"
    tags = {tag for keyword, tag in SECURITY_KEYWORDS.items() if keyword.lower() in haystack.lower()}
    suffix = file_path.lower()
    if suffix.endswith((".xml", ".json", ".yaml", ".yml", ".te")) and tags:
        tags.add("potential_config_file")
    return sorted(tags)


def confidence_for_tags(tags: list[str]) -> str:
    if len(tags) >= 3:
        return "High"
    if len(tags) >= 1:
        return "Medium"
    return "Low"
