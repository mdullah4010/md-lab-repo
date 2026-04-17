#!/usr/bin/env python3
"""
GitHub Variables Copy Script

This script copies variables and lists secrets between:
- GitHub Environments (environment-specific variables)
- GitHub Repositories (repository-level variables)

Requirements:
    pip install requests

Usage:
    # List repository variables
    python copy_github_env_vars.py --owner OWNER --repo REPO --list-repo-vars
    
    # Copy repository variables to another repo
    python copy_github_env_vars.py --owner OWNER --source-repo REPO1 --target-repo REPO2 --token TOKEN
    
    # List environments (if configured)
    python copy_github_env_vars.py --owner OWNER --repo REPO --list-environments
    
    # Copy environment variables
    python copy_github_env_vars.py --owner OWNER --repo REPO --source ENV1 --target ENV2 --token TOKEN

Example:
    python copy_github_env_vars.py --owner myorg --repo myrepo --list-repo-vars

Note:
    - Requires a GitHub Personal Access Token with 'repo' scope
    - Secret values cannot be read from GitHub API (they will need to be set manually)
    - Only non-secret variables will be copied
"""

import os
import sys
import argparse
import logging
import json
from typing import Dict, List, Optional
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GitHubEnvVarCopier:
    """Copy variables between GitHub repositories or environments."""
    
    def __init__(self, owner: str, repo: Optional[str], token: str):
        """
        Initialize the copier.
        
        Args:
            owner: GitHub repository owner (user or organization)
            repo: GitHub repository name (optional for some operations)
            token: GitHub Personal Access Token
        """
        self.owner = owner
        self.repo = repo
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    
    @staticmethod
    def parse_env_file(file_path: str) -> Dict[str, str]:
        """
        Parse a .env file and extract key-value pairs.
        
        Args:
            file_path: Path to the .env file
            
        Returns:
            Dictionary of environment variables
        """
        variables = {}
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f".env file not found: {file_path}")
        
        logger.info(f"Parsing .env file: {file_path}")
        
        with open(file_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                # Remove leading/trailing whitespace
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # Skip export statements (common in shell scripts)
                if line.startswith('export '):
                    line = line[7:].strip()
                
                # Parse KEY=VALUE format
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    
                    variables[key] = value
                else:
                    logger.warning(f"Skipping invalid line {line_num}: {line}")
        
        logger.info(f"Found {len(variables)} variables in .env file")
        return variables
    
    def upload_variables_from_file(self, env_file: str, target_environment: Optional[str] = None, 
                                   dry_run: bool = False) -> Dict[str, any]:
        """
        Upload variables from a .env file to GitHub environment or repository.
        
        Args:
            env_file: Path to .env file
            target_environment: Target environment name (None for repository-level)
            dry_run: If True, only show what would be uploaded
            
        Returns:
            Dictionary with upload results
        """
        # Parse .env file
        variables = self.parse_env_file(env_file)
        
        if not variables:
            logger.warning("No variables found in .env file")
            return {
                'total': 0,
                'uploaded': 0,
                'failed': 0,
                'variables_uploaded': [],
                'variables_failed': []
            }
        
        results = {
            'total': len(variables),
            'uploaded': 0,
            'failed': 0,
            'variables_uploaded': [],
            'variables_failed': []
        }
        
        if dry_run:
            logger.info("\n🔍 DRY RUN - Would upload the following variables:")
            for name in variables.keys():
                logger.info(f"   {name}")
            return results
        
        # Upload variables
        if target_environment:
            logger.info(f"\nUploading {len(variables)} variables to environment: {target_environment}")
            
            for name, value in variables.items():
                try:
                    success = self.create_or_update_variable(target_environment, name, value)
                    if success:
                        results['uploaded'] += 1
                        results['variables_uploaded'].append(name)
                    else:
                        results['failed'] += 1
                        results['variables_failed'].append(name)
                except Exception as e:
                    logger.error(f"Failed to upload {name}: {e}")
                    results['failed'] += 1
                    results['variables_failed'].append(name)
        else:
            logger.info(f"\nUploading {len(variables)} variables to repository: {self.owner}/{self.repo}")
            
            for name, value in variables.items():
                try:
                    url = f"{self.base_url}/repos/{self.owner}/{self.repo}/actions/variables"
                    
                    # Check if variable exists
                    check_url = f"{url}/{name}"
                    check_response = requests.get(check_url, headers=self.headers)
                    
                    if check_response.status_code == 200:
                        # Update existing variable
                        update_url = f"{url}/{name}"
                        response = requests.patch(
                            update_url,
                            headers=self.headers,
                            json={"name": name, "value": value}
                        )
                        response.raise_for_status()
                        logger.info(f"✅ Updated variable: {name}")
                    else:
                        # Create new variable
                        response = requests.post(
                            url,
                            headers=self.headers,
                            json={"name": name, "value": value}
                        )
                        response.raise_for_status()
                        logger.info(f"✅ Created variable: {name}")
                    
                    results['uploaded'] += 1
                    results['variables_uploaded'].append(name)
                    
                except Exception as e:
                    logger.error(f"Failed to upload {name}: {e}")
                    results['failed'] += 1
                    results['variables_failed'].append(name)
        
        return results
    
    def get_repository_variables(self, repo: Optional[str] = None) -> Dict[str, str]:
        """
        Get all repository-level variables.
        
        Args:
            repo: Repository name (uses self.repo if not provided)
            
        Returns:
            Dictionary of variables
        """
        repo_name = repo or self.repo
        url = f"{self.base_url}/repos/{self.owner}/{repo_name}/actions/variables"
        
        logger.info(f"Fetching repository variables from: {self.owner}/{repo_name}")
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            variables = data.get("variables", [])
            
            logger.info(f"Found {len(variables)} repository variables")
            
            return {var["name"]: var["value"] for var in variables}
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(f"Repository '{self.owner}/{repo_name}' not found or no access")
            else:
                logger.error(f"Failed to fetch repository variables: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching repository variables: {e}")
            raise
    
    def get_repository_secrets(self, repo: Optional[str] = None) -> List[str]:
        """
        Get list of repository-level secret names.
        
        Args:
            repo: Repository name (uses self.repo if not provided)
            
        Returns:
            List of secret names
        """
        repo_name = repo or self.repo
        url = f"{self.base_url}/repos/{self.owner}/{repo_name}/actions/secrets"
        
        logger.info(f"Fetching repository secrets from: {self.owner}/{repo_name}")
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            secrets = data.get("secrets", [])
            
            logger.info(f"Found {len(secrets)} repository secrets")
            
            return [secret["name"] for secret in secrets]
            
        except Exception as e:
            logger.error(f"Error fetching repository secrets: {e}")
            raise
    
    def list_environments(self) -> List[Dict[str, any]]:
        """
        List all environments in the repository.
        
        Returns:
            List of environment dictionaries
        """
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments"
        
        logger.info(f"Fetching environments for {self.owner}/{self.repo}")
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            environments = data.get("environments", [])
            
            if len(environments) == 0:
                logger.warning(f"No GitHub Environments configured for {self.owner}/{self.repo}")
                logger.warning("GitHub Environments are different from repository variables.")
                logger.warning("Use --list-repo-vars to see repository-level variables instead.")
            else:
                logger.info(f"Found {len(environments)} environments")
            
            return environments
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"No GitHub Environments found or feature not available")
                logger.warning("This repository may not have Environments configured.")
                logger.warning("Use --list-repo-vars to see repository-level variables instead.")
                return []
            else:
                logger.error(f"Error fetching environments: {e}")
                raise
        except Exception as e:
            logger.error(f"Error fetching environments: {e}")
            raise
    
    def get_environment_variables(self, environment_name: str) -> Dict[str, any]:
        """
        Get all environment variables from a GitHub environment.
        
        Args:
            environment_name: Name of the environment
            
        Returns:
            Dictionary of environment variables
        """
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments/{environment_name}/variables"
        
        logger.info(f"Fetching variables from environment: {environment_name}")
        
        try:
            all_variables = []
            page = 1
            per_page = 100  # Maximum per page
            
            while True:
                paginated_url = f"{url}?per_page={per_page}&page={page}"
                response = requests.get(paginated_url, headers=self.headers)
                response.raise_for_status()
                
                data = response.json()
                variables = data.get("variables", [])
                
                if not variables:
                    break
                    
                all_variables.extend(variables)
                
                # Check if there are more pages
                if len(variables) < per_page:
                    break
                    
                page += 1
            
            logger.info(f"Found {len(all_variables)} variables in {environment_name}")
            
            return {var["name"]: var["value"] for var in all_variables}
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(f"Environment '{environment_name}' not found")
                logger.error(f"\nUse --list-environments to see available environments")
            else:
                logger.error(f"Failed to fetch variables: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching variables: {e}")
            raise
    
    def get_environment_secrets(self, environment_name: str) -> List[str]:
        """
        Get list of secret names from a GitHub environment.
        Note: Secret values cannot be retrieved via API.
        
        Args:
            environment_name: Name of the environment
            
        Returns:
            List of secret names
        """
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments/{environment_name}/secrets"
        
        logger.info(f"Fetching secret names from environment: {environment_name}")
        
        try:
            all_secrets = []
            page = 1
            per_page = 100  # Maximum per page
            
            while True:
                paginated_url = f"{url}?per_page={per_page}&page={page}"
                response = requests.get(paginated_url, headers=self.headers)
                response.raise_for_status()
                
                data = response.json()
                secrets = data.get("secrets", [])
                
                if not secrets:
                    break
                    
                all_secrets.extend(secrets)
                
                # Check if there are more pages
                if len(secrets) < per_page:
                    break
                    
                page += 1
            
            logger.info(f"Found {len(all_secrets)} secrets in {environment_name}")
            
            return [secret["name"] for secret in all_secrets]
            
        except Exception as e:
            logger.error(f"Error fetching secrets: {e}")
            raise
    
    def create_or_update_variable(self, environment_name: str, var_name: str, var_value: str) -> bool:
        """
        Create or update an environment variable.
        
        Args:
            environment_name: Name of the environment
            var_name: Variable name
            var_value: Variable value
            
        Returns:
            True if successful
        """
        # Check if variable exists
        get_url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments/{environment_name}/variables/{var_name}"
        
        try:
            response = requests.get(get_url, headers=self.headers)
            variable_exists = response.status_code == 200
            
            if variable_exists:
                # Update existing variable
                update_url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments/{environment_name}/variables/{var_name}"
                payload = {"name": var_name, "value": var_value}
                
                response = requests.patch(update_url, headers=self.headers, json=payload)
                response.raise_for_status()
                
                logger.info(f"✅ Updated variable: {var_name}")
                return True
            else:
                # Create new variable
                create_url = f"{self.base_url}/repos/{self.owner}/{self.repo}/environments/{environment_name}/variables"
                payload = {"name": var_name, "value": var_value}
                
                response = requests.post(create_url, headers=self.headers, json=payload)
                response.raise_for_status()
                
                logger.info(f"✅ Created variable: {var_name}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to create/update variable {var_name}: {e}")
            return False
    
    def copy_variables(self, source_env: str, target_env: str, dry_run: bool = False) -> Dict[str, any]:
        """
        Copy all variables from source environment to target environment.
        
        Args:
            source_env: Source environment name
            target_env: Target environment name
            dry_run: If True, only show what would be copied without making changes
            
        Returns:
            Dictionary with copy results
        """
        results = {
            "source_env": source_env,
            "target_env": target_env,
            "variables_copied": [],
            "variables_failed": [],
            "secrets_found": [],
            "total_variables": 0,
            "success_count": 0,
            "failure_count": 0,
            "dry_run": dry_run
        }
        
        try:
            # Get variables from source environment
            source_variables = self.get_environment_variables(source_env)
            results["total_variables"] = len(source_variables)
            
            # Get secrets (names only, values cannot be retrieved)
            source_secrets = self.get_environment_secrets(source_env)
            results["secrets_found"] = source_secrets
            
            if dry_run:
                logger.info("\n" + "="*60)
                logger.info("DRY RUN MODE - No changes will be made")
                logger.info("="*60)
            
            # Copy each variable
            logger.info(f"\nCopying {len(source_variables)} variables from '{source_env}' to '{target_env}'...")
            
            for var_name, var_value in source_variables.items():
                if dry_run:
                    logger.info(f"Would copy: {var_name}")
                    results["variables_copied"].append(var_name)
                    results["success_count"] += 1
                else:
                    success = self.create_or_update_variable(target_env, var_name, var_value)
                    if success:
                        results["variables_copied"].append(var_name)
                        results["success_count"] += 1
                    else:
                        results["variables_failed"].append(var_name)
                        results["failure_count"] += 1
            
            # Warn about secrets
            if source_secrets:
                logger.warning("\n" + "="*60)
                logger.warning("⚠️  SECRETS DETECTED (Cannot be copied via API)")
                logger.warning("="*60)
                logger.warning(f"The following {len(source_secrets)} secrets exist in '{source_env}':")
                for secret_name in source_secrets:
                    logger.warning(f"   - {secret_name}")
                logger.warning("\nYou must manually copy secret values:")
                logger.warning(f"   1. Go to: https://github.com/{self.owner}/{self.repo}/settings/environments")
                logger.warning(f"   2. Open environment: {target_env}")
                logger.warning(f"   3. Add each secret with its value from {source_env}")
                logger.warning("="*60)
            
            return results
            
        except Exception as e:
            logger.error(f"Error during copy operation: {e}")
            raise
    
    def print_summary(self, results: Dict[str, any]):
        """Print a summary of the copy operation."""
        logger.info("\n" + "="*60)
        logger.info("COPY SUMMARY")
        logger.info("="*60)
        logger.info(f"Source Environment: {results['source_env']}")
        logger.info(f"Target Environment: {results['target_env']}")
        logger.info(f"Dry Run: {results['dry_run']}")
        logger.info(f"\nVariables:")
        logger.info(f"   Total: {results['total_variables']}")
        logger.info(f"   Copied: {results['success_count']}")
        logger.info(f"   Failed: {results['failure_count']}")
        logger.info(f"\nSecrets Found: {len(results['secrets_found'])}")
        
        if results['variables_copied']:
            logger.info(f"\nCopied Variables:")
            for var in results['variables_copied']:
                logger.info(f"   ✅ {var}")
        
        if results['variables_failed']:
            logger.info(f"\nFailed Variables:")
            for var in results['variables_failed']:
                logger.info(f"   ❌ {var}")
        
        logger.info("="*60 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Copy variables between GitHub repositories or environments",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--owner",
        required=True,
        help="GitHub repository owner (user or organization)"
    )
    
    parser.add_argument(
        "--repo",
        help="GitHub repository name (required for most operations)"
    )
    
    parser.add_argument(
        "--source",
        help="Source environment name (for environment copy)"
    )
    
    parser.add_argument(
        "--target",
        help="Target environment name (for environment copy)"
    )
    
    parser.add_argument(
        "--source-repo",
        help="Source repository name (for repo-to-repo copy)"
    )
    
    parser.add_argument(
        "--target-repo",
        help="Target repository name (for repo-to-repo copy)"
    )
    
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub Personal Access Token (or set GITHUB_TOKEN env var)"
    )
    
    parser.add_argument(
        "--list-repo-vars",
        action="store_true",
        help="List all repository-level variables and secrets"
    )
    
    parser.add_argument(
        "--list-environments",
        action="store_true",
        help="List all GitHub Environments (if configured)"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without making changes"
    )
    
    parser.add_argument(
        "--output",
        help="Save results to JSON file"
    )
    
    parser.add_argument(
        "--from-env-file",
        help="Import variables from .env file to GitHub environment or repository"
    )
    
    args = parser.parse_args()
    
    # Validate token
    if not args.token:
        logger.error("GitHub token is required. Use --token or set GITHUB_TOKEN environment variable")
        sys.exit(1)
    
    try:
        # Create copier instance
        copier = GitHubEnvVarCopier(args.owner, args.repo, args.token)
        
        # Import from .env file mode
        if args.from_env_file:
            if not args.repo:
                logger.error("--repo is required for --from-env-file")
                sys.exit(1)
            
            logger.info("=" * 60)
            if args.target:
                logger.info(f"IMPORTING VARIABLES TO ENVIRONMENT: {args.target}")
            else:
                logger.info(f"IMPORTING VARIABLES TO REPOSITORY: {args.owner}/{args.repo}")
            logger.info("=" * 60)
            
            results = copier.upload_variables_from_file(
                env_file=args.from_env_file,
                target_environment=args.target,
                dry_run=args.dry_run
            )
            
            # Print summary
            logger.info("\n" + "=" * 60)
            logger.info("UPLOAD SUMMARY")
            logger.info("=" * 60)
            logger.info(f"Source: {args.from_env_file}")
            if args.target:
                logger.info(f"Target Environment: {args.target}")
            else:
                logger.info(f"Target Repository: {args.owner}/{args.repo}")
            logger.info(f"Dry Run: {args.dry_run}")
            logger.info(f"\nVariables:")
            logger.info(f"   Total: {results['total']}")
            logger.info(f"   Uploaded: {results['uploaded']}")
            logger.info(f"   Failed: {results['failed']}")
            
            if results['variables_uploaded']:
                logger.info(f"\nUploaded Variables:")
                for var in results['variables_uploaded']:
                    logger.info(f"   ✅ {var}")
            
            if results['variables_failed']:
                logger.info(f"\nFailed Variables:")
                for var in results['variables_failed']:
                    logger.info(f"   ❌ {var}")
            
            logger.info("=" * 60)
            
            # Save to JSON if requested
            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                logger.info(f"\nResults saved to: {args.output}")
            
            sys.exit(0)
        
        # List repository variables mode
        if args.list_repo_vars:
            if not args.repo:
                logger.error("--repo is required for --list-repo-vars")
                sys.exit(1)
            
            logger.info("=" * 60)
            logger.info(f"REPOSITORY VARIABLES IN {args.owner}/{args.repo}")
            logger.info("=" * 60)
            
            # Get variables
            variables = copier.get_repository_variables()
            
            if variables:
                logger.info(f"\n📋 Variables ({len(variables)}):")
                for name in variables.keys():
                    logger.info(f"   {name}")
            else:
                logger.warning("No repository variables found")
            
            # Get secrets
            secrets = copier.get_repository_secrets()
            
            if secrets:
                logger.info(f"\n🔒 Secrets ({len(secrets)}):")
                for name in secrets:
                    logger.info(f"   {name} = <hidden>")
            else:
                logger.warning("No repository secrets found")
            
            logger.info("\n" + "=" * 60)
            logger.info(f"Total Variables: {len(variables)}")
            logger.info(f"Total Secrets: {len(secrets)}")
            logger.info("=" * 60)
            
            sys.exit(0)
        
        # List environments mode
        if args.list_environments:
            if not args.repo:
                logger.error("--repo is required for --list-environments")
                sys.exit(1)
            
            logger.info("=" * 60)
            logger.info(f"GITHUB ENVIRONMENTS IN {args.owner}/{args.repo}")
            logger.info("=" * 60)
            
            environments = copier.list_environments()
            
            if not environments:
                sys.exit(0)
            
            for env in environments:
                logger.info(f"\n📦 {env['name']}")
                logger.info(f"   ID: {env.get('id', 'N/A')}")
                if env.get('protection_rules'):
                    logger.info(f"   Protection Rules: {len(env['protection_rules'])}")
                if env.get('deployment_branch_policy'):
                    logger.info(f"   Deployment Policy: {env['deployment_branch_policy']}")
            
            logger.info("\n" + "=" * 60)
            logger.info(f"Total Environments: {len(environments)}")
            logger.info("=" * 60)
            
            sys.exit(0)
        
        # Validate required arguments for copy operation
        if not args.source or not args.target:
            logger.error("Both --source and --target are required for copy operation")
            logger.error("Use --list-repo-vars or --list-environments to see what's available")
            sys.exit(1)
        
        # Perform copy operation
        results = copier.copy_variables(args.source, args.target, dry_run=args.dry_run)
        
        # Print summary
        copier.print_summary(results)
        
        # Save results if requested
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"Results saved to: {args.output}")
        
        # Exit with appropriate code
        if results['failure_count'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
