"""
RBAC Helper Module for Azure Storage Access Control

This module provides functions to check and assign Azure RBAC roles for storage access.
Follows Azure best practices:
- Uses DefaultAzureCredential for authentication
- Implements least privilege principle
- Assigns roles at appropriate scopes (container/storage account level)
- Proper error handling and logging
"""

import uuid
import os
from typing import Optional, Dict, Any, List
from azure.identity import DefaultAzureCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
from azure.mgmt.storage import StorageManagementClient
from azure.storage.blob import BlobServiceClient, ContainerClient, PublicAccess
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceExistsError, HttpResponseError
from dotenv import load_dotenv

# Import logging configuration
from agents.logging_config import get_logger

# Import tracing configuration
from agents.tracing_config import (
    get_tracer,
    add_span_attributes,
    record_error_details
)
from opentelemetry.trace import Status, StatusCode

# Load environment variables
load_dotenv()

# Create logger for this module
logger = get_logger(__name__)

# Azure built-in role definitions
ROLE_DEFINITIONS = {
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "Storage Table Data Contributor": "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3",
    "Storage Blob Data Owner": "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",
    "Storage Table Data Reader": "76199698-9eea-4c19-bc75-cec21354c6b0"
}

# Template table names (constants)
TEMPLATE_TABLES = [
    "AppDetails",
    "IntegrationDependency",
    "MsSqlDB",
    "OracleDB",
    "InfrastructureDetails"
]


class RBACHelper:
    """Helper class for managing Azure RBAC role assignments for storage resources."""
    
    def __init__(self, subscription_id: Optional[str] = None):
        """
        Initialize RBAC helper with Azure credentials.
        
        Args:
            subscription_id: Azure subscription ID. If not provided, reads from AZURE_SUBSCRIPTION_ID env var.
        """
        self.tracer = get_tracer()
        self.credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        self.subscription_id = subscription_id or os.getenv("AZURE_SUBSCRIPTION_ID")
        
        if not self.subscription_id:
            raise ValueError("AZURE_SUBSCRIPTION_ID environment variable must be set")
        
        # Initialize management clients with appropriate API versions
        self.auth_client = AuthorizationManagementClient(
            credential=self.credential,
            subscription_id=self.subscription_id,
            api_version="2022-04-01"
        )
        
        self.storage_client = StorageManagementClient(
            credential=self.credential,
            subscription_id=self.subscription_id
        )
        
        # logger.info(f"RBAC Helper initialized for subscription: {self.subscription_id}")
    
    def check_container_exists(
        self,
        storage_account_name: str,
        container_name: str,
        resource_group_name: Optional[str] = None
    ) -> bool:
        """
        Check if a blob container exists in the specified storage account.
        Uses DefaultAzureCredential for authentication (supports Managed Identity).
        
        Args:
            storage_account_name: Name of the storage account
            container_name: Name of the container to check
            resource_group_name: Resource group name (optional, will auto-discover if not provided)
            
        Returns:
            bool: True if container exists, False otherwise
        """
        with self.tracer.start_as_current_span("check_container_exists") as span:
            add_span_attributes(span, {
                "storage_account": storage_account_name,
                "container_name": container_name,
                "resource_group": resource_group_name or "auto-discover"
            })
            
            try:
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                # Create blob service client using DefaultAzureCredential (Managed Identity support)
                account_url = f"https://{storage_account_name}.blob.core.windows.net"
                blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=self.credential  # Uses DefaultAzureCredential
                )
                
                # Check if container exists
                container_client = blob_service_client.get_container_client(container_name)
                exists = container_client.exists()
                
                #logger.info(f"Container '{container_name}' exists: {exists} (using Managed Identity)")
                add_span_attributes(span, {
                    "container_exists": exists,
                    "auth_method": "DefaultAzureCredential"
                })
                span.set_status(Status(StatusCode.OK))
                
                return exists
                
            except Exception as ex:
                logger.error(f"Error checking container existence: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def create_container(
        self,
        storage_account_name: str,
        container_name: str,
        resource_group_name: Optional[str] = None,
        public_access: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a blob container in the specified storage account.
        Uses DefaultAzureCredential for authentication (supports Managed Identity).
        
        Args:
            storage_account_name: Name of the storage account
            container_name: Name of the container to create
            resource_group_name: Resource group name (optional, will auto-discover if not provided)
            public_access: Public access level (None/'private', 'blob', 'container'). 
                          Defaults to None (private container).
            
        Returns:
            Dict containing container creation details
        """
        with self.tracer.start_as_current_span("create_container") as span:
            add_span_attributes(span, {
                "storage_account": storage_account_name,
                "container_name": container_name,
                "public_access": public_access or "private"
            })
            
            try:
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                # Create blob service client using DefaultAzureCredential (Managed Identity support)
                account_url = f"https://{storage_account_name}.blob.core.windows.net"
                blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=self.credential  # Uses DefaultAzureCredential
                )
                
                # Map public_access string to PublicAccess enum
                access_level = None
                if public_access:
                    access_map = {
                        'blob': PublicAccess.Blob,
                        'container': PublicAccess.Container,
                        'private': None,
                        'none': None
                    }
                    access_level = access_map.get(public_access.lower())
                
                # Create container (public_access=None means private)
                container_client = blob_service_client.create_container(
                    name=container_name,
                    public_access=access_level
                )
                
                result = {
                    "status": "created",
                    "container_name": container_name,
                    "storage_account": storage_account_name,
                    "resource_group": resource_group_name,
                    "auth_method": "DefaultAzureCredential"
                }
                
                logger.info(f"Container '{container_name}' created successfully (using Managed Identity)")
                add_span_attributes(span, {
                    "creation_status": "success",
                    "auth_method": "DefaultAzureCredential"
                })
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except ResourceExistsError:
                logger.info(f"Container '{container_name}' already exists")
                return {
                    "status": "already_exists",
                    "container_name": container_name,
                    "storage_account": storage_account_name,
                    "resource_group": resource_group_name,
                    "auth_method": "DefaultAzureCredential"
                }
                
            except Exception as ex:
                logger.error(f"Error creating container: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def check_user_permissions(
        self,
        user_object_id: str,
        storage_account_name: str,
        container_name: str,
        resource_group_name: Optional[str] = None,
        required_roles: Optional[list] = None,
        principal_type: str = "User"
    ) -> Dict[str, Any]:
        """
        Check if a user or group has the required permissions on a storage container.
        
        Args:
            user_object_id: Azure AD user object ID or group object ID
            storage_account_name: Name of the storage account
            container_name: Name of the container
            resource_group_name: Resource group name (optional)
            required_roles: List of required role names (defaults to Storage Blob Data Contributor)
            principal_type: Type of principal - "User" or "Group"
            
        Returns:
            Dict with permission check results
        """
        with self.tracer.start_as_current_span("check_user_permissions") as span:
            add_span_attributes(span, {
                "user_object_id": user_object_id,
                "storage_account": storage_account_name,
                "container_name": container_name
            })
            
            try:
                if required_roles is None:
                    required_roles = ["Storage Blob Data Contributor"]
                
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                # Build scope for the container
                container_scope = (
                    f"/subscriptions/{self.subscription_id}"
                    f"/resourceGroups/{resource_group_name}"
                    f"/providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
                    f"/blobServices/default/containers/{container_name}"
                )
                
                # List role assignments at the container scope
                role_assignments = list(self.auth_client.role_assignments.list_for_scope(
                    scope=container_scope
                ))
                
                # Check if user has any of the required roles
                user_roles = []
                for assignment in role_assignments:
                    if assignment.principal_id == user_object_id:
                        # Get role definition to check role name
                        role_def = self.auth_client.role_definitions.get_by_id(
                            assignment.role_definition_id
                        )
                        user_roles.append(role_def.role_name)
                
                has_permission = any(role in user_roles for role in required_roles)
                
                result = {
                    "has_permission": has_permission,
                    "user_roles": user_roles,
                    "required_roles": required_roles,
                    "scope": container_scope
                }
                
                logger.info(f"User permission check: {has_permission}, Roles: {user_roles}")
                add_span_attributes(span, {
                    "has_permission": has_permission,
                    "user_roles_count": len(user_roles)
                })
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except Exception as ex:
                logger.error(f"Error checking user permissions: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def assign_container_permissions(
        self,
        user_object_id: str,
        storage_account_name: str,
        container_name: str,
        resource_group_name: Optional[str] = None,
        role_name: str = "Storage Blob Data Contributor",
        principal_type: str = "User"
    ) -> Dict[str, Any]:
        """
        Assign container-level permissions to a user or group.
        
        Args:
            user_object_id: Azure AD user object ID or group object ID
            storage_account_name: Name of the storage account
            container_name: Name of the container
            resource_group_name: Resource group name (optional)
            role_name: Role to assign (default: Storage Blob Data Contributor)
            principal_type: Type of principal - "User" or "Group"
            
        Returns:
            Dict containing role assignment details
        """
        with self.tracer.start_as_current_span("assign_container_permissions") as span:
            add_span_attributes(span, {
                "user_object_id": user_object_id,
                "storage_account": storage_account_name,
                "container_name": container_name,
                "role_name": role_name
            })
            
            try:
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                # Build scope for the container
                container_scope = (
                    f"/subscriptions/{self.subscription_id}"
                    f"/resourceGroups/{resource_group_name}"
                    f"/providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
                    f"/blobServices/default/containers/{container_name}"
                )
                
                # Get role definition ID
                role_definition_id = self._get_role_definition_id(role_name, container_scope)
                
                # Create role assignment
                role_assignment_name = str(uuid.uuid4())
                role_assignment_params = RoleAssignmentCreateParameters(
                    role_definition_id=role_definition_id,
                    principal_id=user_object_id,
                    principal_type=principal_type
                )
                
                assignment = self.auth_client.role_assignments.create(
                    scope=container_scope,
                    role_assignment_name=role_assignment_name,
                    parameters=role_assignment_params
                )
                
                result = {
                    "status": "assigned",
                    "role": role_name,
                    "scope": container_scope,
                    "assignment_id": assignment.id
                }
                
                logger.info(f"Assigned '{role_name}' to {principal_type.lower()} {user_object_id} on container '{container_name}'")
                add_span_attributes(span, {"assignment_status": "success"})
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except HttpResponseError as ex:
                if "RoleAssignmentExists" in str(ex):
                    logger.info(f"Role assignment already exists for user on container '{container_name}'")
                    return {
                        "status": "already_exists",
                        "role": role_name,
                        "scope": container_scope
                    }
                else:
                    logger.error(f"Error assigning container permissions: {str(ex)}")
                    record_error_details(span, type(ex).__name__, str(ex), False)
                    span.set_status(Status(StatusCode.ERROR, str(ex)))
                    raise
            except Exception as ex:
                logger.error(f"Error assigning container permissions: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def assign_table_permissions(
        self,
        user_object_id: str,
        storage_account_name: str,
        resource_group_name: Optional[str] = None,
        role_name: str = "Storage Table Data Contributor",
        table_names: Optional[list] = None,
        principal_type: str = "User"
    ) -> Dict[str, Any]:
        """
        Assign table storage permissions to a user or group at table-level or storage account level.
        
        ⚠️ IMPORTANT: Table-level RBAC assignments can take up to 10 minutes to propagate.
        
        Args:
            user_object_id: Azure AD user object ID or group object ID
            storage_account_name: Name of the storage account
            resource_group_name: Resource group name (optional)
            role_name: Role to assign (default: Storage Table Data Contributor)
            table_names: List of specific table names. If None, assigns at storage account level (all tables).
            principal_type: Type of principal - "User" or "Group"
            
        Returns:
            Dict containing role assignment details with per-table status if table_names provided
        """
        with self.tracer.start_as_current_span("assign_table_permissions") as span:
            add_span_attributes(span, {
                "user_object_id": user_object_id,
                "storage_account": storage_account_name,
                "role_name": role_name,
                "scope_level": "table" if table_names else "storage_account",
                "table_count": len(table_names) if table_names else 0
            })

            try:
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                # If specific tables provided, assign at table level (GRANULAR RBAC)
                if table_names:
                    logger.info(f"Assigning table-level permissions for {len(table_names)} tables")
                    assignments = []
                    
                    for table_name in table_names:
                        # Build scope for the specific table
                        # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Storage/storageAccounts/{account}/tableServices/default/tables/{table}
                        table_scope = (
                            f"/subscriptions/{self.subscription_id}"
                            f"/resourceGroups/{resource_group_name}"
                            f"/providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
                            f"/tableServices/default/tables/{table_name}"
                        )
                        
                        try:
                            # Get role definition ID
                            role_definition_id = self._get_role_definition_id(role_name, table_scope)
                            
                            # Create role assignment
                            role_assignment_name = str(uuid.uuid4())
                            role_assignment_params = RoleAssignmentCreateParameters(
                                role_definition_id=role_definition_id,
                                principal_id=user_object_id,
                                principal_type=principal_type
                            )
                            
                            assignment = self.auth_client.role_assignments.create(
                                scope=table_scope,
                                role_assignment_name=role_assignment_name,
                                parameters=role_assignment_params
                            )
                            
                            assignments.append({
                                "table_name": table_name,
                                "status": "assigned",
                                "scope": table_scope,
                                "assignment_id": assignment.id
                            })
                            
                            logger.info(f"✅ Assigned '{role_name}' to {principal_type.lower()} on table '{table_name}'")
                            
                        except HttpResponseError as ex:
                            if "RoleAssignmentExists" in str(ex):
                                logger.info(f"Role assignment already exists for table '{table_name}'")
                                assignments.append({
                                    "table_name": table_name,
                                    "status": "already_exists",
                                    "scope": table_scope
                                })
                            else:
                                logger.error(f"❌ Error assigning permissions for table '{table_name}': {str(ex)}")
                                assignments.append({
                                    "table_name": table_name,
                                    "status": "error",
                                    "error": str(ex)
                                })
                    
                    result = {
                        "status": "completed",
                        "role": role_name,
                        "scope_level": "table",
                        "assignments": assignments,
                        "total_tables": len(table_names),
                        "successful": len([a for a in assignments if a["status"] in ["assigned", "already_exists"]]),
                        "failed": len([a for a in assignments if a["status"] == "error"]),
                        "propagation_note": "⚠️ Role assignments may take up to 10 minutes to take effect"
                    }
                    
                    logger.info(
                        f"Table-level permissions for {principal_type.lower()}: {result['successful']}/{result['total_tables']} successful. "
                        f"⚠️ Changes may take up to 10 minutes to propagate."
                    )
                    add_span_attributes(span, {
                        "assignment_status": "success",
                        "successful_count": result['successful'],
                        "failed_count": result['failed']
                    })
                    span.set_status(Status(StatusCode.OK))
                    
                    return result
                
                else:
                    # No specific tables - assign at storage account level (ALL TABLES)
                    logger.info("No specific tables provided. Assigning at storage account level (all tables).")
                    
                    account_scope = (
                        f"/subscriptions/{self.subscription_id}"
                        f"/resourceGroups/{resource_group_name}"
                        f"/providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
                    )
                    
                    # Get role definition ID
                    role_definition_id = self._get_role_definition_id(role_name, account_scope)
                    
                    # Create role assignment
                    role_assignment_name = str(uuid.uuid4())
                    role_assignment_params = RoleAssignmentCreateParameters(
                        role_definition_id=role_definition_id,
                        principal_id=user_object_id,
                        principal_type=principal_type
                    )
                    
                    assignment = self.auth_client.role_assignments.create(
                        scope=account_scope,
                        role_assignment_name=role_assignment_name,
                        parameters=role_assignment_params
                    )
                    
                    result = {
                        "status": "assigned",
                        "role": role_name,
                        "scope": account_scope,
                        "scope_level": "storage_account",
                        "assignment_id": assignment.id,
                        "note": f"⚠️ {principal_type} has access to ALL tables in this storage account",
                        "propagation_note": "⚠️ Role assignments may take up to 10 minutes to take effect"
                    }
                    
                    logger.info(f"Assigned '{role_name}' to {principal_type.lower()} at storage account level (all tables)")
                    add_span_attributes(span, {"assignment_status": "success"})
                    span.set_status(Status(StatusCode.OK))
                    
                    return result
                
            except HttpResponseError as ex:
                if "RoleAssignmentExists" in str(ex) and not table_names:
                    logger.info(f"Role assignment already exists at storage account level")
                    return {
                        "status": "already_exists",
                        "role": role_name,
                        "scope_level": "storage_account",
                        "note": f"{principal_type} already has access to ALL tables"
                    }
                else:
                    logger.error(f"Error assigning table permissions: {str(ex)}")
                    record_error_details(span, type(ex).__name__, str(ex), False)
                    span.set_status(Status(StatusCode.ERROR, str(ex)))
                    raise
            except Exception as ex:
                logger.error(f"Error assigning table permissions: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def _get_storage_account_resource_group(self, storage_account_name: str) -> str:
        """
        Find the resource group for a storage account.
        
        Args:
            storage_account_name: Name of the storage account
            
        Returns:
            Resource group name
        """
        with self.tracer.start_as_current_span("get_storage_account_resource_group") as span:
            try:
                # List all storage accounts in the subscription
                storage_accounts = self.storage_client.storage_accounts.list()
                
                for account in storage_accounts:
                    if account.name == storage_account_name:
                        # Extract resource group from the account ID
                        # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/...
                        resource_group = account.id.split('/')[4]
                        # logger.info(f"Found storage account '{storage_account_name}' in resource group '{resource_group}'")
                        add_span_attributes(span, {"resource_group": resource_group})
                        return resource_group
                
                error_msg = f"Storage account '{storage_account_name}' not found in subscription"
                logger.error(error_msg)
                span.set_status(Status(StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)
                
            except Exception as ex:
                logger.error(f"Error finding storage account resource group: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                raise
    
    def remove_table_permissions(
        self,
        user_object_id: str,
        storage_account_name: str,
        table_names: List[str],
        resource_group_name: Optional[str] = None,
        role_name: str = "Storage Table Data Contributor",
        principal_type: str = "User"
    ) -> Dict[str, Any]:
        """
        Remove table storage permissions from a user or group for specific tables.
        
        This is used to clean up orphaned permissions where a user/group has table access
        but no container access.
        
        Args:
            user_object_id: Azure AD user object ID or group object ID
            storage_account_name: Name of the storage account
            table_names: List of table names to remove permissions from
            resource_group_name: Resource group name (optional)
            role_name: Role to remove (default: Storage Table Data Contributor)
            principal_type: Type of principal - "User" or "Group"
            
        Returns:
            Dict containing removal results with per-table status
        """
        with self.tracer.start_as_current_span("remove_table_permissions") as span:
            add_span_attributes(span, {
                "user_object_id": user_object_id,
                "storage_account": storage_account_name,
                "role_name": role_name,
                "table_count": len(table_names)
            })
            
            try:
                # If resource group not provided, find it
                if not resource_group_name:
                    resource_group_name = self._get_storage_account_resource_group(storage_account_name)
                
                logger.info(f"Removing table-level permissions for {len(table_names)} tables")
                removals = []
                
                for table_name in table_names:
                    # Build scope for the specific table
                    table_scope = (
                        f"/subscriptions/{self.subscription_id}/"
                        f"resourceGroups/{resource_group_name}/"
                        f"providers/Microsoft.Storage/storageAccounts/{storage_account_name}/"
                        f"tableServices/default/tables/{table_name}"
                    )
                    
                    try:
                        # Get role definition ID
                        role_definition_id = self._get_role_definition_id(role_name, table_scope)
                        
                        # List role assignments for this user at table scope
                        role_assignments = list(self.auth_client.role_assignments.list_for_scope(
                            scope=table_scope,
                            filter=f"principalId eq '{user_object_id}'"
                        ))
                        
                        removed_count = 0
                        for assignment in role_assignments:
                            # Check if this assignment matches the role we want to remove
                            if role_definition_id in assignment.role_definition_id:
                                # Delete the role assignment
                                self.auth_client.role_assignments.delete(
                                    scope=table_scope,
                                    role_assignment_name=assignment.name
                                )
                                removed_count += 1
                                logger.info(f"✅ Removed '{role_name}' from user on table '{table_name}'")
                        
                        if removed_count > 0:
                            removals.append({
                                "table_name": table_name,
                                "status": "removed",
                                "removed_count": removed_count,
                                "scope": table_scope
                            })
                        else:
                            removals.append({
                                "table_name": table_name,
                                "status": "no_assignment_found",
                                "scope": table_scope
                            })
                        
                    except HttpResponseError as ex:
                        logger.error(f"❌ Error removing permissions for table '{table_name}': {str(ex)}")
                        removals.append({
                            "table_name": table_name,
                            "status": "error",
                            "error": str(ex)
                        })
                    except Exception as ex:
                        logger.error(f"❌ Unexpected error removing permissions for table '{table_name}': {str(ex)}")
                        removals.append({
                            "table_name": table_name,
                            "status": "error",
                            "error": str(ex)
                        })
                
                successful = sum(1 for r in removals if r["status"] == "removed")
                failed = sum(1 for r in removals if r["status"] == "error")
                
                result = {
                    "status": "completed",
                    "role": role_name,
                    "scope_level": "table",
                    "removals": removals,
                    "total_tables": len(table_names),
                    "successful": successful,
                    "failed": failed
                }
                
                logger.info(
                    f"Permission removal completed: {successful}/{len(table_names)} successful, {failed} failed"
                )
                
                add_span_attributes(span, {
                    "successful": successful,
                    "failed": failed
                })
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except Exception as ex:
                logger.error(f"Error removing table permissions: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def check_tables_exist(
        self,
        storage_account_name: str,
        table_names: List[str],
        resource_group_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check which tables exist in the storage account.
        
        Args:
            storage_account_name: Name of the storage account
            table_names: List of table names to check
            resource_group_name: Optional resource group name
            
        Returns:
            Dict with existing and missing tables:
            {
                "existing_tables": [...],
                "missing_tables": [...],
                "all_exist": bool
            }
        """
        with self.tracer.start_as_current_span("check_tables_exist") as span:
            add_span_attributes(span, {
                "storage_account": storage_account_name,
                "table_count": len(table_names)
            })
            
            try:
                # Create table service client
                account_url = f"https://{storage_account_name}.table.core.windows.net"
                logger.debug(f"Tables URL: '{account_url}'")
                table_service_client = TableServiceClient(
                    endpoint=account_url,
                    credential=self.credential
                )
                
                existing_tables = []
                missing_tables = []
                
                # Check each table
                for table_name in table_names:
                    try:
                        table_client = table_service_client.get_table_client(table_name=table_name)
                        # Use list_entities with minimal results to check if table exists
                        # This only requires basic "Storage Table Data Contributor" permissions
                        list(table_client.list_entities(results_per_page=1))
                        existing_tables.append(table_name)
                        logger.debug(f"Table '{table_name}' exists")
                    except Exception as e:
                        from azure.core.exceptions import ResourceNotFoundError
                        missing_tables.append(table_name)
                        
                        # Handle table not found gracefully without stack trace
                        if isinstance(e, ResourceNotFoundError) or "TableNotFound" in str(e):
                            logger.info(f"Table '{table_name}' does not exist in storage account '{storage_account_name}'")
                        else:
                            # For other types of errors, log more details
                            logger.warning(
                                f"Table '{table_name}' check failed. "
                                f"Error type: {type(e).__name__}, "
                                f"Error message: {str(e)}, "
                                f"Storage account: {storage_account_name}, "
                                f"Account URL: {account_url}"
                            )
                            logger.debug(f"Table '{table_name}' access error details", exc_info=True)
                
                result = {
                    "existing_tables": existing_tables,
                    "missing_tables": missing_tables,
                    "all_exist": len(missing_tables) == 0
                }            
                
                add_span_attributes(span, {
                    "existing_count": len(existing_tables),
                    "missing_count": len(missing_tables)
                })
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except Exception as ex:
                logger.error(f"Error checking table existence: {str(ex)}")
                record_error_details(span, type(ex).__name__, str(ex), False)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    def _get_role_definition_id(self, role_name: str, scope: str) -> str:
        """
        Get the full role definition ID for a role name.
        
        Args:
            role_name: Name of the role
            scope: Scope where the role will be assigned
            
        Returns:
            Full role definition ID
        """
        try:
            # Use predefined role IDs if available
            if role_name in ROLE_DEFINITIONS:
                role_id = ROLE_DEFINITIONS[role_name]
                return f"/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_id}"
            
            # Otherwise, search for the role
            role_definitions = self.auth_client.role_definitions.list(scope=scope)
            for role_def in role_definitions:
                if role_def.role_name == role_name:
                    return role_def.id
            
            raise ValueError(f"Role '{role_name}' not found")
            
        except Exception as ex:
            logger.error(f"Error getting role definition ID: {str(ex)}")
            raise
