# Azure AI Multi-Agent Code Analyzer

A pure Semantic Kernel implementation for intelligent code analysis through configurable multi-agent coordination using Azure AI Services.

## 🚀 Overview

This platform leverages Azure AI Agents (via Semantic Kernel) to perform security analysis and code review through coordinated agent interactions. The system features an intelligent orchestrator that manages group chat orchestration based on configurable JSON-driven agent definitions.

### Key Capabilities

- **Configurable Agent Workflows**: Define complex multi-step processes through simple JSON configuration
- **Intelligent Agent Orchestration**: Dynamic agent selection and workflow management via `GroupChatOrchestration`
- **Security Scanning**: Automated secret detection and security vulnerability analysis
- **File Generation & Processing**: Automated report generation with organized output management
- **Unified Plugin System**: All functionality consolidated into `CodeAnalyzerPlugin`

## 🏗️ Architecture

### Core Components

- **SemanticKernelCodeAnalyzer**: Main class that orchestrates the entire analysis workflow
- **CodeAnalyzerGroupChatManager**: Custom group chat manager for managing agent turns and termination
- **CodeAnalyzerPlugin**: Unified plugin with all kernel functions (file ops, security scanning)

### Agent Coordination

The platform uses Semantic Kernel's `GroupChatOrchestration` with custom manager:
1. An Orchestrator Agent analyzes the conversation flow and selects the next agent
2. Specialized agents (configured via JSON) perform their designated analysis tasks
3. Each agent has access to tools via `CodeAnalyzerPlugin` and `CodeInterpreterTool`
4. The process continues until the orchestrator signals "PERFECTUS" (completion)

## 📋 Prerequisites

- Python 3.11+
- Azure subscription with AI Services
- Azure AI Projects resource
- Proper Azure credentials configured

### Required Dependencies

```bash
pip install -r requirements.txt
```

Core dependencies:
- `semantic-kernel==1.29.0`
- `azure-ai-projects==1.0.0b10`
- `azure-identity==1.22.0`

## 🔧 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd <project-directory>
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   Create a `.env` file with your Azure credentials:
   ```env
   AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME=your_base_model_deployment_name
   AZURE_AI_AGENT_ENDPOINT=azure_ai_foundry_endpoint
   AZURE_AI_AGENT_PROJECT_CONNECTION_STRING=azure_ai_foundry_project_connection_string
   ```

4. **Set up Azure AI Services**:
   - Create an Azure AI Projects resource
   - Configure appropriate permissions for agent creation and file upload

## 🚀 Usage

### Basic Execution

```bash
python main.py
```

When prompted, enter the configuration folder name containing your agent definitions.

### Agent Configuration

Define your workflow through JSON configuration:

```json
{
    "agents": [
        {
            "name": "AnalyzerAgent",
            "model": "gpt-4.1",
            "instructions_file": "analyzer_instructions.txt",
            "file_writer": false,
            "description": "Analyzes input data and extracts key insights"
        },
        {
            "name": "GeneratorAgent",
            "instructions_file": "generator_instructions.txt",
            "model": "gpt-4.1",
            "file_writer": true,
            "description": "Generates content based on analysis results"
        }
    ],
    "base_model": "gpt-4.1",
    "initial_message": "Process the uploaded files and generate comprehensive output",
    "uploads": true
}
```

### Configuration Structure

```
code_analyzer/
├── kinfosec/                       # JavaScript/TypeScript analysis config
│   ├── config.json                 # Agent workflow configuration
│   └── analyst_instructions.txt    # Agent instructions
├── terrasec/                       # Terraform analysis config
│   ├── config.json                 # Agent workflow configuration
│   ├── terraform_expert.txt        # Terraform analysis instructions
│   └── security_expert.txt         # Security assessment instructions
├── src/
│   └── plugins/
│       └── code_analyzer_plugin.py # Unified plugin with all kernel functions
├── modified_files/                 # Generated output reports
├── semantic_kernel_analyzer.py     # Main analyzer implementation
├── __init__.py                     # Package exports
└── readme.md                       # This file
```

## 🔄 How It Works

### Agent Lifecycle

1. **Configuration Loading**: System validates and loads agent definitions from JSON
2. **Agent Creation**: Azure AI Agents are instantiated with specified models and instructions
3. **Orchestration Setup**: Query orchestrator is configured with knowledge of available agents
4. **Workflow Execution**: Agents collaborate based on conversation context and task requirements
5. **Cleanup**: All created agents are properly cleaned up after workflow completion

### Intelligent Agent Selection

The Query Orchestrator uses sophisticated logic to:
- Analyze conversation history and current context
- Understand what tasks have been completed
- Determine the next appropriate agent based on workflow state
- Handle error conditions and agent coordination

### File Processing

- **Upload Support**: Seamlessly upload files to Azure AI Agent service for processing
- **Organized Output**: Generated files are structured in the `modified_files/` directory
- **Plugin Architecture**: Extensible file handling through the plugin system

## 🛠️ Key Features

### Configuration-Driven Design

- **Flexible Agent Definitions**: Define any number of specialized agents through JSON
- **Custom Instructions**: Agent behavior controlled through instruction files
- **Model Selection**: Support for different AI models per agent
- **Capability Flags**: Enable/disable features like file writing and code interpretation

### Robust Error Handling

- **Validation**: Comprehensive configuration validation with detailed error messages
- **Recovery**: Graceful handling of agent creation failures and network issues
- **Cleanup**: Automatic resource cleanup even in error scenarios
- **Logging**: Detailed logging for debugging and monitoring

### Security & Safety

- **Credential Management**: Secure Azure credential handling with Managed Identity support
- **Path Validation**: Prevents directory traversal attacks in file operations
- **Input Sanitization**: Comprehensive validation of all configuration inputs
- **Resource Limits**: File size and type restrictions for safe processing

### Performance & Scalability

- **Async Operations**: Full asynchronous support for optimal performance
- **Timeout Management**: Configurable timeouts for long-running operations
- **Resource Optimization**: Efficient agent lifecycle management
- **Concurrent Processing**: Support for parallel agent operations

## 🔌 Extensibility

### Plugin System

The platform supports custom plugins through the Semantic Kernel framework:

```python
@kernel_function(description="Custom function description")
def custom_function(self, parameter: str) -> str:
    # Your custom logic here
    return result
```

### Custom Agents

Create specialized agents by:
1. Defining custom instruction files
2. Configuring specific models and capabilities
3. Adding custom plugins for domain-specific functionality

### Integration Points

- **Azure Services**: Native integration with Azure AI Services
- **File Systems**: Configurable input/output file handling
- **External APIs**: Plugin-based integration capabilities
- **Monitoring**: Built-in logging and error reporting

## 📊 Monitoring and Observability

### Built-in Logging

- Agent creation and lifecycle events
- Workflow progression and agent selection
- Error conditions and recovery actions
- Performance metrics and timing information

### Configuration Validation

- JSON schema validation
- Required field verification
- File existence and accessibility checks
- Model and capability validation

## 🚨 Error Handling

The system provides comprehensive error handling for:

- **Azure Authentication**: Credential validation and token management
- **Agent Operations**: Creation, execution, and cleanup failures
- **File Operations**: Upload, processing, and output generation issues
- **Configuration Issues**: Invalid JSON, missing files, or malformed settings
- **Network Connectivity**: Azure service availability and timeout handling

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with appropriate tests
4. Commit your changes (`git commit -m 'Add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

### Development Guidelines

- Follow Python PEP 8 style guidelines
- Add comprehensive docstrings to all functions
- Include error handling for all external operations
- Write tests for new functionality
- Update documentation for user-facing changes


## 🆘 Troubleshooting

### Common Issues

1. **Azure Authentication Errors**
   - Verify Azure credentials in `.env` file
   - Ensure Azure AI Projects resource is accessible
   - Check service principal permissions

2. **Agent Creation Failures**
   - Validate model names and availability
   - Check Azure resource quotas and limits
   - Verify instruction file accessibility

3. **Configuration Errors**
   - Validate JSON syntax in configuration files
   - Ensure all required fields are present
   - Check file paths and accessibility

4. **File Processing Issues**
   - Verify file upload permissions
   - Check file size and type restrictions
   - Ensure output directory is writable

### Debug Mode

Enable detailed logging by setting environment variables:
```bash
export PYTHONPATH=$PYTHONPATH:.
export LOG_LEVEL=DEBUG
```

---

*Intelligent Workflow Automation Through Configurable AI Agent Orchestration*