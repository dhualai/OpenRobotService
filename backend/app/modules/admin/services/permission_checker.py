from typing import Dict, Any, List

class PermissionChecker:
    @staticmethod
    def has_permission(permissions_data: Dict[str, Any], project: str, tag: str, indicator: str) -> bool:
        project_permissions = permissions_data.get("projectPermissions", {})
        if project not in project_permissions:
            return False
        
        project_indicators = project_permissions[project].get("indicators", [])
        
        if indicator == "*":
            return len(project_indicators) > 0
        
        full_indicator_permission = f"indicators:{tag}:{indicator}:read"
        return full_indicator_permission in project_indicators
        
    @staticmethod
    def has_permissions_for_indicators(permissions_data: Dict[str, Any], project: str, tag: str, indicators: List[str]) -> List[str]:
        project_permissions = permissions_data.get("projectPermissions", {})
        if project not in project_permissions:
            return []
        
        project_indicators = project_permissions[project].get("indicators", [])
        authorized_indicators = []
        
        for indicator in indicators:
            if indicator == "*":
                return PermissionChecker.get_all_authorized_indicators(permissions_data, project, tag)
            
            full_indicator_permission = f"indicators:{tag}:{indicator}:read"
            if full_indicator_permission in project_indicators:
                authorized_indicators.append(indicator)
        
        return authorized_indicators
    
    @staticmethod
    def get_all_authorized_indicators(permissions_data: Dict[str, Any], project: str, tag: str) -> list:
        project_permissions = permissions_data.get("projectPermissions", {})
        if project not in project_permissions:
            return []
        
        project_indicators = project_permissions[project].get("indicators", [])
        indicators = []
        for perm in project_indicators:
            if perm.startswith(f"indicators:{tag}:") and perm.endswith(":read"):
                parts = perm.split(":")
                if len(parts) >= 4:
                    indicators.append(parts[2])
        return indicators