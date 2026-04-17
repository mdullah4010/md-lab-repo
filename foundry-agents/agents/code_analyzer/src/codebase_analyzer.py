"""
Codebase Analyzer - Deterministic Code Analysis (No LLM Required)

This module provides deterministic, fast, and accurate codebase analysis:
- File statistics (count, LOC, size by extension)
- Framework and SDK detection
- Dependency extraction with versions
- MVC/Layer classification
- Class dependency graph extraction
- Mermaid diagram generation

All analysis is done using static analysis (AST, file parsing) without LLM calls.
Supports Python (native AST), C# (tree-sitter), and Java (tree-sitter) class extraction.
"""

import os
import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import logging

# Tree-sitter imports for C# and Java parsing
try:
    import tree_sitter_c_sharp as tscsharp
    from tree_sitter import Language, Parser
    TREE_SITTER_CSHARP_AVAILABLE = True
except ImportError:
    TREE_SITTER_CSHARP_AVAILABLE = False

try:
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser
    TREE_SITTER_JAVA_AVAILABLE = True
except ImportError:
    TREE_SITTER_JAVA_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FileStats:
    """Statistics for a single file type."""
    extension: str
    count: int = 0
    total_lines: int = 0
    total_bytes: int = 0


@dataclass
class DependencyInfo:
    """Information about a dependency."""
    name: str
    version: str
    language: str
    source_file: str
    is_dev_dependency: bool = False


@dataclass
class FrameworkInfo:
    """Information about a detected framework/SDK."""
    name: str
    category: str  # "framework", "sdk", "testing", "database", "security"
    package: str
    version: Optional[str] = None


@dataclass
class ClassInfo:
    """Information about a class."""
    name: str
    file_path: str
    base_classes: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    layer: Optional[str] = None  # "model", "view", "controller", "service", etc.


@dataclass
class CodebaseAnalysisResult:
    """Complete codebase analysis result."""
    file_stats: Dict[str, FileStats]
    total_files: int
    total_lines: int
    total_bytes: int
    frameworks: List[FrameworkInfo]
    dependencies: List[DependencyInfo]
    mvc_classification: Dict[str, List[str]]
    classes: List[ClassInfo]
    class_dependencies: Dict[str, List[str]]
    mermaid_class_diagram: str
    mermaid_dependency_diagram: str
    language_breakdown: Dict[str, int]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "file_stats": {k: vars(v) for k, v in self.file_stats.items()},
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "total_bytes": self.total_bytes,
            "frameworks": [vars(f) for f in self.frameworks],
            "dependencies": [vars(d) for d in self.dependencies],
            "mvc_classification": self.mvc_classification,
            "classes": [vars(c) for c in self.classes],
            "class_dependencies": self.class_dependencies,
            "mermaid_class_diagram": self.mermaid_class_diagram,
            "mermaid_dependency_diagram": self.mermaid_dependency_diagram,
            "language_breakdown": self.language_breakdown
        }
    
    def to_markdown_section(self) -> str:
        """Generate markdown section for the report."""
        md = []
        
        # Codebase Overview Header
        md.append("## Codebase Overview\n")
        
        # File Statistics Table
        md.append("### File Statistics\n")
        md.append("| File Type | Count | Lines of Code | Size |")
        md.append("|-----------|-------|---------------|------|")
        
        sorted_stats = sorted(
            self.file_stats.values(), 
            key=lambda x: x.count, 
            reverse=True
        )
        for stat in sorted_stats[:15]:  # Top 15 file types
            size_str = self._format_bytes(stat.total_bytes)
            md.append(f"| {stat.extension} | {stat.count} | {stat.total_lines:,} | {size_str} |")
        
        md.append(f"| **Total** | **{self.total_files}** | **{self.total_lines:,}** | **{self._format_bytes(self.total_bytes)}** |")
        md.append("")
        
        # Language Breakdown
        if self.language_breakdown:
            md.append("### Language Breakdown\n")
            md.append("| Language | Files | Percentage |")
            md.append("|----------|-------|------------|")
            total = sum(self.language_breakdown.values())
            for lang, count in sorted(self.language_breakdown.items(), key=lambda x: x[1], reverse=True)[:10]:
                pct = (count / total * 100) if total > 0 else 0
                md.append(f"| {lang} | {count} | {pct:.1f}% |")
            md.append("")
        
        # Framework & SDK Detection
        if self.frameworks:
            md.append("### Framework & SDK Detection\n")
            md.append("| Category | Name | Package | Version |")
            md.append("|----------|------|---------|---------|")
            for fw in self.frameworks:
                version = fw.version or "N/A"
                md.append(f"| {fw.category.title()} | {fw.name} | {fw.package} | {version} |")
            md.append("")
        
        # External Dependencies
        if self.dependencies:
            md.append("### External Dependencies\n")
            md.append(f"*Total: {len(self.dependencies)} dependencies*\n")
            md.append("| Package | Version | Language |")
            md.append("|---------|---------|----------|")
            for dep in self.dependencies[:30]:  # Top 30
                md.append(f"| {dep.name} | {dep.version} | {dep.language} |")
            if len(self.dependencies) > 30:
                md.append(f"| ... | ... | ... |")
                md.append(f"| *{len(self.dependencies) - 30} more dependencies* | | |")
            md.append("")
        
        # Architectural Assessment - only show if we have layer classifications
        has_layers = any(files for files in self.mvc_classification.values())
        if has_layers:
            md.append("### Architectural Assessment\n")
            md.append("#### Code Structure (Layer Classification)\n")
            md.append("| Layer | Files | Examples |")
            md.append("|-------|-------|----------|")
            for layer, files in self.mvc_classification.items():
                if files:
                    examples = ", ".join(files[:3])
                    if len(files) > 3:
                        examples += f" (+{len(files)-3} more)"
                    md.append(f"| {layer.title()} | {len(files)} | {examples} |")
            md.append("")
        
        # Class Information
        if self.classes:
            md.append("#### Classes Detected\n")
            md.append(f"*Total: {len(self.classes)} classes*\n")
            md.append("| Class | File | Layer | Methods | Base Classes |")
            md.append("|-------|------|-------|---------|--------------|")
            for cls in self.classes[:20]:  # Top 20
                layer = cls.layer or "Unknown"
                methods_count = len(cls.methods)
                bases = ", ".join(cls.base_classes) if cls.base_classes else "None"
                file_short = Path(cls.file_path).name
                md.append(f"| {cls.name} | {file_short} | {layer} | {methods_count} | {bases} |")
            if len(self.classes) > 20:
                md.append(f"| *...{len(self.classes) - 20} more classes* | | | | |")
            md.append("")
        
        # Dependency Mapping - Mermaid Diagrams (only if we have actual class data)
        has_class_diagram = self.mermaid_class_diagram and len(self.mermaid_class_diagram.strip().split('\n')) > 1
        has_dependency_diagram = self.mermaid_dependency_diagram and len(self.mermaid_dependency_diagram.strip().split('\n')) > 1
        
        if has_class_diagram or has_dependency_diagram:
            md.append("### Dependency Mapping\n")
            
            if has_class_diagram:
                md.append("#### Class Dependency Diagram\n")
                md.append("```mermaid")
                md.append(self.mermaid_class_diagram)
                md.append("```\n")
            
            if has_dependency_diagram:
                md.append("#### Module Dependency Diagram\n")
                md.append("```mermaid")
                md.append(self.mermaid_dependency_diagram)
                md.append("```\n")
        
        md.append("---\n")
        
        return "\n".join(md)
    
    def _format_bytes(self, bytes_val: int) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} TB"


# =============================================================================
# FRAMEWORK AND SDK PATTERNS
# =============================================================================

FRAMEWORK_PATTERNS = {
    # Python Web Frameworks
    "flask": FrameworkInfo("Flask", "framework", "flask"),
    "django": FrameworkInfo("Django", "framework", "django"),
    "fastapi": FrameworkInfo("FastAPI", "framework", "fastapi"),
    "tornado": FrameworkInfo("Tornado", "framework", "tornado"),
    "bottle": FrameworkInfo("Bottle", "framework", "bottle"),
    "pyramid": FrameworkInfo("Pyramid", "framework", "pyramid"),
    "starlette": FrameworkInfo("Starlette", "framework", "starlette"),
    
    # JavaScript Frameworks
    "react": FrameworkInfo("React", "framework", "react"),
    "vue": FrameworkInfo("Vue.js", "framework", "vue"),
    "angular": FrameworkInfo("Angular", "framework", "@angular/core"),
    "express": FrameworkInfo("Express.js", "framework", "express"),
    "next": FrameworkInfo("Next.js", "framework", "next"),
    "nuxt": FrameworkInfo("Nuxt.js", "framework", "nuxt"),
    "nestjs": FrameworkInfo("NestJS", "framework", "@nestjs/core"),
    "svelte": FrameworkInfo("Svelte", "framework", "svelte"),
    
    # Cloud SDKs
    "azure-": FrameworkInfo("Azure SDK", "sdk", "azure-*"),
    "boto3": FrameworkInfo("AWS SDK (Python)", "sdk", "boto3"),
    "botocore": FrameworkInfo("AWS SDK Core", "sdk", "botocore"),
    "@aws-sdk": FrameworkInfo("AWS SDK (JS)", "sdk", "@aws-sdk/*"),
    "google-cloud": FrameworkInfo("Google Cloud SDK", "sdk", "google-cloud-*"),
    "@google-cloud": FrameworkInfo("Google Cloud SDK (JS)", "sdk", "@google-cloud/*"),
    
    # Database
    "sqlalchemy": FrameworkInfo("SQLAlchemy", "database", "sqlalchemy"),
    "pymongo": FrameworkInfo("PyMongo", "database", "pymongo"),
    "psycopg2": FrameworkInfo("PostgreSQL", "database", "psycopg2"),
    "mysql-connector": FrameworkInfo("MySQL", "database", "mysql-connector-python"),
    "redis": FrameworkInfo("Redis", "database", "redis"),
    "mongoose": FrameworkInfo("Mongoose", "database", "mongoose"),
    "prisma": FrameworkInfo("Prisma", "database", "prisma"),
    "typeorm": FrameworkInfo("TypeORM", "database", "typeorm"),
    "sequelize": FrameworkInfo("Sequelize", "database", "sequelize"),
    
    # Testing
    "pytest": FrameworkInfo("pytest", "testing", "pytest"),
    "unittest": FrameworkInfo("unittest", "testing", "unittest"),
    "jest": FrameworkInfo("Jest", "testing", "jest"),
    "mocha": FrameworkInfo("Mocha", "testing", "mocha"),
    "cypress": FrameworkInfo("Cypress", "testing", "cypress"),
    "selenium": FrameworkInfo("Selenium", "testing", "selenium"),
    
    # Security
    "cryptography": FrameworkInfo("Cryptography", "security", "cryptography"),
    "pyjwt": FrameworkInfo("PyJWT", "security", "pyjwt"),
    "passlib": FrameworkInfo("Passlib", "security", "passlib"),
    "bcrypt": FrameworkInfo("bcrypt", "security", "bcrypt"),
    "jsonwebtoken": FrameworkInfo("jsonwebtoken", "security", "jsonwebtoken"),
    
    # ML/AI
    "tensorflow": FrameworkInfo("TensorFlow", "ml", "tensorflow"),
    "torch": FrameworkInfo("PyTorch", "ml", "torch"),
    "scikit-learn": FrameworkInfo("scikit-learn", "ml", "scikit-learn"),
    "pandas": FrameworkInfo("Pandas", "data", "pandas"),
    "numpy": FrameworkInfo("NumPy", "data", "numpy"),
    
    # Infrastructure
    "kubernetes": FrameworkInfo("Kubernetes Client", "infrastructure", "kubernetes"),
    "docker": FrameworkInfo("Docker SDK", "infrastructure", "docker"),
    "terraform": FrameworkInfo("Terraform", "infrastructure", "terraform"),
    
    # .NET / C# Frameworks
    "aspnetcore": FrameworkInfo("ASP.NET Core", "framework", "Microsoft.AspNetCore"),
    "microsoft.aspnetcore": FrameworkInfo("ASP.NET Core", "framework", "Microsoft.AspNetCore"),
    "entityframeworkcore": FrameworkInfo("Entity Framework Core", "database", "Microsoft.EntityFrameworkCore"),
    "microsoft.entityframeworkcore": FrameworkInfo("Entity Framework Core", "database", "Microsoft.EntityFrameworkCore"),
    "newtonsoft.json": FrameworkInfo("Newtonsoft.Json", "utility", "Newtonsoft.Json"),
    "serilog": FrameworkInfo("Serilog", "logging", "Serilog"),
    "autofac": FrameworkInfo("Autofac", "dependency-injection", "Autofac"),
    "nunit": FrameworkInfo("NUnit", "testing", "NUnit"),
    "xunit": FrameworkInfo("xUnit", "testing", "xunit"),
    "moq": FrameworkInfo("Moq", "testing", "Moq"),
    "fluentassertions": FrameworkInfo("FluentAssertions", "testing", "FluentAssertions"),
    "mediatr": FrameworkInfo("MediatR", "framework", "MediatR"),
    "automapper": FrameworkInfo("AutoMapper", "utility", "AutoMapper"),
    "dapper": FrameworkInfo("Dapper", "database", "Dapper"),
    "signalr": FrameworkInfo("SignalR", "framework", "Microsoft.AspNetCore.SignalR"),
    "identityserver": FrameworkInfo("IdentityServer", "security", "IdentityServer4"),
    "polly": FrameworkInfo("Polly", "resilience", "Polly"),
    "hangfire": FrameworkInfo("Hangfire", "background-jobs", "Hangfire"),
    "swashbuckle": FrameworkInfo("Swagger/OpenAPI", "documentation", "Swashbuckle.AspNetCore"),
    "microsoft.azure": FrameworkInfo("Azure SDK (.NET)", "sdk", "Microsoft.Azure.*"),
    "azure.": FrameworkInfo("Azure SDK (.NET)", "sdk", "Azure.*"),
}

# MVC/Layer Classification Patterns
MVC_PATTERNS = {
    "models": ["models/", "model/", "entities/", "domain/", "schemas/", "schema/"],
    "views": ["views/", "templates/", "pages/", "components/", "ui/", "frontend/"],
    "controllers": ["controllers/", "routes/", "api/", "handlers/", "endpoints/", "routers/"],
    "services": ["services/", "service/", "business/", "logic/", "usecases/"],
    "repositories": ["repositories/", "repo/", "dal/", "data/", "persistence/", "dao/"],
    "utils": ["utils/", "util/", "helpers/", "common/", "shared/", "lib/"],
    "config": ["config/", "configuration/", "settings/", "conf/"],
    "tests": ["tests/", "test/", "__tests__/", "spec/", "specs/"],
    "middleware": ["middleware/", "middlewares/", "interceptors/"],
}

# File extension to language mapping
EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".java": "Java",
    ".cs": "C#",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".rs": "Rust",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".tf": "Terraform",
    ".hcl": "HCL",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".xml": "XML",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".md": "Markdown",
}


# =============================================================================
# MAIN ANALYZER CLASS
# =============================================================================

class CodebaseAnalyzer:
    """
    Deterministic codebase analyzer - no LLM required.
    
    Performs static analysis to extract:
    - File statistics
    - Framework/SDK detection
    - Dependency extraction
    - MVC/Layer classification
    - Class dependency graphs
    - Mermaid diagram generation
    """
    
    # Directories to skip during analysis
    SKIP_DIRS = {
        'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
        '.env', 'dist', 'build', '.idea', '.vscode', 'coverage',
        '.pytest_cache', '.mypy_cache', 'eggs', '*.egg-info',
        '.tox', '.nox', 'htmlcov', '.coverage', 'vendor', 'packages'
    }
    
    # Binary/non-text extensions to skip for LOC counting
    BINARY_EXTENSIONS = {
        '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.exe', '.dll', '.so', '.dylib', '.bin',
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp3', '.mp4', '.wav', '.avi', '.mov',
        '.pyc', '.pyo', '.class', '.o', '.obj'
    }
    
    def __init__(self, repo_path: str):
        """
        Initialize the analyzer.
        
        Args:
            repo_path: Path to the repository/codebase to analyze
            
        Raises:
            ValueError: If the path does not exist
        """
        self.repo_path = Path(repo_path)
        if not self.repo_path.exists():
            raise ValueError(f"Path does not exist: {repo_path}")
        
        self._file_stats: Dict[str, FileStats] = defaultdict(lambda: FileStats(""))
        self._frameworks: List[FrameworkInfo] = []
        self._dependencies: List[DependencyInfo] = []
        self._classes: List[ClassInfo] = []
        self._class_deps: Dict[str, List[str]] = {}
        self._mvc_classification: Dict[str, List[str]] = {k: [] for k in MVC_PATTERNS}
        self._language_breakdown: Dict[str, int] = defaultdict(int)
    
    def analyze(self) -> CodebaseAnalysisResult:
        """
        Perform complete codebase analysis.
        
        Returns:
            CodebaseAnalysisResult with all analysis data
        """
        logger.info(f"Starting codebase analysis for: {self.repo_path}")
        
        # Step 1: File statistics and language breakdown
        self._analyze_files()
        logger.info(f"File analysis complete: {sum(s.count for s in self._file_stats.values())} files")
        
        # Step 2: Framework and SDK detection
        self._detect_frameworks()
        logger.info(f"Framework detection complete: {len(self._frameworks)} frameworks found")
        
        # Step 3: Dependency extraction
        self._extract_dependencies()
        logger.info(f"Dependency extraction complete: {len(self._dependencies)} dependencies found")
        
        # Step 4: MVC/Layer classification
        self._classify_mvc()
        logger.info(f"MVC classification complete")
        
        # Step 5: Class extraction and dependency analysis
        self._extract_classes()
        logger.info(f"Class extraction complete: {len(self._classes)} classes found")
        
        # Step 6: Generate Mermaid diagrams
        class_diagram = self._generate_mermaid_class_diagram()
        dep_diagram = self._generate_mermaid_dependency_diagram()
        logger.info(f"Mermaid diagrams generated")
        
        # Build result
        total_files = sum(s.count for s in self._file_stats.values())
        total_lines = sum(s.total_lines for s in self._file_stats.values())
        total_bytes = sum(s.total_bytes for s in self._file_stats.values())
        
        result = CodebaseAnalysisResult(
            file_stats=dict(self._file_stats),
            total_files=total_files,
            total_lines=total_lines,
            total_bytes=total_bytes,
            frameworks=self._frameworks,
            dependencies=self._dependencies,
            mvc_classification=self._mvc_classification,
            classes=self._classes,
            class_dependencies=self._class_deps,
            mermaid_class_diagram=class_diagram,
            mermaid_dependency_diagram=dep_diagram,
            language_breakdown=dict(self._language_breakdown)
        )
        
        logger.info(f"Codebase analysis complete: {total_files} files, {total_lines} LOC")
        return result
    
    def _should_skip_dir(self, dir_name: str) -> bool:
        """Check if directory should be skipped."""
        return dir_name in self.SKIP_DIRS or dir_name.startswith('.')
    
    def _analyze_files(self) -> None:
        """Analyze all files for statistics."""
        for root, dirs, files in os.walk(self.repo_path):
            # Filter out directories to skip
            dirs[:] = [d for d in dirs if not self._should_skip_dir(d)]
            
            for file in files:
                file_path = Path(root) / file
                ext = file_path.suffix.lower() or "(no extension)"
                
                # Initialize stats for this extension
                if ext not in self._file_stats:
                    self._file_stats[ext] = FileStats(extension=ext)
                
                stats = self._file_stats[ext]
                stats.count += 1
                
                try:
                    stats.total_bytes += file_path.stat().st_size
                    
                    # Count lines for text files
                    if ext not in self.BINARY_EXTENSIONS:
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                lines = len(f.readlines())
                                stats.total_lines += lines
                        except:
                            pass
                    
                    # Track language breakdown
                    if ext in EXTENSION_LANGUAGE_MAP:
                        lang = EXTENSION_LANGUAGE_MAP[ext]
                        self._language_breakdown[lang] += 1
                        
                except Exception as e:
                    logger.debug(f"Error analyzing file {file_path}: {e}")
    
    def _detect_frameworks(self) -> None:
        """Detect frameworks and SDKs from dependency files."""
        detected = set()  # Track by name to avoid duplicates
        
        # Check Python requirements
        for req_file in ["requirements.txt", "requirements-dev.txt", "requirements_dev.txt"]:
            req_path = self.repo_path / req_file
            if req_path.exists():
                self._parse_requirements_txt(req_path, detected)
        
        # Check Pipfile
        pipfile = self.repo_path / "Pipfile"
        if pipfile.exists():
            self._parse_pipfile(pipfile, detected)
        
        # Check pyproject.toml
        pyproject = self.repo_path / "pyproject.toml"
        if pyproject.exists():
            self._parse_pyproject_toml(pyproject, detected)
        
        # Check package.json
        pkg_json = self.repo_path / "package.json"
        if pkg_json.exists():
            self._parse_package_json(pkg_json, detected)
        
        # Check pom.xml (Maven)
        pom_xml = self.repo_path / "pom.xml"
        if pom_xml.exists():
            self._parse_pom_xml(pom_xml, detected)
        
        # Check go.mod
        go_mod = self.repo_path / "go.mod"
        if go_mod.exists():
            self._parse_go_mod(go_mod, detected)
    
    def _parse_requirements_txt(self, path: Path, detected: Set[str]) -> None:
        """Parse Python requirements.txt."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('-'):
                        continue
                    
                    # Extract package name and version
                    match = re.match(r'^([a-zA-Z0-9_-]+)(?:[=<>!~]+(.+))?', line)
                    if match:
                        pkg_name = match.group(1).lower()
                        version = match.group(2) or "unspecified"
                        
                        # Check against framework patterns
                        for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                            if pattern in pkg_name and fw_info.name not in detected:
                                detected.add(fw_info.name)
                                self._frameworks.append(FrameworkInfo(
                                    name=fw_info.name,
                                    category=fw_info.category,
                                    package=pkg_name,
                                    version=version
                                ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _parse_pipfile(self, path: Path, detected: Set[str]) -> None:
        """Parse Pipfile."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                # Simple pattern matching for Pipfile
                for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                    if pattern in content.lower() and fw_info.name not in detected:
                        detected.add(fw_info.name)
                        self._frameworks.append(FrameworkInfo(
                            name=fw_info.name,
                            category=fw_info.category,
                            package=fw_info.package,
                            version=None
                        ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _parse_pyproject_toml(self, path: Path, detected: Set[str]) -> None:
        """Parse pyproject.toml."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                    if pattern in content.lower() and fw_info.name not in detected:
                        detected.add(fw_info.name)
                        self._frameworks.append(FrameworkInfo(
                            name=fw_info.name,
                            category=fw_info.category,
                            package=fw_info.package,
                            version=None
                        ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _parse_package_json(self, path: Path, detected: Set[str]) -> None:
        """Parse package.json for Node.js projects."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
            
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            
            for pkg_name, version in all_deps.items():
                pkg_lower = pkg_name.lower()
                for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                    if pattern in pkg_lower and fw_info.name not in detected:
                        detected.add(fw_info.name)
                        self._frameworks.append(FrameworkInfo(
                            name=fw_info.name,
                            category=fw_info.category,
                            package=pkg_name,
                            version=version
                        ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _parse_pom_xml(self, path: Path, detected: Set[str]) -> None:
        """Parse Maven pom.xml."""
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            
            # Handle Maven namespace
            ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
            
            # Find all dependencies
            for dep in root.findall('.//m:dependency', ns) + root.findall('.//dependency'):
                artifact_id_ns = dep.find('m:artifactId', ns)
                artifact_id = artifact_id_ns if artifact_id_ns is not None else dep.find('artifactId')
                version_elem_ns = dep.find('m:version', ns)
                version_elem = version_elem_ns if version_elem_ns is not None else dep.find('version')
                
                if artifact_id is not None:
                    artifact = artifact_id.text.lower() if artifact_id.text else ""
                    version = version_elem.text if version_elem is not None else None
                    
                    for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                        if pattern in artifact and fw_info.name not in detected:
                            detected.add(fw_info.name)
                            self._frameworks.append(FrameworkInfo(
                                name=fw_info.name,
                                category=fw_info.category,
                                package=artifact,
                                version=version
                            ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _parse_go_mod(self, path: Path, detected: Set[str]) -> None:
        """Parse go.mod."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                    if pattern in content.lower() and fw_info.name not in detected:
                        detected.add(fw_info.name)
                        self._frameworks.append(FrameworkInfo(
                            name=fw_info.name,
                            category=fw_info.category,
                            package=fw_info.package,
                            version=None
                        ))
        except Exception as e:
            logger.debug(f"Error parsing {path}: {e}")
    
    def _extract_dependencies(self) -> None:
        """Extract all dependencies with versions."""
        # Python requirements.txt
        for req_file in self.repo_path.rglob("requirements*.txt"):
            self._extract_python_deps(req_file, is_dev="dev" in req_file.name.lower())
        
        # Package.json
        for pkg_file in self.repo_path.rglob("package.json"):
            if "node_modules" not in str(pkg_file):
                self._extract_npm_deps(pkg_file)
        
        # pom.xml (Maven/Java)
        for pom_file in self.repo_path.rglob("pom.xml"):
            self._extract_maven_deps(pom_file)
        
        # .csproj files (NuGet/C#)
        for csproj_file in self.repo_path.rglob("*.csproj"):
            if "bin" not in str(csproj_file) and "obj" not in str(csproj_file):
                self._extract_nuget_deps(csproj_file)
    
    def _extract_python_deps(self, path: Path, is_dev: bool = False) -> None:
        """Extract Python dependencies."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('-'):
                        continue
                    
                    match = re.match(r'^([a-zA-Z0-9_.-]+)(?:[=<>!~]+(.+))?', line)
                    if match:
                        self._dependencies.append(DependencyInfo(
                            name=match.group(1),
                            version=match.group(2) or "unspecified",
                            language="Python",
                            source_file=str(path.relative_to(self.repo_path)),
                            is_dev_dependency=is_dev
                        ))
        except Exception as e:
            logger.debug(f"Error extracting deps from {path}: {e}")
    
    def _extract_npm_deps(self, path: Path) -> None:
        """Extract NPM dependencies."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
            
            for name, version in pkg.get("dependencies", {}).items():
                self._dependencies.append(DependencyInfo(
                    name=name,
                    version=version,
                    language="JavaScript",
                    source_file=str(path.relative_to(self.repo_path)),
                    is_dev_dependency=False
                ))
            
            for name, version in pkg.get("devDependencies", {}).items():
                self._dependencies.append(DependencyInfo(
                    name=name,
                    version=version,
                    language="JavaScript",
                    source_file=str(path.relative_to(self.repo_path)),
                    is_dev_dependency=True
                ))
        except Exception as e:
            logger.debug(f"Error extracting deps from {path}: {e}")
    
    def _extract_maven_deps(self, path: Path) -> None:
        """Extract Maven dependencies."""
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
            
            for dep in root.findall('.//m:dependency', ns) + root.findall('.//dependency'):
                group_ns = dep.find('m:groupId', ns)
                group = group_ns if group_ns is not None else dep.find('groupId')
                artifact_ns = dep.find('m:artifactId', ns)
                artifact = artifact_ns if artifact_ns is not None else dep.find('artifactId')
                version_ns = dep.find('m:version', ns)
                version = version_ns if version_ns is not None else dep.find('version')
                scope_ns = dep.find('m:scope', ns)
                scope = scope_ns if scope_ns is not None else dep.find('scope')
                
                if artifact is not None and artifact.text:
                    name = f"{group.text}:{artifact.text}" if group is not None and group.text else artifact.text
                    self._dependencies.append(DependencyInfo(
                        name=name,
                        version=version.text if version is not None else "unspecified",
                        language="Java",
                        source_file=str(path.relative_to(self.repo_path)),
                        is_dev_dependency=scope is not None and scope.text in ["test", "provided"]
                    ))
        except Exception as e:
            logger.debug(f"Error extracting deps from {path}: {e}")
    
    def _extract_nuget_deps(self, path: Path) -> None:
        """Extract NuGet dependencies from .csproj files."""
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            
            # Handle both old-style and SDK-style .csproj formats
            # SDK-style projects don't have a namespace
            namespaces = [
                {},  # SDK-style (no namespace)
                {'m': 'http://schemas.microsoft.com/developer/msbuild/2003'}  # Old-style
            ]
            
            for ns in namespaces:
                prefix = 'm:' if ns else ''
                
                # Find PackageReference elements (SDK-style and new format)
                for pkg_ref in root.findall(f'.//{prefix}PackageReference', ns):
                    include = pkg_ref.get('Include') or pkg_ref.get('include')
                    version = pkg_ref.get('Version') or pkg_ref.get('version')
                    
                    # Version might be in a child element
                    if not version:
                        version_elem = pkg_ref.find(f'{prefix}Version', ns)
                        if version_elem is not None:
                            version = version_elem.text
                    
                    if include:
                        self._dependencies.append(DependencyInfo(
                            name=include,
                            version=version or "unspecified",
                            language="C#",
                            source_file=str(path.relative_to(self.repo_path)),
                            is_dev_dependency=False
                        ))
                        
                        # Check for framework patterns
                        include_lower = include.lower()
                        for pattern, fw_info in FRAMEWORK_PATTERNS.items():
                            if pattern in include_lower:
                                if not any(f.name == fw_info.name for f in self._frameworks):
                                    self._frameworks.append(FrameworkInfo(
                                        name=fw_info.name,
                                        category=fw_info.category,
                                        package=include,
                                        version=version
                                    ))
                
                # Find Reference elements (old-style)
                for ref in root.findall(f'.//{prefix}Reference', ns):
                    include = ref.get('Include') or ref.get('include')
                    if include:
                        # Parse assembly reference (may include version info)
                        parts = include.split(',')
                        name = parts[0].strip()
                        version = None
                        for part in parts[1:]:
                            if 'Version=' in part:
                                version = part.split('=')[1].strip()
                                break
                        
                        # Skip common .NET framework assemblies
                        if not name.startswith('System.') and not name.startswith('Microsoft.') or \
                           any(fw in name.lower() for fw in ['entityframework', 'aspnet', 'azure']):
                            self._dependencies.append(DependencyInfo(
                                name=name,
                                version=version or "unspecified",
                                language="C#",
                                source_file=str(path.relative_to(self.repo_path)),
                                is_dev_dependency=False
                            ))
                            
        except Exception as e:
            logger.debug(f"Error extracting NuGet deps from {path}: {e}")
    
    def _classify_mvc(self) -> None:
        """Classify files into MVC/architectural layers."""
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if not self._should_skip_dir(d)]
            
            rel_root = Path(root).relative_to(self.repo_path)
            rel_root_str = str(rel_root).lower().replace("\\", "/") + "/"
            
            for file in files:
                file_path = str(rel_root / file).replace("\\", "/")
                
                # Classify based on path patterns
                classified = False
                for layer, patterns in MVC_PATTERNS.items():
                    for pattern in patterns:
                        if pattern in rel_root_str or pattern in file.lower():
                            self._mvc_classification[layer].append(file_path)
                            classified = True
                            break
                    if classified:
                        break
    
    def _extract_classes(self) -> None:
        """Extract classes and their dependencies from Python and C# files."""
        py_files_found = 0
        py_files_skipped = 0
        cs_files_found = 0
        cs_files_skipped = 0
        
        # Extract Python classes
        for py_file in self.repo_path.rglob("*.py"):
            # Skip virtual environments and cache - check path components, not substring
            path_parts = set(py_file.parts)
            if path_parts & self.SKIP_DIRS:
                py_files_skipped += 1
                continue
            
            # Also skip hidden directories
            if any(part.startswith('.') for part in py_file.parts):
                py_files_skipped += 1
                continue
                
            py_files_found += 1
            
            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                tree = ast.parse(content)
                rel_path = str(py_file.relative_to(self.repo_path))
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        class_info = self._extract_class_info(node, rel_path)
                        self._classes.append(class_info)
                        
                        # Build dependency map
                        if class_info.dependencies:
                            self._class_deps[class_info.name] = class_info.dependencies
                            
            except SyntaxError:
                logger.debug(f"Syntax error parsing {py_file}")
            except Exception as e:
                logger.debug(f"Error parsing {py_file}: {e}")
        
        logger.debug(f"Python class extraction: {py_files_found} files analyzed, {py_files_skipped} skipped")
        
        # Extract C# classes using tree-sitter
        if TREE_SITTER_CSHARP_AVAILABLE:
            for cs_file in self.repo_path.rglob("*.cs"):
                # Skip common excluded directories
                path_parts = set(cs_file.parts)
                skip_dirs_cs = self.SKIP_DIRS | {'bin', 'obj', 'packages', '.nuget'}
                if path_parts & skip_dirs_cs:
                    cs_files_skipped += 1
                    continue
                
                # Skip hidden directories
                if any(part.startswith('.') for part in cs_file.parts):
                    cs_files_skipped += 1
                    continue
                
                cs_files_found += 1
                
                try:
                    # Read file as bytes to ensure byte offsets match tree-sitter
                    with open(cs_file, 'rb') as f:
                        content_bytes = f.read()
                    
                    # Remove BOM if present (UTF-8 BOM is 3 bytes: EF BB BF)
                    if content_bytes.startswith(b'\xef\xbb\xbf'):
                        content_bytes = content_bytes[3:]
                    
                    # Decode for string operations
                    content = content_bytes.decode('utf-8', errors='ignore')
                    
                    rel_path = str(cs_file.relative_to(self.repo_path))
                    classes = self._extract_csharp_classes(content_bytes, content, rel_path)
                    
                    for class_info in classes:
                        self._classes.append(class_info)
                        if class_info.dependencies:
                            self._class_deps[class_info.name] = class_info.dependencies
                            
                except Exception as e:
                    logger.debug(f"Error parsing C# file {cs_file}: {e}")
            
            logger.debug(f"C# class extraction: {cs_files_found} files analyzed, {cs_files_skipped} skipped")
        else:
            logger.debug("tree-sitter-c-sharp not available, skipping C# class extraction")
        
        # Extract Java classes using tree-sitter
        java_files_found = 0
        java_files_skipped = 0
        
        if TREE_SITTER_JAVA_AVAILABLE:
            for java_file in self.repo_path.rglob("*.java"):
                # Skip common excluded directories
                path_parts = set(java_file.parts)
                skip_dirs_java = self.SKIP_DIRS | {'target', 'build', '.gradle', '.mvn'}
                if path_parts & skip_dirs_java:
                    java_files_skipped += 1
                    continue
                
                # Skip hidden directories
                if any(part.startswith('.') for part in java_file.parts):
                    java_files_skipped += 1
                    continue
                
                java_files_found += 1
                
                try:
                    # Read file as bytes to ensure byte offsets match tree-sitter
                    with open(java_file, 'rb') as f:
                        content_bytes = f.read()
                    
                    # Remove BOM if present (UTF-8 BOM is 3 bytes: EF BB BF)
                    if content_bytes.startswith(b'\xef\xbb\xbf'):
                        content_bytes = content_bytes[3:]
                    
                    rel_path = str(java_file.relative_to(self.repo_path))
                    classes = self._extract_java_classes(content_bytes, rel_path)
                    
                    for class_info in classes:
                        self._classes.append(class_info)
                        if class_info.dependencies:
                            self._class_deps[class_info.name] = class_info.dependencies
                            
                except Exception as e:
                    logger.debug(f"Error parsing Java file {java_file}: {e}")
            
            logger.debug(f"Java class extraction: {java_files_found} files analyzed, {java_files_skipped} skipped")
        else:
            logger.debug("tree-sitter-java not available, skipping Java class extraction")
        
        logger.debug(f"Total classes found: {len(self._classes)}")
    
    def _extract_csharp_classes(self, content_bytes: bytes, content: str, file_path: str) -> List[ClassInfo]:
        """
        Extract classes from C# code using tree-sitter.
        
        Args:
            content_bytes: Raw bytes of the C# source code (for tree-sitter)
            content: Decoded string content (for text extraction)
            file_path: Relative path to the file
            
        Returns:
            List of ClassInfo objects for each class found
        """
        if not TREE_SITTER_CSHARP_AVAILABLE:
            return []
        
        classes = []
        
        try:
            # Initialize tree-sitter C# parser
            CS_LANGUAGE = Language(tscsharp.language())
            parser = Parser(CS_LANGUAGE)
            
            # Parse the C# code using the raw bytes
            tree = parser.parse(content_bytes)
            root_node = tree.root_node
            
            # Find all class declarations
            class_nodes = self._find_csharp_nodes(root_node, 'class_declaration')
            
            for class_node in class_nodes:
                class_info = self._extract_csharp_class_info(class_node, content_bytes, file_path)
                if class_info:
                    classes.append(class_info)
            
            # Also find struct declarations (similar to classes in C#)
            struct_nodes = self._find_csharp_nodes(root_node, 'struct_declaration')
            for struct_node in struct_nodes:
                class_info = self._extract_csharp_class_info(struct_node, content_bytes, file_path, is_struct=True)
                if class_info:
                    classes.append(class_info)
            
            # Find interface declarations
            interface_nodes = self._find_csharp_nodes(root_node, 'interface_declaration')
            for interface_node in interface_nodes:
                class_info = self._extract_csharp_interface_info(interface_node, content_bytes, file_path)
                if class_info:
                    classes.append(class_info)
                    
        except Exception as e:
            logger.debug(f"Error in C# tree-sitter parsing for {file_path}: {e}")
        
        return classes
    
    def _find_csharp_nodes(self, node, node_type: str) -> List:
        """Recursively find all nodes of a specific type in the tree."""
        results = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_csharp_nodes(child, node_type))
        return results
    
    def _get_node_text(self, node, content_bytes: bytes) -> str:
        """Get the text content of a tree-sitter node."""
        text_bytes = content_bytes[node.start_byte:node.end_byte]
        text = text_bytes.decode('utf-8', errors='ignore')
        # Clean up any newlines or extra whitespace
        return text.strip().replace('\n', '').replace('\r', '')
    
    def _is_valid_csharp_identifier(self, name: str) -> bool:
        """Check if a string is a valid C# identifier."""
        if not name or len(name) < 2:
            return False
        # Must start with letter or underscore
        if not (name[0].isalpha() or name[0] == '_'):
            return False
        # Rest must be alphanumeric or underscore
        return all(c.isalnum() or c == '_' for c in name)
    
    def _extract_csharp_class_info(self, class_node, content_bytes: bytes, file_path: str, is_struct: bool = False) -> Optional[ClassInfo]:
        """Extract class information from a C# class_declaration node."""
        class_name = None
        base_classes = []
        methods = []
        dependencies = set()
        
        for child in class_node.children:
            # Get class name - only from direct identifier child (not nested)
            if child.type == 'identifier' and class_name is None:
                class_name = self._get_node_text(child, content_bytes)
                # Ensure class name is clean (no colons, braces, etc.)
                class_name = class_name.split(':')[0].split('{')[0].strip()
                # Validate it's a proper identifier
                if not self._is_valid_csharp_identifier(class_name):
                    class_name = None
                    continue
            
            # Get base classes/interfaces from base_list
            elif child.type == 'base_list':
                base_classes = self._extract_base_types(child, content_bytes)
                # Filter to only valid identifiers
                base_classes = [b for b in base_classes if self._is_valid_csharp_identifier(b)]
            
            # Get methods from declaration_list (class body)
            elif child.type == 'declaration_list':
                for member in child.children:
                    if member.type == 'method_declaration':
                        for method_child in member.children:
                            if method_child.type == 'identifier':
                                method_name = self._get_node_text(method_child, content_bytes)
                                if method_name and not method_name.startswith('{') and self._is_valid_csharp_identifier(method_name):
                                    methods.append(method_name)
                                break
                    elif member.type == 'constructor_declaration':
                        for ctor_child in member.children:
                            if ctor_child.type == 'identifier':
                                ctor_name = self._get_node_text(ctor_child, content_bytes)
                                if ctor_name and not ctor_name.startswith('{') and self._is_valid_csharp_identifier(ctor_name):
                                    methods.append(ctor_name)
                                break
                    
                    # Extract type dependencies from fields, properties, parameters
                    self._extract_csharp_type_dependencies(member, content_bytes, dependencies)
        
        if not class_name:
            return None
        
        # Determine layer
        layer = self._determine_class_layer(class_name, file_path)
        
        # Remove base classes from dependencies
        deps_list = list(dependencies - set(base_classes) - {class_name})
        
        return ClassInfo(
            name=class_name,
            file_path=file_path,
            base_classes=base_classes,
            methods=methods,
            dependencies=deps_list,
            layer=layer
        )
    
    def _extract_base_types(self, base_list_node, content_bytes: bytes) -> List[str]:
        """Extract base class/interface names from a base_list node."""
        base_types = []
        
        for child in base_list_node.children:
            # Skip punctuation like ':' and ','
            if child.type in (':', ','):
                continue
            
            base_name = None
            
            # Direct identifier
            if child.type == 'identifier':
                base_name = self._get_node_text(child, content_bytes)
            
            # Generic name like List<T>
            elif child.type == 'generic_name':
                # Get just the identifier part
                for gc in child.children:
                    if gc.type == 'identifier':
                        base_name = self._get_node_text(gc, content_bytes)
                        break
            
            # Qualified name like System.Collections.Generic.IList
            elif child.type == 'qualified_name':
                # Get the last identifier (the actual type name)
                base_name = self._get_node_text(child, content_bytes)
                if '.' in base_name:
                    base_name = base_name.split('.')[-1]
            
            # simple_base_type contains the actual type
            elif child.type == 'simple_base_type':
                for type_child in child.children:
                    if type_child.type == 'identifier':
                        base_name = self._get_node_text(type_child, content_bytes)
                        break
                    elif type_child.type == 'generic_name':
                        for gc in type_child.children:
                            if gc.type == 'identifier':
                                base_name = self._get_node_text(gc, content_bytes)
                                break
                        break
                    elif type_child.type == 'qualified_name':
                        base_name = self._get_node_text(type_child, content_bytes)
                        if '.' in base_name:
                            base_name = base_name.split('.')[-1]
                        break
            
            if base_name:
                # Clean up - remove any trailing punctuation or braces
                base_name = base_name.split('<')[0].split('{')[0].split(':')[0].strip()
                if base_name and base_name not in base_types and self._is_valid_csharp_identifier(base_name):
                    base_types.append(base_name)
        
        return base_types
    
    def _extract_csharp_interface_info(self, interface_node, content_bytes: bytes, file_path: str) -> Optional[ClassInfo]:
        """Extract interface information from a C# interface_declaration node."""
        interface_name = None
        base_interfaces = []
        methods = []
        
        for child in interface_node.children:
            if child.type == 'identifier' and interface_name is None:
                interface_name = self._get_node_text(child, content_bytes)
                # Clean up interface name
                interface_name = interface_name.split(':')[0].split('{')[0].strip()
                # Validate it's a proper identifier
                if not self._is_valid_csharp_identifier(interface_name):
                    interface_name = None
                    continue
            elif child.type == 'base_list':
                base_interfaces = self._extract_base_types(child, content_bytes)
            elif child.type == 'declaration_list':
                for member in child.children:
                    if member.type == 'method_declaration':
                        for method_child in member.children:
                            if method_child.type == 'identifier':
                                method_name = self._get_node_text(method_child, content_bytes)
                                if method_name and not method_name.startswith('{') and self._is_valid_csharp_identifier(method_name):
                                    methods.append(method_name)
                                break
        
        if not interface_name:
            return None
        
        layer = self._determine_class_layer(interface_name, file_path)
        
        return ClassInfo(
            name=interface_name,
            file_path=file_path,
            base_classes=base_interfaces,
            methods=methods,
            dependencies=[],
            layer=layer
        )
    
    def _extract_csharp_type_dependencies(self, node, content_bytes: bytes, dependencies: Set[str]) -> None:
        """Recursively extract type dependencies from a C# AST node."""
        # Only look at specific node types that represent type references
        # Skip attribute_list nodes entirely - they contain attribute names, not type dependencies
        if node.type == 'attribute_list':
            return
        
        # Skip argument nodes - they often contain property/method names, not types
        if node.type == 'argument':
            return
        
        # Types that indicate actual type dependencies
        type_context_nodes = {
            'type', 'predefined_type', 'nullable_type', 'array_type',
            'object_creation_expression', 'variable_declaration',
            'parameter', 'type_parameter', 'base_list'
        }
        
        # Skip common built-in types and common false positives
        builtin_and_skip_types = {
            # C# built-in types
            'string', 'int', 'bool', 'void', 'object', 'double', 'float', 'decimal',
            'long', 'short', 'byte', 'char', 'uint', 'ulong', 'ushort', 'sbyte',
            'dynamic', 'var', 'Task', 'Action', 'Func', 'IEnumerable', 'List',
            'Dictionary', 'HashSet', 'Array', 'String', 'Int32', 'Int64', 'Boolean',
            'Object', 'Exception', 'DateTime', 'TimeSpan', 'Guid', 'Type',
            'ILogger', 'IConfiguration', 'IOptions', 'CancellationToken',
            'HttpContext', 'IActionResult', 'ActionResult', 'IResult',
            # Common attribute names (not types)
            'Required', 'MinLength', 'MaxLength', 'EmailAddress', 'Compare',
            'JsonIgnore', 'JsonProperty', 'JsonPropertyName', 'NotMapped',
            'Key', 'ForeignKey', 'Column', 'Table', 'Index',
            'Authorize', 'AllowAnonymous', 'Route', 'HttpGet', 'HttpPost', 'HttpPut', 'HttpDelete',
            'ApiController', 'Controller', 'FromBody', 'FromQuery', 'FromRoute',
            'Obsolete', 'Serializable', 'DataContract', 'DataMember',
            # Common method/property names that look like types
            'Title', 'Name', 'FirstName', 'LastName', 'Email', 'Password',
            'Id', 'Status', 'Message', 'Error', 'Result', 'Value', 'Count',
            'Format', 'ToString', 'Parse', 'TryParse', 'Equals', 'GetHashCode',
            'Serialize', 'Deserialize', 'Configure', 'Add', 'Remove', 'Update',
            'Get', 'Set', 'Delete', 'Create', 'Save', 'Load', 'Find', 'Search',
            'CurrentCulture', 'InvariantCulture', 'CultureInfo',
            'IsNullOrEmpty', 'IsNullOrWhiteSpace',
            'StatusCode', 'BadRequest', 'Ok', 'NotFound', 'Unauthorized',
            'OnConfiguring', 'OnModelCreating', 'SaveChanges', 'SaveChangesAsync',
            'UseInMemoryDatabase', 'UseSqlServer', 'UseNpgsql',
            'DestinationMember', 'ForAllMembers', 'ForMember', 'MapFrom',
            'HashPassword', 'VerifyPassword',
        }
        
        # Only extract types from nodes that represent type usage contexts
        if node.type == 'identifier':
            # Check if parent is a type-related node
            parent = node.parent
            if parent and parent.type in type_context_nodes:
                type_name = self._get_node_text(node, content_bytes)
                # Clean up
                type_name = type_name.split('<')[0].split('.')[0].strip()
                # Only add if it looks like a user-defined type
                if (type_name and 
                    len(type_name) > 1 and 
                    type_name[0].isupper() and 
                    type_name not in builtin_and_skip_types and
                    self._is_valid_csharp_identifier(type_name)):
                    dependencies.add(type_name)
        
        # Recurse into children
        for child in node.children:
            self._extract_csharp_type_dependencies(child, content_bytes, dependencies)
    
    # =========================================================================
    # JAVA TREE-SITTER METHODS
    # =========================================================================
    
    def _extract_java_classes(self, content_bytes: bytes, file_path: str) -> List[ClassInfo]:
        """
        Extract classes from Java code using tree-sitter.
        
        Args:
            content_bytes: Raw bytes of the Java source code
            file_path: Relative path to the file
            
        Returns:
            List of ClassInfo objects for each class/interface found
        """
        if not TREE_SITTER_JAVA_AVAILABLE:
            return []
        
        classes = []
        
        try:
            # Initialize tree-sitter Java parser
            JAVA_LANGUAGE = Language(tsjava.language())
            parser = Parser(JAVA_LANGUAGE)
            
            # Parse the Java code using the raw bytes
            tree = parser.parse(content_bytes)
            root_node = tree.root_node
            
            # Find all class declarations
            class_nodes = self._find_java_nodes(root_node, 'class_declaration')
            for class_node in class_nodes:
                class_info = self._extract_java_class_info(class_node, content_bytes, file_path)
                if class_info:
                    classes.append(class_info)
            
            # Find interface declarations
            interface_nodes = self._find_java_nodes(root_node, 'interface_declaration')
            for interface_node in interface_nodes:
                class_info = self._extract_java_interface_info(interface_node, content_bytes, file_path)
                if class_info:
                    classes.append(class_info)
            
            # Find enum declarations
            enum_nodes = self._find_java_nodes(root_node, 'enum_declaration')
            for enum_node in enum_nodes:
                class_info = self._extract_java_enum_info(enum_node, content_bytes, file_path)
                if class_info:
                    classes.append(class_info)
                    
        except Exception as e:
            logger.debug(f"Error in Java tree-sitter parsing for {file_path}: {e}")
        
        return classes
    
    def _find_java_nodes(self, node, node_type: str) -> List:
        """Recursively find all nodes of a specific type in the Java AST."""
        results = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_java_nodes(child, node_type))
        return results
    
    def _get_java_node_text(self, node, content_bytes: bytes) -> str:
        """Get the text content of a tree-sitter node for Java."""
        text_bytes = content_bytes[node.start_byte:node.end_byte]
        text = text_bytes.decode('utf-8', errors='ignore')
        return text.strip().replace('\n', '').replace('\r', '')
    
    def _is_valid_java_identifier(self, name: str) -> bool:
        """Check if a string is a valid Java identifier."""
        if not name or len(name) < 2:
            return False
        # Must start with letter or underscore or $
        if not (name[0].isalpha() or name[0] in '_$'):
            return False
        # Rest must be alphanumeric or underscore or $
        return all(c.isalnum() or c in '_$' for c in name)
    
    def _extract_java_class_info(self, class_node, content_bytes: bytes, file_path: str) -> Optional[ClassInfo]:
        """Extract class information from a Java class_declaration node."""
        class_name = None
        base_classes = []
        interfaces = []
        methods = []
        dependencies = set()
        
        for child in class_node.children:
            # Get class name
            if child.type == 'identifier' and class_name is None:
                class_name = self._get_java_node_text(child, content_bytes)
                if not self._is_valid_java_identifier(class_name):
                    class_name = None
                    continue
            
            # Get superclass from superclass node
            elif child.type == 'superclass':
                for sc_child in child.children:
                    if sc_child.type == 'type_identifier':
                        base_name = self._get_java_node_text(sc_child, content_bytes)
                        if self._is_valid_java_identifier(base_name):
                            base_classes.append(base_name)
            
            # Get interfaces from super_interfaces node
            elif child.type == 'super_interfaces':
                for iface_child in child.children:
                    if iface_child.type == 'type_list':
                        for type_child in iface_child.children:
                            if type_child.type == 'type_identifier':
                                iface_name = self._get_java_node_text(type_child, content_bytes)
                                if self._is_valid_java_identifier(iface_name):
                                    interfaces.append(iface_name)
            
            # Get methods from class_body
            elif child.type == 'class_body':
                for member in child.children:
                    if member.type == 'method_declaration':
                        for method_child in member.children:
                            if method_child.type == 'identifier':
                                method_name = self._get_java_node_text(method_child, content_bytes)
                                if self._is_valid_java_identifier(method_name):
                                    methods.append(method_name)
                                break
                    elif member.type == 'constructor_declaration':
                        for ctor_child in member.children:
                            if ctor_child.type == 'identifier':
                                ctor_name = self._get_java_node_text(ctor_child, content_bytes)
                                if self._is_valid_java_identifier(ctor_name):
                                    methods.append(ctor_name)
                                break
                    
                    # Extract type dependencies from fields, parameters, etc.
                    self._extract_java_type_dependencies(member, content_bytes, dependencies)
        
        if not class_name:
            return None
        
        # Combine base class and interfaces
        all_bases = base_classes + interfaces
        
        # Determine layer
        layer = self._determine_class_layer(class_name, file_path)
        
        # Remove base classes from dependencies
        deps_list = list(dependencies - set(all_bases) - {class_name})
        
        return ClassInfo(
            name=class_name,
            file_path=file_path,
            base_classes=all_bases,
            methods=methods,
            dependencies=deps_list,
            layer=layer
        )
    
    def _extract_java_interface_info(self, interface_node, content_bytes: bytes, file_path: str) -> Optional[ClassInfo]:
        """Extract interface information from a Java interface_declaration node."""
        interface_name = None
        extends_interfaces = []
        methods = []
        
        for child in interface_node.children:
            if child.type == 'identifier' and interface_name is None:
                interface_name = self._get_java_node_text(child, content_bytes)
                if not self._is_valid_java_identifier(interface_name):
                    interface_name = None
                    continue
            
            # Get extended interfaces
            elif child.type == 'extends_interfaces':
                for ext_child in child.children:
                    if ext_child.type == 'type_list':
                        for type_child in ext_child.children:
                            if type_child.type == 'type_identifier':
                                ext_name = self._get_java_node_text(type_child, content_bytes)
                                if self._is_valid_java_identifier(ext_name):
                                    extends_interfaces.append(ext_name)
            
            # Get methods from interface_body
            elif child.type == 'interface_body':
                for member in child.children:
                    if member.type == 'method_declaration':
                        for method_child in member.children:
                            if method_child.type == 'identifier':
                                method_name = self._get_java_node_text(method_child, content_bytes)
                                if self._is_valid_java_identifier(method_name):
                                    methods.append(method_name)
                                break
        
        if not interface_name:
            return None
        
        layer = self._determine_class_layer(interface_name, file_path)
        
        return ClassInfo(
            name=interface_name,
            file_path=file_path,
            base_classes=extends_interfaces,
            methods=methods,
            dependencies=[],
            layer=layer
        )
    
    def _extract_java_enum_info(self, enum_node, content_bytes: bytes, file_path: str) -> Optional[ClassInfo]:
        """Extract enum information from a Java enum_declaration node."""
        enum_name = None
        interfaces = []
        methods = []
        
        for child in enum_node.children:
            if child.type == 'identifier' and enum_name is None:
                enum_name = self._get_java_node_text(child, content_bytes)
                if not self._is_valid_java_identifier(enum_name):
                    enum_name = None
                    continue
            
            # Get implemented interfaces
            elif child.type == 'super_interfaces':
                for iface_child in child.children:
                    if iface_child.type == 'type_list':
                        for type_child in iface_child.children:
                            if type_child.type == 'type_identifier':
                                iface_name = self._get_java_node_text(type_child, content_bytes)
                                if self._is_valid_java_identifier(iface_name):
                                    interfaces.append(iface_name)
            
            # Get methods from enum_body
            elif child.type == 'enum_body':
                for member in child.children:
                    if member.type == 'method_declaration':
                        for method_child in member.children:
                            if method_child.type == 'identifier':
                                method_name = self._get_java_node_text(method_child, content_bytes)
                                if self._is_valid_java_identifier(method_name):
                                    methods.append(method_name)
                                break
        
        if not enum_name:
            return None
        
        layer = self._determine_class_layer(enum_name, file_path)
        
        return ClassInfo(
            name=enum_name,
            file_path=file_path,
            base_classes=interfaces,
            methods=methods,
            dependencies=[],
            layer=layer
        )
    
    def _extract_java_type_dependencies(self, node, content_bytes: bytes, dependencies: Set[str]) -> None:
        """Recursively extract type dependencies from a Java AST node."""
        # Skip annotation nodes - they contain annotation names, not type dependencies
        if node.type in ('annotation', 'marker_annotation', 'annotation_argument_list'):
            return
        
        # Types that indicate actual type dependencies
        type_context_nodes = {
            'type_identifier', 'generic_type', 'object_creation_expression',
            'field_declaration', 'formal_parameter', 'local_variable_declaration'
        }
        
        # Skip common built-in types and Java standard library types
        builtin_and_skip_types = {
            # Java primitives and wrappers
            'int', 'long', 'short', 'byte', 'float', 'double', 'boolean', 'char', 'void',
            'Integer', 'Long', 'Short', 'Byte', 'Float', 'Double', 'Boolean', 'Character',
            'String', 'Object', 'Class', 'Void',
            # Common Java types
            'List', 'ArrayList', 'LinkedList', 'Map', 'HashMap', 'TreeMap', 'LinkedHashMap',
            'Set', 'HashSet', 'TreeSet', 'Collection', 'Collections', 'Arrays',
            'Optional', 'Stream', 'Collectors',
            'Date', 'Calendar', 'LocalDate', 'LocalDateTime', 'LocalTime', 'Instant',
            'Exception', 'RuntimeException', 'Throwable', 'Error',
            'Thread', 'Runnable', 'Callable', 'Future', 'CompletableFuture',
            'File', 'Path', 'Files', 'Paths',
            'StringBuilder', 'StringBuffer', 'Pattern', 'Matcher',
            # Common annotations (not types)
            'Override', 'Deprecated', 'SuppressWarnings', 'FunctionalInterface',
            'Autowired', 'Component', 'Service', 'Repository', 'Controller', 'RestController',
            'RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping',
            'PathVariable', 'RequestBody', 'RequestParam', 'ResponseBody',
            'Entity', 'Table', 'Column', 'Id', 'GeneratedValue', 'ManyToOne', 'OneToMany',
            'Transactional', 'Bean', 'Configuration', 'Value', 'Qualifier',
            'Test', 'Before', 'After', 'BeforeEach', 'AfterEach',
            # Common method/field names
            'Id', 'Name', 'Type', 'Value', 'Size', 'Length', 'Count',
        }
        
        # Extract type_identifier nodes
        if node.type == 'type_identifier':
            type_name = self._get_java_node_text(node, content_bytes)
            # Clean up generic types
            type_name = type_name.split('<')[0].strip()
            if (type_name and 
                len(type_name) > 1 and 
                type_name[0].isupper() and 
                type_name not in builtin_and_skip_types and
                self._is_valid_java_identifier(type_name)):
                dependencies.add(type_name)
        
        # Recurse into children
        for child in node.children:
            self._extract_java_type_dependencies(child, content_bytes, dependencies)
    
    def _extract_class_info(self, node: ast.ClassDef, file_path: str) -> ClassInfo:
        """Extract information about a class."""
        # Get base classes
        base_classes = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_classes.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_classes.append(base.attr)
        
        # Get methods
        methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        
        # Extract dependencies (other classes referenced)
        dependencies = set()
        for item in ast.walk(node):
            if isinstance(item, ast.Name):
                # Heuristic: class names start with uppercase
                if item.id and item.id[0].isupper() and item.id != node.name:
                    dependencies.add(item.id)
            elif isinstance(item, ast.Attribute):
                if item.attr and item.attr[0].isupper():
                    dependencies.add(item.attr)
        
        # Determine layer based on class name and location
        layer = self._determine_class_layer(node.name, file_path)
        
        return ClassInfo(
            name=node.name,
            file_path=file_path,
            base_classes=base_classes,
            methods=methods,
            dependencies=list(dependencies - set(base_classes)),
            layer=layer
        )
    
    def _determine_class_layer(self, class_name: str, file_path: str) -> str:
        """Determine the architectural layer of a class."""
        name_lower = class_name.lower()
        path_lower = file_path.lower()
        
        # Check by name suffix
        if any(s in name_lower for s in ["model", "entity", "schema"]):
            return "model"
        if any(s in name_lower for s in ["view", "template", "component"]):
            return "view"
        if any(s in name_lower for s in ["controller", "handler", "router", "endpoint"]):
            return "controller"
        if any(s in name_lower for s in ["service", "usecase", "business"]):
            return "service"
        if any(s in name_lower for s in ["repository", "repo", "dao", "dal"]):
            return "repository"
        if any(s in name_lower for s in ["util", "helper", "common"]):
            return "utility"
        if any(s in name_lower for s in ["test", "spec"]):
            return "test"
        
        # Check by path
        for layer, patterns in MVC_PATTERNS.items():
            for pattern in patterns:
                if pattern.rstrip("/") in path_lower:
                    return layer
        
        return "unknown"
    
    def _generate_mermaid_class_diagram(self) -> str:
        """Generate Mermaid class diagram from class dependencies."""
        lines = ["classDiagram"]
        
        # Limit to avoid huge diagrams
        max_classes = 30
        relevant_classes = [c for c in self._classes if c.base_classes or c.dependencies][:max_classes]
        
        seen_relationships = set()
        
        for cls in relevant_classes:
            # Add inheritance relationships
            for base in cls.base_classes:
                rel = f"    {base} <|-- {cls.name}"
                if rel not in seen_relationships:
                    lines.append(rel)
                    seen_relationships.add(rel)
            
            # Add dependency relationships (limit per class)
            for dep in cls.dependencies[:5]:
                rel = f"    {cls.name} --> {dep}"
                if rel not in seen_relationships:
                    lines.append(rel)
                    seen_relationships.add(rel)
        
        if len(lines) == 1:
            return ""  # No relationships found
        
        return "\n".join(lines)
    
    def _generate_mermaid_dependency_diagram(self) -> str:
        """Generate Mermaid flowchart showing module dependencies."""
        lines = ["flowchart LR"]
        
        # Group classes by file/module
        module_deps: Dict[str, Set[str]] = defaultdict(set)
        
        for cls in self._classes:
            # Get module name from path
            module = Path(cls.file_path).stem
            for dep in cls.dependencies[:3]:  # Limit deps per class
                # Find which module the dep belongs to
                for other_cls in self._classes:
                    if other_cls.name == dep:
                        other_module = Path(other_cls.file_path).stem
                        if module != other_module:
                            module_deps[module].add(other_module)
        
        # Generate diagram (limit connections)
        seen = set()
        count = 0
        for module, deps in module_deps.items():
            for dep in list(deps)[:3]:
                rel = f"    {module} --> {dep}"
                if rel not in seen and count < 30:
                    lines.append(rel)
                    seen.add(rel)
                    count += 1
        
        if len(lines) == 1:
            return ""  # No relationships found
        
        return "\n".join(lines)


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def analyze_codebase(repo_path: str) -> CodebaseAnalysisResult:
    """
    Convenience function to analyze a codebase.
    
    Args:
        repo_path: Path to the repository
        
    Returns:
        CodebaseAnalysisResult with complete analysis
    """
    analyzer = CodebaseAnalyzer(repo_path)
    return analyzer.analyze()


def get_codebase_markdown_section(repo_path: str) -> str:
    """
    Get the markdown section for a codebase analysis.
    
    Args:
        repo_path: Path to the repository
        
    Returns:
        Markdown string to insert into reports
    """
    result = analyze_codebase(repo_path)
    return result.to_markdown_section()
