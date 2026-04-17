"""
RBAC Authentication Module

This module provides centralized authentication and authorization functions for API endpoints.
It validates user permissions for both blob containers and table storage before allowing operations.

Key Features:
- Validates blob container permissions (Storage Blob Data Contributor/Owner)
- Validates table storage permissions (Storage Table Data Contributor)
- Provides clear error messages with permission details
- Reuses rbac_helper methods for consistency
- Supports both existing and new container scenarios
- Unified validation logic for all endpoints
- Falls back to storage account key authentication when AZURE_STORAGE_ACCOUNT_KEY is set
"""

import logging
import os
import re
from typing import Optional, Dict, Any, List
from fastapi import HTTPException

# Import RBAC helper and constants
from agents.rbac_helper import RBACHelper, TEMPLATE_TABLES
from agents.logging_config import get_logger

# Create logger for this module
logger = get_logger(__name__)


def sanitize_table_name(name: str) -> str:
    """
    Sanitize table name for Azure Table Storage compliance.
    Azure Table Storage naming rules:
    - Must be alphanumeric (letters and numbers only)
    - Must start with a letter
    - Between 3 and 63 characters
    - No hyphens, underscores, or special characters
    
    Args:
        name: Raw table name that may contain invalid characters
    
    Returns:
        Sanitized table name with invalid characters removed
    """
    # Remove all non-alphanumeric characters
    sanitized = re.sub(r'[^a-zA-Z0-9]', '', name)
    
    # Ensure it starts with a letter
    if sanitized and not sanitized[0].isalpha():
        sanitized = 'T' + sanitized
    
    # Ensure minimum length
    if len(sanitized) < 3:
        sanitized = sanitized + 'Table'
    
    # Ensure maximum length
    if len(sanitized) > 63:
        sanitized = sanitized[:63]
    
    return sanitized


class AuthorizationError(Exception):
    """Custom exception for authorization failures."""
    pass


def validate_container_only_access(
    storage_account_name: str,
    container_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate access to a specific container without table validation.
    
    This is a simplified authentication flow for special operations
    that don't require the full unified validation (no table checks).
    
    Args:
        storage_account_name: Storage account name
        container_name: Name of the container to validate access for
        user_object_id: Azure AD user object ID (optional if group_object_id provided)
        group_object_id: Azure AD group object ID (optional if user_object_id provided)
        resource_group_name: Optional resource group name
    
    Returns:
        Dict with validation results:
        {
            "container": {
                "exists": bool,
                "status": str,
                "permissions": {...}
            },
            "actions_taken": [...]
        }
    
    Raises:
        HTTPException: If validation fails or access is denied
    """
    logger.info(f"🔐 Starting container-only validation for user {user_object_id}, group {group_object_id}, container '{container_name}'")
    
    # Check for storage account key bypass
    storage_account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if storage_account_key:
        logger.info("🔑 Storage account key detected - bypassing RBAC checks")
        return {
            "container": {
                "exists": True,
                "status": "accessible_via_key",
                "permissions": {
                    "access_method": "storage_account_key",
                    "rbac_bypassed": True
                }
            },
            "actions_taken": ["Using storage account key for authentication - RBAC checks bypassed"]
        }
    
    # Validate that at least one ID is provided
    if not user_object_id and not group_object_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_identity",
                "message": "At least one of user_object_id or group_object_id must be provided",
                "container": container_name
            }
        )
    
    result = {
        "container": {},
        "actions_taken": []
    }
    
    try:
        rbac_helper = RBACHelper()
        
        # Check container existence
        #logger.info(f"📦 Checking container '{container_name}' existence")
        container_exists = rbac_helper.check_container_exists(
            storage_account_name=storage_account_name,
            container_name=container_name,
            resource_group_name=resource_group_name
        )
        
        result["container"]["exists"] = container_exists
        
        if not container_exists:
            error_msg = (
                f"Container '{container_name}' does not exist in storage account '{storage_account_name}'."
            )
            logger.error(f"❌ {error_msg}")
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "container_not_found",
                    "message": error_msg,
                    "container": container_name,
                    "storage_account": storage_account_name
                }
            )
        
        # Container exists - check user/group permissions
        #logger.info(f"✅ Container '{container_name}' exists. Checking permissions...")
        
        user_has_permission = False
        group_has_permission = False
        user_permission_check = None
        group_permission_check = None
        
        if user_object_id:
            user_permission_check = rbac_helper.check_user_permissions(
                user_object_id=user_object_id,
                storage_account_name=storage_account_name,
                container_name=container_name,
                resource_group_name=resource_group_name,
                required_roles=["Storage Blob Data Contributor", "Storage Blob Data Owner"],
                principal_type="User"
            )
            user_has_permission = user_permission_check["has_permission"]
            #logger.info(f"User permission check for '{container_name}': {user_has_permission}")
        
        if group_object_id:
            group_permission_check = rbac_helper.check_user_permissions(
                user_object_id=group_object_id,
                storage_account_name=storage_account_name,
                container_name=container_name,
                resource_group_name=resource_group_name,
                required_roles=["Storage Blob Data Contributor", "Storage Blob Data Owner"],
                principal_type="Group"
            )
            group_has_permission = group_permission_check["has_permission"]
            #logger.info(f"Group permission check for '{container_name}': {group_has_permission}")
        
        # At least one must have permission
        if not user_has_permission and not group_has_permission:
            logger.error(f"❌ Neither user {user_object_id} nor group {group_object_id} have permissions on container '{container_name}'")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "access_denied",
                    "message": f"Access denied: Neither user nor group has required permissions on container '{container_name}'",
                    "container": container_name,
                    "storage_account": storage_account_name,
                    "required_roles": ["Storage Blob Data Contributor", "Storage Blob Data Owner"]
                }
            )
        
        # Build permission details
        permission_details = {}
        if user_has_permission:
            permission_details["user"] = {
                "has_permission": True,
                "roles": user_permission_check["user_roles"]
            }
        if group_has_permission:
            permission_details["group"] = {
                "has_permission": True,
                "roles": group_permission_check["user_roles"]
            }
        
        logger.info(f"✅ Container '{container_name}' permissions verified: User={user_has_permission}, Group={group_has_permission}")
        result["container"]["status"] = "exists_with_permissions"
        result["container"]["permissions"] = permission_details
        result["actions_taken"].append(f"Container-only validation successful for '{container_name}'")
        
        return result
        
    except HTTPException:
        raise
    except Exception as ex:
        logger.error(f"Error during container-only validation: {str(ex)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "validation_error",
                "message": f"Error validating container access: {str(ex)}",
                "container": container_name
            }
        )


def user_authentication(
    storage_account_name: str,
    app_id: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None,
    endpoint_type: str = "operation"
) -> Dict[str, Any]:
    """
    Unified validation and setup logic for all API endpoints.
    
    FLOW:
    1. Check container existence
       - If container exists: check user/group permissions
         - If permissions present: proceed to table validation
         - If permissions NOT present: DENY ACCESS and check for orphaned table permissions
       - If container does NOT exist: THROW ERROR (do not create, applies to ALL endpoints)
    
    2. Check table existence (only if user/group has container permissions)
       - If tables exist: check user/group permissions
         - If permissions present: validation complete
         - If permissions NOT present: assign table permissions
       - If tables do NOT exist:
         - For "create" endpoint: tables will be created by orchestrator
         - For "operation" endpoints: THROW ERROR
    
    Args:
        storage_account_name: Storage account name
        app_id: Application ID (used as container name and table suffix)
        user_object_id: Azure AD user object ID (optional if group_object_id provided)
        group_object_id: Azure AD group object ID (optional if user_object_id provided)
        resource_group_name: Optional resource group name
        endpoint_type: Type of endpoint - "create" or "operation"
                      - "create": createApplicationId endpoint (can create tables)
                      - "operation": other endpoints (require existing resources)
    
    Returns:
        Dict with validation results:
        {
            "container": {
                "exists": bool,
                "status": str,
                "permissions": {...}
            },
            "tables": {
                "exist": bool,
                "status": str,
                "existing_tables": [...],
                "missing_tables": [...],
                "permissions": {...}
            },
            "actions_taken": [...]
        }
    
    Raises:
        HTTPException: If validation fails or access is denied
    """
    logger.info(f"🔐 Starting unified validation for user {user_object_id}, group {group_object_id}, app {app_id}, endpoint_type: {endpoint_type}")
    
     # Validate that at least one ID is provided
    if not user_object_id and not group_object_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_identity",
                "message": "At least one of user_object_id or group_object_id must be provided",
                "app_id": app_id
            }
        )
    
    result = {
        "container": {},
        "tables": {},
        "actions_taken": []
    }
    
    try:
        rbac_helper = RBACHelper()
        
        # ==========================================
        # STEP 1: CONTAINER VALIDATION
        # ==========================================
        logger.info(f"📦 Checking container '{app_id}' existence")
        container_exists = rbac_helper.check_container_exists(
            storage_account_name=storage_account_name,
            container_name=app_id,
            resource_group_name=resource_group_name
        )
        
        result["container"]["exists"] = container_exists
        
        if container_exists:
            # Container exists - check user/group permissions
            logger.info(f"✅ Container '{app_id}' exists. Checking permissions...")
            
            # Check permissions for both user and group (if provided)
            user_has_permission = False
            group_has_permission = False
            user_permission_check = None
            group_permission_check = None
            
            if user_object_id:
                user_permission_check = rbac_helper.check_user_permissions(
                    user_object_id=user_object_id,
                    storage_account_name=storage_account_name,
                    container_name=app_id,
                    resource_group_name=resource_group_name,
                    required_roles=["Storage Blob Data Contributor", "Storage Blob Data Owner"],
                    principal_type="User"
                )
                user_has_permission = user_permission_check["has_permission"]
                logger.info(f"User permission check: {user_has_permission}")
            
            if group_object_id:
                group_permission_check = rbac_helper.check_user_permissions(
                    user_object_id=group_object_id,
                    storage_account_name=storage_account_name,
                    container_name=app_id,
                    resource_group_name=resource_group_name,
                    required_roles=["Storage Blob Data Contributor", "Storage Blob Data Owner"],
                    principal_type="Group"
                )
                group_has_permission = group_permission_check["has_permission"]
                logger.info(f"Group permission check: {group_has_permission}")
            
            # At least one must have permission
            if not user_has_permission and not group_has_permission:
                # Neither user nor group have container permissions - DENY ACCESS
                logger.error(f"❌ Neither user {user_object_id} nor group {group_object_id} have container permissions")
                
                # Check if they have table permissions (they shouldn't if no container access)
                cloned_table_names = [sanitize_table_name(f"{table}{app_id}") for table in TEMPLATE_TABLES]
                table_check = rbac_helper.check_tables_exist(
                    storage_account_name=storage_account_name,
                    table_names=cloned_table_names,
                    resource_group_name=resource_group_name
                )
                
                # If tables exist, check permissions and remove if present
                orphaned_permissions_removed = []
                if table_check["existing_tables"]:
                    logger.info(f"🔍 Checking for orphaned table permissions...")
                    
                    # Check and remove user table permissions if user was provided
                    if user_object_id:
                        user_table_permission_check = _check_table_permissions(
                            rbac_helper=rbac_helper,
                            user_object_id=user_object_id,
                            storage_account_name=storage_account_name,
                            resource_group_name=resource_group_name,
                            table_names=table_check["existing_tables"],
                            principal_type="User"
                        )
                        
                        user_accessible_tables = user_table_permission_check.get("accessible_tables", [])
                        if user_accessible_tables:
                            logger.warning(f"⚠️ Removing orphaned table permissions for user on {len(user_accessible_tables)} tables")
                            try:
                                removal_result = rbac_helper.remove_table_permissions(
                                    user_object_id=user_object_id,
                                    storage_account_name=storage_account_name,
                                    table_names=user_accessible_tables,
                                    resource_group_name=resource_group_name,
                                    role_name="Storage Table Data Contributor",
                                    principal_type="User"
                                )
                                orphaned_permissions_removed.append(f"user:{len(user_accessible_tables)} tables")
                            except Exception as ex:
                                logger.error(f"Failed to remove user orphaned permissions: {ex}")
                    
                    # Check and remove group table permissions if group was provided
                    if group_object_id:
                        group_table_permission_check = _check_table_permissions(
                            rbac_helper=rbac_helper,
                            user_object_id=group_object_id,
                            storage_account_name=storage_account_name,
                            resource_group_name=resource_group_name,
                            table_names=table_check["existing_tables"],
                            principal_type="Group"
                        )
                        
                        group_accessible_tables = group_table_permission_check.get("accessible_tables", [])
                        if group_accessible_tables:
                            logger.warning(f"⚠️ Removing orphaned table permissions for group on {len(group_accessible_tables)} tables")
                            try:
                                removal_result = rbac_helper.remove_table_permissions(
                                    user_object_id=group_object_id,
                                    storage_account_name=storage_account_name,
                                    table_names=group_accessible_tables,
                                    resource_group_name=resource_group_name,
                                    role_name="Storage Table Data Contributor",
                                    principal_type="Group"
                                )
                                orphaned_permissions_removed.append(f"group:{len(group_accessible_tables)} tables")
                            except Exception as ex:
                                logger.error(f"Failed to remove group orphaned permissions: {ex}")
                
                # Return access denied error with details
                error_detail = {
                    "error": "access_denied",
                    "message": f"Access denied: Neither user nor group has required permissions on container '{app_id}'",
                    "container": app_id,
                    "storage_account": storage_account_name,
                    "required_roles": ["Storage Blob Data Contributor", "Storage Blob Data Owner"]
                }
                
                if orphaned_permissions_removed:
                    error_detail["orphaned_permissions_cleaned"] = orphaned_permissions_removed
                
                logger.error(f"❌ Access denied for container {app_id}")
                raise HTTPException(status_code=403, detail=error_detail)
            
            # At least one (user or group) HAS container permissions
            permission_details = {}
            if user_has_permission:
                permission_details["user"] = {
                    "has_permission": True,
                    "roles": user_permission_check["user_roles"]
                }
            if group_has_permission:
                permission_details["group"] = {
                    "has_permission": True,
                    "roles": group_permission_check["user_roles"]
                }
            
            logger.info(f"✅ Container permissions verified: User={user_has_permission}, Group={group_has_permission}")
            result["container"]["status"] = "exists_with_permissions"
            result["container"]["permissions"] = permission_details
            
        else:
            # Container does NOT exist - THROW ERROR for ALL endpoints
            error_msg = (
                f"Container '{app_id}' does not exist in storage account '{storage_account_name}'. "
                f"Please contact an administrator to create the container and assign appropriate permissions."
            )
            logger.error(f"❌ {error_msg}")
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "container_not_found",
                    "message": error_msg,
                    "container": app_id,
                    "storage_account": storage_account_name,
                    "required_action": "Contact administrator to create container and assign permissions"
                }
            )
        
        # ==========================================
        # STEP 2: TABLE VALIDATION
        # ==========================================
        logger.info(f"📊 Checking tables for app {app_id}")
        cloned_table_names = [sanitize_table_name(f"{table}{app_id}") for table in TEMPLATE_TABLES]
        
        table_check = rbac_helper.check_tables_exist(
            storage_account_name=storage_account_name,
            table_names=cloned_table_names,
            resource_group_name=resource_group_name
        )
        
        existing_tables = table_check["existing_tables"]
        missing_tables = table_check["missing_tables"]
        
        logger.info(
            f"Table check: {len(existing_tables)}/{len(cloned_table_names)} exist. "
            f"Existing: {existing_tables}, Missing: {missing_tables}"
        )
        
        result["tables"]["existing_tables"] = existing_tables
        result["tables"]["missing_tables"] = missing_tables
        result["tables"]["exist"] = len(existing_tables) > 0
        
        if existing_tables:
            # Tables exist - check and assign permissions if needed for both user and group
            logger.info(f"✅ {len(existing_tables)} tables exist. Checking permissions...")
            
            # Check permissions for user and/or group
            user_table_accessible = []
            group_table_accessible = []
            tables_needing_user_permissions = []
            tables_needing_group_permissions = []
            
            if user_object_id:
                user_permission_check = _check_table_permissions(
                    rbac_helper=rbac_helper,
                    user_object_id=user_object_id,
                    storage_account_name=storage_account_name,
                    resource_group_name=resource_group_name,
                    table_names=existing_tables,
                    principal_type="User"
                )
                
                user_table_accessible = user_permission_check.get("accessible_tables", [])
                tables_needing_user_permissions = user_permission_check.get("inaccessible_tables", [])
                logger.info(f"User table access: {len(user_table_accessible)}/{len(existing_tables)} accessible")
            
            if group_object_id:
                group_permission_check = _check_table_permissions(
                    rbac_helper=rbac_helper,
                    user_object_id=group_object_id,
                    storage_account_name=storage_account_name,
                    resource_group_name=resource_group_name,
                    table_names=existing_tables,
                    principal_type="Group"
                )
                
                group_table_accessible = group_permission_check.get("accessible_tables", [])
                tables_needing_group_permissions = group_permission_check.get("inaccessible_tables", [])
                logger.info(f"Group table access: {len(group_table_accessible)}/{len(existing_tables)} accessible")
            
            # Assign permissions where needed
            assignment_results = {}
            
            if tables_needing_user_permissions and user_object_id:
                logger.info(f"🔨 Assigning table permissions for user on {len(tables_needing_user_permissions)} tables...")
                user_assignment_result = rbac_helper.assign_table_permissions(
                    user_object_id=user_object_id,
                    storage_account_name=storage_account_name,
                    resource_group_name=resource_group_name,
                    role_name="Storage Table Data Contributor",
                    table_names=tables_needing_user_permissions,
                    principal_type="User"
                )
                assignment_results["user"] = user_assignment_result
                result["actions_taken"].append(f"assigned_user_table_permissions_for_{len(tables_needing_user_permissions)}_tables")
            
            if tables_needing_group_permissions and group_object_id:
                logger.info(f"🔨 Assigning table permissions for group on {len(tables_needing_group_permissions)} tables...")
                group_assignment_result = rbac_helper.assign_table_permissions(
                    user_object_id=group_object_id,
                    storage_account_name=storage_account_name,
                    resource_group_name=resource_group_name,
                    role_name="Storage Table Data Contributor",
                    table_names=tables_needing_group_permissions,
                    principal_type="Group"
                )
                assignment_results["group"] = group_assignment_result
                result["actions_taken"].append(f"assigned_group_table_permissions_for_{len(tables_needing_group_permissions)}_tables")
            
            # Update result with permission details
            result["tables"]["permissions"] = {
                "has_permission": True,
                "assignment_results": assignment_results,
                "user_accessible": user_table_accessible,
                "group_accessible": group_table_accessible
            }
            
            result["tables"]["status"] = "exist_with_permissions"
            
        else:
            # No tables exist
            logger.info(f"❌ No tables exist for app {app_id}")
            
            if endpoint_type == "create":
                # CREATE endpoint - tables will be created by orchestrator
                logger.info(f"📝 Tables will be created by orchestrator")
                result["tables"]["status"] = "will_be_created"
                result["actions_taken"].append("tables_to_be_cloned")
                
            else:
                # OPERATION endpoints - tables must exist
                error_msg = (
                    f"Tables for application '{app_id}' do not exist. "
                    f"The initialization is not complete. "
                    f"Please initialize the application first using the /createApplicationId endpoint."
                )
                logger.error(f"❌ {error_msg}")
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "tables_not_found",
                        "message": error_msg,
                        "app_id": app_id,
                        "required_action": "Call /createApplicationId first to initialize tables"
                    }
                )
        
        # ==========================================
        # VALIDATION COMPLETE
        # ==========================================
        logger.info(
            f"✅ Unified validation completed successfully. "
            f"Container: {result['container']['status']}, "
            f"Tables: {result['tables'].get('status', 'pending')}, "
            f"Actions: {result['actions_taken']}"
        )
        
        return result
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
        
    except Exception as ex:
        error_msg = f"Validation error for user {user_object_id} / group {group_object_id}, app {app_id}: {str(ex)}"
        logger.error(f"❌ {error_msg}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "validation_error",
                "message": error_msg,
                "user_id": user_object_id,
                "group_id": group_object_id,
                "app_id": app_id
            }
        )


def _check_table_permissions(
    rbac_helper: RBACHelper,
    user_object_id: str,
    storage_account_name: str,
    resource_group_name: Optional[str] = None,
    table_names: Optional[List[str]] = None,
    principal_type: str = "User"
) -> Dict[str, Any]:
    """
    Internal helper to check table storage permissions at storage account or table level.
    
    This function checks if the user/group has Storage Table Data Contributor role
    at the storage account level OR at individual table level if table_names provided.
    
    Args:
        rbac_helper: Initialized RBACHelper instance
        user_object_id: Azure AD user object ID or group object ID
        storage_account_name: Storage account name
        resource_group_name: Optional resource group name
        table_names: Optional list of specific table names to check at table level
        principal_type: Type of principal - "User" or "Group"
        
    Returns:
        Dict with permission check results including per-table status if table_names provided
    """
    try:
        # Get resource group if not provided
        if not resource_group_name:
            resource_group_name = rbac_helper._get_storage_account_resource_group(storage_account_name)
        
        # Required role for table access
        required_role = "Storage Table Data Contributor"
        
        # If specific tables provided, check table-level permissions
        if table_names:
            logger.info(f"Checking table-level permissions for {principal_type.lower()} on {len(table_names)} tables")
            
            table_results = {}
            all_tables_accessible = True
            accessible_tables = []
            inaccessible_tables = []
            
            for table_name in table_names:
                # Build table-level scope
                table_scope = (
                    f"/subscriptions/{rbac_helper.subscription_id}/"
                    f"resourceGroups/{resource_group_name}/"
                    f"providers/Microsoft.Storage/storageAccounts/{storage_account_name}/"
                    f"tableServices/default/tables/{table_name}"
                )
                
                logger.debug(f"Checking permissions for table '{table_name}' at scope: {table_scope}")
                
                try:
                    # List role assignments for the user at table scope
                    role_assignments = list(rbac_helper.auth_client.role_assignments.list_for_scope(
                        scope=table_scope,
                        filter=f"principalId eq '{user_object_id}'"
                    ))
                    
                    # Get required role ID
                    required_role_id = rbac_helper._get_role_definition_id(required_role, table_scope)
                    
                    # Check if user has the required role for this table
                    table_has_permission = False
                    table_roles = []
                    
                    for assignment in role_assignments:
                        role_def_id = assignment.role_definition_id
                        if required_role_id in role_def_id:
                            table_has_permission = True
                            table_roles.append(required_role)
                        else:
                            # Try to get the role name for logging
                            try:
                                role_name = role_def_id.split('/')[-1]
                                table_roles.append(role_name)
                            except:
                                table_roles.append(role_def_id)
                    
                    table_results[table_name] = {
                        "has_permission": table_has_permission,
                        "roles": table_roles
                    }
                    
                    if table_has_permission:
                        accessible_tables.append(table_name)
                        logger.debug(f"✅ User has access to table '{table_name}'")
                    else:
                        inaccessible_tables.append(table_name)
                        all_tables_accessible = False
                        logger.warning(f"❌ User lacks access to table '{table_name}'")
                        
                except Exception as table_ex:
                    logger.error(f"Error checking permissions for table '{table_name}': {str(table_ex)}")
                    table_results[table_name] = {
                        "has_permission": False,
                        "roles": [],
                        "error": str(table_ex)
                    }
                    inaccessible_tables.append(table_name)
                    all_tables_accessible = False
            
            result = {
                "has_permission": all_tables_accessible,
                "user_roles": [required_role] if all_tables_accessible else [],
                "required_role": required_role,
                "scope": "table-level",
                "storage_account": storage_account_name,
                "tables_checked": len(table_names),
                "accessible_tables": accessible_tables,
                "inaccessible_tables": inaccessible_tables,
                "table_details": table_results
            }
            
            logger.info(
                f"Table-level permission check: {len(accessible_tables)}/{len(table_names)} tables accessible"
            )
            return result
        
        else:
            # No specific tables - check storage account level permissions
            logger.info("Checking storage account level table permissions")
            
            # Build storage account scope
            scope = (
                f"/subscriptions/{rbac_helper.subscription_id}/"
                f"resourceGroups/{resource_group_name}/"
                f"providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
            )
            
            logger.debug(f"Checking table permissions at scope: {scope}")
            
            # List role assignments for the user at storage account scope
            role_assignments = rbac_helper.auth_client.role_assignments.list_for_scope(
                scope=scope,
                filter=f"principalId eq '{user_object_id}'"
            )
            
            # Get required role ID
            required_role_id = rbac_helper._get_role_definition_id(required_role, scope)
            
            # Check if user has the required role
            user_roles = []
            has_permission = False
            
            for assignment in role_assignments:
                role_def_id = assignment.role_definition_id
                if required_role_id in role_def_id:
                    has_permission = True
                    user_roles.append(required_role)
                else:
                    # Try to get the role name for logging
                    try:
                        role_name = role_def_id.split('/')[-1]
                        user_roles.append(role_name)
                    except:
                        user_roles.append(role_def_id)
            
            result = {
                "has_permission": has_permission,
                "user_roles": user_roles,
                "required_role": required_role,
                "scope": "storage_account",
                "storage_account": storage_account_name
            }
            
            logger.debug(f"Table permission check result: {result}")
            return result
        
    except Exception as ex:
        logger.error(f"Error checking table permissions: {str(ex)}")
        # Return permission denied on error
        return {
            "has_permission": False,
            "user_roles": [],
            "required_role": "Storage Table Data Contributor",
            "error": str(ex)
        }
