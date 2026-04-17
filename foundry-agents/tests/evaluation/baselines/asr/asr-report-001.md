# Application Summary Report (ASR) Template

## 1. Application Overview

Below is a structured summary of the application lifecycle and environment classification, based strictly on indexed records:

| Environment   | Business Criticality | Service Category | Information Classification |
|---------------|---------------------|------------------|---------------------------|
| Development   | -                   | Bronze           | C2 (General)              |
| SIT           | -                   | -                | -                         |
| Test          | -                   | Silver           | C2 (General)              |
| PAT           | -                   | -                | -                         |
| Pre-Prod      | -                   | Silver           | -                         |
| Production    | Critical            | Gold             | C3 (Confidential)         |

**Overall Application Classification:**  
- The application is classified as critical for business operations and processes confidential data in production, with general data in non-production environments.  
- It is subject to GDPR and SOX compliance, and handles official-sensitive information such as customer IP addresses and network topology diagrams.  
- Data residency is enforced within the EU, and both internal and external users are supported  [see tables_snapshot_50000.jsonl] (blob)  .

**Citations:**  
- Business Criticality: "The application and its database are classified as critical for business operations." 
- Service Category: "Production: Gold; Pre-Production: Silver; Testing: Silver; Development: Bronze." [see tables_snapshot_50000.jsonl] (blob)
- Information Classification: "Production: C3 (Confidential); Test: C2 (General); Development: C2 (General)." 
- Official-sensitive info: "The application contains official-sensitive information such as customer IP addresses, network topology diagrams, and risk assessments." 

If any value is not found in the indexed records, it is marked as "-".

## 1.1 Key Business Drivers

Here are three key business drivers for the application, based strictly on indexed records:

| Driver                        | Description                                                                                           | Source                        |
|-------------------------------|-------------------------------------------------------------------------------------------------------|-------------------------------|
| Streamlining Business Processes| The application (ERMS) is used for managing company resources (HR, finance, operations), streamlining business processes, and providing reporting and analytics. | AppDetails50000_2             |
| Regulatory Compliance         | The application is subject to GDPR and SOX compliance, with strict data residency requirements within the EU. | AppDetails50000_72            |
| Supporting Internal & External Users | The application supports both internal and external users, including customers and business partners, impacting productivity and revenue if unavailable. | AppDetails50000_75, AppDetails50000_76 |

**Citations:**  
- Streamlining Business Processes: AppDetails50000_2 [see tables_snapshot_50000.jsonl] (blob)
- Regulatory Compliance: AppDetails50000_72 [see tables_snapshot_50000.jsonl] (blob)
- Supporting Internal & External Users: AppDetails50000_75, AppDetails50000_76 [see tables_snapshot_50000.jsonl] (blob)

If more drivers are needed, additional examples include risk mitigation (business continuity/disaster recovery) and integration with legacy and third-party systems.

## 1.2 Key Contacts

Here are the key migration contacts extracted from the indexed content:

| Full Name      | Email                      | Title/Role                | Source                        |
|----------------|---------------------------|---------------------------|-------------------------------|
| Jane Smith     | jane.smith@company.com     | Application Owner         | AppDetails50000_3, AppDetails50000_65 [see tables_snapshot_50000.jsonl] (blob) |
| John Doe       | john.doe@company.com       | System Integrator / IT Application Manager | AppDetails50000_63, AppDetails50000_8 [see tables_snapshot_50000.jsonl] (blob) |
| Michael Chan   | michael.chan@company.com   | Application Technical Contact (SME) | AppDetails50000_64 [see tables_snapshot_50000.jsonl] (blob) |
| David Kim      | david.kim@company.com      | Database Support          | AppDetails50000_65 [see tables_snapshot_50000.jsonl] (blob) |
| Lisa Wong      | lisa.wong@company.com      | Infrastructure Support    | AppDetails50000_65 [see tables_snapshot_50000.jsonl] (blob) |
| Emily Turner   | emily.turner@company.com   | Budget Owner              | AppDetails50000_68 [see tables_snapshot_50000.jsonl] (blob) |
| Samuel Green   | samuel.green@company.com   | Subscription Owner        | AppDetails50000_69 [see tables_snapshot_50000.jsonl] (blob) |
| Robert Lee     | robert.lee@company.com     | Business Owner            | AppDetails50000_4 [see tables_snapshot_50000.jsonl] (blob) |

If you need more details or specific roles, let me know!

## 2. Current Architecture

Here is a detailed overview of the current architecture for the application, based strictly on indexed content:

---

### Technology Stack

- **Frontend:** React.js (web), native mobile apps (iOS/Android)
- **Backend:** Node.js microservices, containerized and orchestrated via Kubernetes/OpenShift
- **API Gateway:** NGINX/Kong (deployed in DMZ), handles routing, throttling, rate limiting, authentication
- **Database:** Microsoft SQL Server 2016 (AlwaysOn cluster for HA), AD integrated authentication, Full recovery model, SSIS and SSRS used
- **Cache:** Redis (clustered for HA)
- **Messaging:** RabbitMQ or Kafka for asynchronous event-driven communication
- **Authentication/Authorization:** Integrated with Active Directory (LDAP/Kerberos), RBAC via AD groups (ERMS_Users, ERMS_Admins)
- **Monitoring:** Prometheus, Grafana, ELK Stack, Splunk, Application Insights
- **CI/CD:** Jenkins, Azure DevOps pipelines, automated builds and deployments
- **Source Control:** Git (feature branching, pull requests)
- **Infrastructure Automation:** Azure Resource Manager (ARM) templates, PowerShell scripts

---

### Deployment Model

- **On-Premises:** Entire solution runs within the customer’s data center
- **Virtualization:** VMware ESXi clusters (3-node for production, dedicated clusters for QA/UAT)
- **High Availability:** SQL AlwaysOn clusters, F5 BIG-IP load balancers (HA pair), clustered Redis, Kubernetes horizontal pod autoscaling
- **Disaster Recovery:** SAN-based storage with replication to secondary DR site, active-passive failover (RTO 30 min, RPO <15 min)
- **Environments:** DEV, QA, UAT, PROD (each with dedicated or shared clusters as appropriate)
- **External/Internal Access:** Application is accessible via both internal and external URLs, supporting customers and business partners

---

### Architectural Layers

- Presentation Layer (UI/Front-End)
- Web Layer (Web Server)
- Application Layer (Business Logic)
- Service Layer (APIs/Services)
- Data Layer (Database/Storage)
- Batch/Job Processing Layer
- Messaging Layer (Queues/Event Hubs/Kafka)

---

### Key Infrastructure Components

| Server Name   | Role/Function         | OS/Platform                | Environment | Notes/Cluster |
|---------------|----------------------|----------------------------|-------------|---------------|
| LB-01, LB-02  | Load Balancer (F5)   | F5 TMOS 15.1               | PROD        | HA Pair       |
| API-GW-01/02  | API Gateway          | RHEL 8.6 (VM)              | PROD        | DMZ, Clustered|
| APP-01/02/03  | App Node (Microservices) | RHEL 8.6/8.5 (K8s Node) | PROD        | K8s Cluster   |
| DB-PRI/SEC    | SQL Server AlwaysOn  | Windows Server 2022/2019   | PROD        | HA Cluster    |
| CACHE-01/02   | Redis Cache          | RHEL 8.6 (VM)              | PROD        | Clustered     |
| LOG-01        | Log Aggregation (ELK) | Ubuntu 22.04               | PROD        | Centralized   |
| MON-01        | Monitoring           | Ubuntu 22.04               | PROD        | Prometheus    |

---

### Architectural Diagram/Description

- Requests flow through perimeter firewall and F5 load balancer to API Gateway in DMZ.
- API Gateway routes to microservices in Kubernetes cluster.
- Microservices interact with SQL Server AlwaysOn cluster and Redis cache.
- Messaging handled via RabbitMQ/Kafka.
- Monitoring and logging via Prometheus, Grafana, ELK, Splunk.
- Automated CI/CD pipeline for deployments.
- Security enforced at multiple layers (network, data, authentication).

---

**Sources:**  
- Application Design Document (On-Premises) [see Application_Design_Document_OnPrem_Detailed.docx] (blob)
- InfrastructureDetails50000 (server inventory and configuration) [see tables_snapshot_50000.jsonl] (blob)
- AppDetails50000 (architecture layers, authentication, integration patterns) [see tables_snapshot_50000.jsonl] (blob)
- CRMS_Server_Communication_Matrix.xlsx (integration dependencies) [see tables_snapshot_50000.jsonl] (blob)

If you need a visual diagram, refer to the "Application Design Document (On-Premises)" and "CRMS_Server_Communication_Matrix.xlsx" for system context and high-level architecture diagrams.

## 3. Technical Debt Summary

Here is a summary of identified technical debt items associated with the current architecture, their potential impact on migration, and recommended mitigation strategies, based strictly on indexed content:

---

### Technical Debt Items

| Technical Debt Item                          | Potential Impact on Migration                          | Recommended Mitigation Strategy                       | Source |
|----------------------------------------------|--------------------------------------------------------|------------------------------------------------------|--------|
| Poor/incomplete technical documentation      | Slows migration discovery, increases risk of errors    | Update and complete documentation before migration    | AppDetails50000_46 [see tables_snapshot_50000.jsonl] (blob) |
| Legacy OS dependencies (in use for binaries) | May require re-platforming or compatibility testing    | Assess legacy components, plan for OS upgrades or containerization | AppDetails50000_49, AppDetails50000_51 [see tables_snapshot_50000.jsonl] (blob) |
| In-house developed application, no vendor support | Limits access to expert help during migration         | Ensure internal SMEs are available, document knowledge transfer | AppDetails50000_10, AppDetails50000_6 [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob) |
| Single-instance database architecture        | Limits scalability and resilience in cloud             | Consider refactoring for cloud-native multi-instance or managed DB | AppDetails50000_53 [see tables_snapshot_50000.jsonl] (blob) |
| Manual credential/key management (CyberArk, not cloud-native) | May complicate integration with cloud key vaults      | Plan integration/migration to Azure Key Vault or similar | AppDetails50000_20 [see tables_snapshot_50000.jsonl] (blob) |
| Linked server integration with legacy billing system | Migration may break integration or require rework     | Map dependencies, test integration in cloud, consider API-based integration | AppDetails50000_14 [see tables_snapshot_50000.jsonl] (blob) |
| Scheduled jobs and file share dependencies   | May require re-architecting for cloud file services    | Inventory jobs, migrate to cloud-native scheduling and storage | AppDetails50000_45, AppDetails50000_16 [see tables_snapshot_50000.jsonl] (blob) |
| Release management constraints (scheduled downtime, manual approvals) | May delay migration cutover and rollback              | Automate approvals, optimize downtime windows, use blue/green deployment | AppDetails50000_59 [see tables_snapshot_50000.jsonl] (blob) |

---

### Additional Notes

- Risks identified in privacy/security assessment include unauthorized access and need for regular patching, which may be exacerbated during migration if not addressed [see tables_snapshot_50000.jsonl] (blob).
- No major audit, legal, or compliance constraints for Azure migration, but technical debt may increase operational risk if not mitigated [see tables_snapshot_50000.jsonl] (blob).

---

### Summary

The main technical debt items are related to documentation gaps, legacy OS dependencies, lack of vendor support, manual processes, and legacy integrations. These can slow down migration, increase risk of downtime, and complicate cloud adoption. Recommended strategies include updating documentation, planning for legacy component upgrades, ensuring SME availability, refactoring for cloud-native patterns, and automating operational processes.

**Citations:**  
- Documentation gaps: AppDetails50000_46 [see tables_snapshot_50000.jsonl] (blob)
- Legacy OS: AppDetails50000_49, AppDetails50000_51 [see tables_snapshot_50000.jsonl] (blob)
- No vendor support: AppDetails50000_10, AppDetails50000_6 [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob)
- Single-instance DB: AppDetails50000_53 [see tables_snapshot_50000.jsonl] (blob)
- Credential management: AppDetails50000_20 [see tables_snapshot_50000.jsonl] (blob)
- Linked server: AppDetails50000_14 [see tables_snapshot_50000.jsonl] (blob)
- Scheduled jobs/file shares: AppDetails50000_45, AppDetails50000_16 [see tables_snapshot_50000.jsonl] (blob)
- Release management: AppDetails50000_59 [see tables_snapshot_50000.jsonl] (blob)

## 4. Overall Application Complexity and Readiness score

**Overall Application Complexity:**  
**Complexity: High**

**Readiness Score for Migration:**  
**Score: 3/5 (Good)**

---

### Explanation & Key Factors

#### Complexity: High

- **Architecture:** The application is a 3-tier, in-house developed Line of Business (LOB) system with separate Web, Application, and Database tiers. It includes multiple architectural layers (presentation, web, business logic, service/API, data, batch jobs) [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob).
- **Dependencies:** There are significant macro dependencies, including integration with a legacy mainframe for billing, third-party payment gateway (PayPal API), and external identity provider (Azure AD). There are also linked server integrations and file share dependencies for report generation and data exchange  [see CRMS_Server_Config.pdf] (blob) .
- **Data Volume:** The application stores between 100,000 and 1 million records of personal information, with a production database size of 500 GB and expected annual growth of 50 GB   .
- **User Base:** Supports ~1,000 users, with 100-150 peak concurrent users, and is both internal and external-facing  .
- **Security & Compliance:** Handles confidential and official-sensitive information, subject to GDPR/SOX, with strict EU data residency requirements   .
- **Technical Debt:** Documentation gaps, legacy OS dependencies, manual credential management, and release management constraints increase migration complexity    .

#### Readiness Score: 3/5 (Good)

- **Strengths:**  
  - High availability (SQL Always On), disaster recovery plan in place, CI/CD pipelines, infrastructure automation, and documented operational support model     .
  - No major audit, legal, or compliance constraints for Azure migration [see tables_snapshot_50000.jsonl] (blob).
  - Acceptance test cases and technical documentation are available  .

- **Weaknesses/Barriers:**  
  - Poor/incomplete documentation in some areas .
  - Legacy OS and manual processes may require rework or modernization  .
  - Single-instance database architecture and file share dependencies may complicate cloud migration  .
  - Release management constraints (scheduled downtime, manual approvals) .

---

**Summary:**  
The application is complex due to its multi-tier architecture, significant integrations, large data volumes, and compliance requirements. While there are strengths in automation, HA/DR, and support models, technical debt and legacy dependencies reduce migration readiness. The readiness score is "Good" (3/5), indicating that migration is feasible but will require careful planning and mitigation of identified risks.

**Citations:**  
- Architecture, dependencies, data volume, and technical debt: Application_Design_Document.docx, Integration_Inventory.xlsx, Security_Controls.docx, File_Share_Inventory.xlsx, Assessment based on previous answers, Legacy_OS_Installation_Guide.docx, DevOps_Practices.docx, Cloud_Migration_Assessment.xlsx [see tables_snapshot_50000.jsonl] (blob)       [see tables_snapshot_50000.jsonl] (blob).

## 5. Migration Strategy

## 5.1 Migration Pattern and Complexity

Here is a modernization-focused migration proposal for each discovered server and database type, based on indexed architecture and the provided migration matrix:

| Source Server Name | Source OS/Database Type & Version      | Target Version/Type                | Migration Type      | Reasoning |
|--------------------|----------------------------------------|------------------------------------|---------------------|-----------|
| API-GW-01          | RHEL 8.6 (VM)                         | RHEL 8.6 (Azure VM)                | Re-Host             | Already modern; latest supported RHEL version, direct re-host to Azure VM for minimal disruption.  [see CRMS_Server_Config.pdf] (blob) |
| API-GW-02          | RHEL 8.6 (VM)                         | RHEL 8.6 (Azure VM)                | Re-Host             | Same as above; clustered, so re-host preserves HA setup.  [see CRMS_Server_Config.pdf] (blob) |
| APP-01             | RHEL 8.6 (K8s Node)                   | RHEL 8.6 (Azure VM/AKS Node)       | Re-Host/Modernize   | Already containerized; migrate to Azure Kubernetes Service (AKS) for highest modernization.  [see CRMS_Server_Config.pdf] (blob) |
| APP-02             | RHEL 8.6 (K8s Node)                   | RHEL 8.6 (Azure VM/AKS Node)       | Re-Host/Modernize   | Same as above; move to AKS for managed orchestration.  [see CRMS_Server_Config.pdf] (blob) |
| APP-03             | RHEL 8.5 (K8s Node)                   | RHEL 8.6 (Azure VM/AKS Node)       | Clean Deployment/Modernize | Upgrade to RHEL 8.6 for consistency, migrate to AKS for modernization.  [see CRMS_Server_Config.pdf] (blob) |
| DB-PRI             | Windows Server 2022 + SQL Server 2016 | Azure SQL Managed Instance (MI)     | Modernize           | Highest modernization: move to Azure SQL MI for managed HA, scalability, and reduced ops overhead.  [see tables_snapshot_50000.jsonl] (blob) |
| DB-SEC             | Windows Server 2019/2022 + SQL Server 2016 | Azure SQL Managed Instance (MI) | Modernize           | Same as above; migrate secondary node to Azure SQL MI for managed DR/HA.  [see tables_snapshot_50000.jsonl] (blob) |
| CACHE-01           | RHEL 8.6 (VM, Redis)                  | Azure Cache for Redis               | Modernize           | Move to managed Azure Cache for Redis for scalability, HA, and patching.  [see CRMS_Server_Config.pdf] (blob) |
| CACHE-02           | RHEL 8.6 (VM, Redis)                  | Azure Cache for Redis               | Modernize           | Same as above; migrate failover node to managed service.  [see CRMS_Server_Config.pdf] (blob) |
| MQ-01              | RHEL 8.6 (VM, RabbitMQ)               | Azure Service Bus or Azure Event Grid | Modernize        | Move to managed messaging for reliability, scalability, and integration.  [see CRMS_Server_Config.pdf] (blob) |
| MQ-02              | RHEL 8.6 (VM, RabbitMQ)               | Azure Service Bus or Azure Event Grid | Modernize        | Same as above; migrate clustered node to managed service.  [see CRMS_Server_Config.pdf] (blob) |
| MON-01             | Ubuntu 22.04 (VM, Prometheus/Grafana) | Azure Monitor, Azure Log Analytics   | Modernize           | Move to Azure-native monitoring for unified visibility and automation.  [see CRMS_Server_Config.pdf] (blob) |
| LOG-01             | Ubuntu 22.04 (VM, ELK Stack)          | Azure Log Analytics, Azure Monitor   | Modernize           | Move to managed logging for scalability, security, and integration.  [see CRMS_Server_Config.pdf] (blob) |

**Notes:**
- For all RHEL 8.x nodes, re-host is supported, but modernization via AKS (for app nodes) and managed Azure services (for Redis, RabbitMQ, monitoring, logging) is preferred.
- For SQL Server 2016, Azure SQL Managed Instance is the most modern target, supporting HA, DR, and managed operations.
- For Ubuntu 22.04, clean deployment to Azure VM is possible, but modernization to Azure-native monitoring/logging is recommended.

**Citations:**  
- Server inventory and OS/database details: CRMS_Server_Configuration_and_Validation_Details.xlsx, InfrastructureDetails50000, Application_Design_Document.docx [see CRMS_Server_Config.pdf] (blob) [see tables_snapshot_50000.jsonl] (blob)

If you need environment-specific breakdowns (Dev, Test, Prod), let me know!

## 5.2 Database information

Here is the compiled database information per environment, based strictly on indexed data:

| Environment   | Database Name | Type         | Version                | Size (GB) | Migration Pattern         | Complexity | Source                                   |
|---------------|--------------|--------------|------------------------|-----------|--------------------------|------------|-------------------------------------------|
| Production    | SQL          | MSSQL        | SQL Server 2016 Std    | 500       | Azure SQL Managed Instance (MI) | High       | InfrastructureDetails50000, MSSQLDB50000 [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob) |
| Production    | OracleDB     | Oracle       | -                      | 500       | Oracle 19c on ADB/BaseDB/Exacs/IaaS | High       | OracleDB50000 [see tables_snapshot_50000.jsonl] (blob)              |
| Test          | SQL          | MSSQL        | SQL Server 2016 Std    | -         | Azure SQL Managed Instance (MI) | Medium     | MSSQLDB50000 [see tables_snapshot_50000.jsonl] (blob)               |
| Development   | SQL          | MSSQL        | SQL Server 2016 Std    | -         | Azure SQL Managed Instance (MI) | Medium     | MSSQLDB50000 [see tables_snapshot_50000.jsonl] (blob)               |

**Notes:**
- All environments use SQL Server 2016 Standard Edition, clustered via AlwaysOn Availability Groups for HA.
- Production database size is 500 GB, with expected annual growth of 50 GB.
- OracleDB is referenced for production, but version is not specified; migration pattern follows modernization guidance to Oracle 19c on cloud platforms.
- Lower environments (Test, Development) are regularly refreshed from sanitized production backups [see tables_snapshot_50000.jsonl] (blob).
- Complexity is high for production due to clustering, integrations, and data volume; medium for lower environments.

**Citations:**  
- InfrastructureDetails50000, MSSQLDB50000, OracleDB50000 [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob)

If you need more granular breakdowns or additional environments, let me know!

## 6. Indicative Azure Cost

Due to limitations in the Azure Pricing MCP server product search, I am unable to retrieve exact SKUs and prices for some components. However, based on the indexed migration patterns and database information, here is a detailed breakdown of required Azure components and a cost estimation approach for each environment. Reasonable assumptions are made where data is missing.

---

### Production Environment

| Component                | SKU/Type                        | Monthly Cost (USD) | Assumptions/Notes |
|--------------------------|----------------------------------|--------------------|-------------------|
| Azure SQL Managed Instance | General Purpose, 8 vCores, 500 GB | ~$1,200            | Assumed based on typical MI pricing for 8 vCores, 500 GB storage |
| Azure App Service (Web/API) | P1v3 (Production)                | ~$220              | Assumed 2 instances for HA |
| Azure Kubernetes Service  | Standard Node Pool (3 nodes, D2 v5) | ~$300              | Assumed D2 v5 nodes, 3 for HA |
| Azure Cache for Redis     | Standard C3                      | ~$200              | Assumed 2 nodes for HA |
| Azure Service Bus         | Standard Tier                    | ~$50               | For messaging integration |
| Azure Monitor/Log Analytics | Standard Tier, 500 GB/month      | ~$100              | For monitoring/logging |
| Azure Storage Account     | 1 TB (General Purpose v2)        | ~$25               | For backups, fileshares |
| Total Monthly Cost        |                                  | **~$2,095**        |                     |

---

### Test Environment

| Component                | SKU/Type                        | Monthly Cost (USD) | Assumptions/Notes |
|--------------------------|----------------------------------|--------------------|-------------------|
| Azure SQL Managed Instance | General Purpose, 4 vCores, 100 GB | ~$600              | Smaller instance for test |
| Azure App Service (Web/API) | P1v3 (Test)                      | ~$110              | Single instance |
| Azure Kubernetes Service  | Standard Node Pool (2 nodes, D2 v5) | ~$200              | Smaller pool |
| Azure Cache for Redis     | Standard C1                      | ~$80               | Single node |
| Azure Monitor/Log Analytics | Standard Tier, 100 GB/month      | ~$30               | Reduced volume |
| Azure Storage Account     | 200 GB (General Purpose v2)      | ~$5                | For test backups |
| Total Monthly Cost        |                                  | **~$1,025**        |                     |

---

### Development Environment

| Component                | SKU/Type                        | Monthly Cost (USD) | Assumptions/Notes |
|--------------------------|----------------------------------|--------------------|-------------------|
| Azure SQL Managed Instance | General Purpose, 2 vCores, 50 GB | ~$300              | Minimal instance for dev |
| Azure App Service (Web/API) | P1v3 (Dev)                       | ~$55               | Single instance |
| Azure Kubernetes Service  | Standard Node Pool (1 node, D2 v5) | ~$100              | Minimal pool |
| Azure Cache for Redis     | Basic C0                         | ~$30               | Minimal cache |
| Azure Monitor/Log Analytics | Standard Tier, 50 GB/month       | ~$15               | Minimal logging |
| Azure Storage Account     | 100 GB (General Purpose v2)      | ~$2                | For dev backups |
| Total Monthly Cost        |                                  | **~$502**          |                     |

---

**Assumptions:**
- Pricing is estimated based on typical Azure retail rates for Western Europe.
- Database sizing and vCore counts are based on indexed environment data.
- App Service and AKS node counts are based on HA and environment needs.
- Redis, Service Bus, Monitor, and Storage are sized per environment usage.
- Actual costs may vary; for precise pricing, use Azure Calculator with exact SKUs and configurations.

If you need a breakdown for additional environments (QA, UAT, Pre-Prod), or want to refine assumptions, let me know!

## 7. Macro Dependencies

Here is a dependency map for the Enterprise Resource Management System (ERMS) application, highlighting its dependencies with other applications, databases, interfaces, and integrations:

---

### Application Dependency Map

#### 1. Application Layers & Internal Dependencies
- **Web Layer:** ASP.NET MVC, JavaScript frontend [see tables_snapshot_50000.jsonl] (blob)
- **API Gateway:** RHEL 8.6 (API-GW-01/02), handles secure API traffic, routing, throttling, authentication [see tables_snapshot_50000.jsonl] (blob)
- **Application Layer:** Microservices on Kubernetes (APP-01/02/03), business logic, API processing [see tables_snapshot_50000.jsonl] (blob)
- **Data Layer:** SQL Server 2016 AlwaysOn cluster (DB-PRI, DB-SEC), AD integrated authentication [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob)
- **Batch/Job Processing:** Nightly data sync and report generation, depends on DB and file share [see tables_snapshot_50000.jsonl] (blob)

#### 2. Database Dependencies
- **Primary Database:** SQL Server 2016 Std, 2-node AlwaysOn cluster (DB-PRI, DB-SEC) [see tables_snapshot_50000.jsonl] (blob)
- **Linked Server:** Integration with legacy billing system [see tables_snapshot_50000.jsonl] (blob)
- **SSIS/SSRS:** Used for ETL and reporting [see tables_snapshot_50000.jsonl] (blob)

#### 3. File Share Dependencies
- **Production:** \\fileserver01\prodshare (SMB, AD Integrated, 500 GB allocated) [see tables_snapshot_50000.jsonl] (blob)
- **Non-Prod:** \\fileserver01\nonprodshare (SMB, AD Integrated, 200 GB allocated) [see tables_snapshot_50000.jsonl] (blob)

#### 4. Messaging & Cache
- **Redis Cache:** Clustered for HA (CACHE-01/02) [see tables_snapshot_50000.jsonl] (blob)
- **RabbitMQ:** For async event-driven communication (MQ-01/02) [see tables_snapshot_50000.jsonl] (blob)

#### 5. Monitoring & Logging
- **Prometheus/Grafana:** Node and DB metrics scraping (MON-01) [see tables_snapshot_50000.jsonl] (blob)
- **ELK Stack:** Centralized log aggregation (LOG-01) [see tables_snapshot_50000.jsonl] (blob)

#### 6. External Integrations
- **Payment Gateway:** PayPal API (outbound HTTPS calls) [see tables_snapshot_50000.jsonl] (blob)
- **Identity Provider:** Azure AD for authentication [see tables_snapshot_50000.jsonl] (blob)
- **Internet Connectivity:** Ingress via HTTPS (443) for user access, egress for API calls to https://api.thirdpartyservice.com [see tables_snapshot_50000.jsonl] (blob)

#### 7. Other Applications
- **POS System:** Secure traffic to API Gateway (443/TCP) [see tables_snapshot_50000.jsonl] (blob)
- **E-Commerce Frontend:** API calls to API Gateway (443/TCP) [see tables_snapshot_50000.jsonl] (blob)

#### 8. Security & Credentials
- **CyberArk:** Manages encryption keys, certificates, and sensitive credentials [see tables_snapshot_50000.jsonl] (blob)

#### 9. Load Balancers
- **Production:** External (F5), Internal (Windows NLB) [see tables_snapshot_50000.jsonl] (blob)
- **Pre-Production:** Internal (Windows NLB) [see tables_snapshot_50000.jsonl] (blob)

#### 10. User Groups & Authorization
- **AD Groups:** ERMS_Users, ERMS_Admins [see tables_snapshot_50000.jsonl] (blob)

---

### Visual Map (Textual Representation)

```
[External Users/Partners]
        |
   [Imperva WAF] -- [F5 Load Balancer]
        |
   [API-GW-01/02] <--- [POS System], [E-Commerce Frontend]
        |
   [APP-01/02/03] (K8s Microservices)
        |         |         |         |
   [DB-PRI/DB-SEC] [CACHE-01/02] [MQ-01/02] [LOG-01] [MON-01]
        |         |         |         |         |
[File Shares] [Legacy Billing System] [PayPal API] [Azure AD]
```

---

### Key Citations
- Application architecture, layers, and integrations: Application_Design_Document.docx, Integration_Inventory.xlsx, CRMS_Server_Communication_Matrix.xlsx, InfrastructureDetails50000, MSSQLDB50000 [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob)

---

**Summary:**  
ERMS is a 3-tier, microservices-based application with dependencies on SQL Server, Redis, RabbitMQ, file shares, external payment and identity providers, and legacy systems. It integrates with POS and E-Commerce systems, uses CyberArk for credential management, and is monitored/logged via Prometheus/Grafana and ELK. All dependencies are mapped for migration and operational planning.

## 8. Security Considerations

- Data must reside and be accessed only within the EU to comply with GDPR and local EU data protection laws; no access is permitted from outside the EU (data residency enforced)  .
- The application is subject to GDPR and SOX compliance requirements, including automated data subject requests and auditability .
- Local EU member state data protection laws apply in addition to GDPR .
- Data retention policy specifies that data is retained for 7 years in compliance with regulatory requirements [see tables_snapshot_50000.jsonl] (blob).
- Data is classified as C3 (Confidential) in Production, C2 (General) in Test and Development environments .
- The application processes personal data categories including account data, authentication data, basic personal data (name, email), financial data, employment details, security data, and user activity logs .
- Encryption keys, certificates, and sensitive credentials are managed using CyberArk, with regular rotation of credentials .
- Authentication is integrated with Active Directory via LDAP/Kerberos; authorization is enforced using role-based access control (RBAC) at the API level [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- Network security is implemented using segregated VLANs for DMZ, app, and DB tiers, with ACLs enforced [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- Data security includes SQL Server TDE, disk-level encryption, and TLS 1.2 for all traffic [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- SIEM integration streams logs to Splunk for threat detection and monitoring [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- Risks identified in privacy/security assessment include potential unauthorized access to personal data and the need for regular security patching  .
- The application contains official-sensitive information such as customer IP addresses, network topology diagrams, and risk assessments, handled according to internal security policies .
- Both internal and external users (including business partners and system integrators) require access, increasing the need for robust access controls and monitoring .
- If migration is not successful, the business faces reputational, regulatory, and financial risks due to downtime or data loss, and potential non-compliance with GDPR/SOX .

## 9. Resiliency Consideration

**Resiliency Configurations for ERMS in Azure**

**High Availability (HA):**
- Use Azure SQL Managed Instance with built-in HA (multi-zone deployment, automatic failover) for the database tier .
- Deploy application microservices on Azure Kubernetes Service (AKS) with multiple node pools and horizontal pod autoscaling [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- Use Azure Load Balancer or Azure Application Gateway for web/API tier, with external and internal load balancing for redundancy .
- Redis Cache and messaging (Service Bus/Event Grid) should be deployed in clustered/HA configurations [see tables_snapshot_50000.jsonl] (blob).

**Disaster Recovery (DR):**
- Geo-redundant backup and replication for databases (Azure SQL MI geo-replication, or paired region failover) .
- Store application and configuration backups in Azure Recovery Services Vault with cross-region replication.
- Annual DR testing and documented failover procedures; target RTO: 2 hours, RPO: 30 minutes .
- Use Azure Site Recovery for VM-based components if any remain post-modernization.

**Backup Strategies:**
- Nightly full database backups, transaction log backups every 15 minutes, with 30-day retention .
- Application and file share backups to Azure Storage (General Purpose v2), with versioning and point-in-time restore.
- Regular backup of configuration files and secrets (preferably stored in Azure Key Vault).
- Non-production environments refreshed from sanitized production backups .

**Additional Notes:**
- Data encryption at rest (TDE, disk encryption) and in transit (TLS 1.2/HTTPS) is mandatory .
- Use Azure Monitor and Log Analytics for real-time monitoring, alerting, and centralized log aggregation.
- All resiliency configurations should be tested and validated as part of the migration acceptance criteria.

**Citations:**  
- Application_Design_Document.docx [see Application_Design_Document_OnPrem_Detailed.docx] (blob) 
- DR_Plan.docx 
- Security_Controls.docx 
- InfrastructureDetails50000 [see tables_snapshot_50000.jsonl] (blob)
- OracleDB50000 

If you need environment-specific details or a visual diagram, let me know!

## 10. Network Access Requirements

Here is a detailed list of network access requirements for the ERMS application deployment in Azure, based strictly on indexed records:

---

### Allowed Protocols
- **HTTPS (TCP 443):** For user access (internal/external), API calls, and integration with third-party services [see tables_snapshot_50000.jsonl] (blob).
- **LDAP/Kerberos (TCP 389/636/88):** For Active Directory authentication and authorization [see tables_snapshot_50000.jsonl] (blob).
- **SMB (TCP 445):** For file share access (internal only) [see tables_snapshot_50000.jsonl] (blob).
- **SQL (TCP 1433):** For database connectivity (internal only, via listener) [see tables_snapshot_50000.jsonl] (blob).
- **SMTP (TCP 25/587):** For outbound email notifications via Exchange server [see tables_snapshot_50000.jsonl] (blob).
- **Custom Ports (e.g., TCP 9100):** For monitoring (Prometheus Node Exporter scraping) [see tables_snapshot_50000.jsonl] (blob).

---

### Source/Destination Networks
- **External Users/Partners:**  
  - Source: Internet  
  - Destination: Azure Frontend (App Gateway/Load Balancer), DMZ subnet  
  - Protocol: HTTPS (443) [see tables_snapshot_50000.jsonl] (blob)

- **Internal Users (Employees):**  
  - Source: Corporate LAN  
  - Destination: Azure Frontend, Internal subnet  
  - Protocol: HTTPS (443), SMB (445), SQL (1433) [see tables_snapshot_50000.jsonl] (blob)

- **Application Servers:**  
  - Source: App subnet  
  - Destination: DB subnet, Cache subnet, Messaging subnet  
  - Protocol: SQL (1433), Redis (6379), RabbitMQ/Service Bus (5671/443) [see tables_snapshot_50000.jsonl] (blob)

- **Monitoring/Logging:**  
  - Source: Monitoring subnet  
  - Destination: App/DB/Cache nodes  
  - Protocol: TCP 9100 (Prometheus), TCP 9200 (ELK) [see tables_snapshot_50000.jsonl] (blob)

- **Third-party Integrations:**  
  - Source: App subnet  
  - Destination: Internet (PayPal API, Azure AD, other APIs)  
  - Protocol: HTTPS (443) [see tables_snapshot_50000.jsonl] (blob)

---

### Access Restrictions
- **Internal Access:**  
  - Restricted to corporate IP ranges and Azure VNets/subnets.  
  - Only authorized users/groups (ERMS_Users, ERMS_Admins) via AD [see tables_snapshot_50000.jsonl] (blob) [see tables_snapshot_50000.jsonl] (blob).

- **External Access:**  
  - Allowed only via HTTPS (443) through Azure Application Gateway and WAF.  
  - External DNS: https://erms.company.com  
  - DMZ isolation and firewall rules enforced [see tables_snapshot_50000.jsonl] (blob).

- **Developer/Admin Access:**  
  - Restricted to management subnets and jump hosts.  
  - Admin access via bastion host or VPN, with MFA and RBAC enforced [see tables_snapshot_50000.jsonl] (blob).

- **Database Access:**  
  - Only via AD-integrated service accounts.  
  - No direct external DB access; all access via application layer [see tables_snapshot_50000.jsonl] (blob).

- **File Shares:**  
  - Internal only, AD-integrated authentication.  
  - No external access to file shares [see tables_snapshot_50000.jsonl] (blob).

---

### Special Security Considerations
- **Data Residency:**  
  - All data must reside and be accessed only within the EU; no access permitted from outside the EU (GDPR compliance) [see tables_snapshot_50000.jsonl] (blob).
- **Encryption:**  
  - Data encrypted at rest (disk/database encryption) and in transit (TLS 1.2/HTTPS) [see tables_snapshot_50000.jsonl] (blob).
- **WAF Protection:**  
  - All external traffic passes through Imperva WAF before reaching the application [see tables_snapshot_50000.jsonl] (blob).
- **Segregated Network Zones:**  
  - DMZ, App, DB, Monitoring, and Management subnets are isolated with ACLs and NSGs [see tables_snapshot_50000.jsonl] (blob).
- **Credential Management:**  
  - Encryption keys, certificates, and sensitive credentials managed via CyberArk, with regular rotation [see tables_snapshot_50000.jsonl] (blob).
- **Compliance:**  
  - Subject to GDPR, SOX, and local EU data protection laws [see tables_snapshot_50000.jsonl] (blob).
- **Audit & Monitoring:**  
  - SIEM integration (Splunk), centralized logging, and regular security patching required [see tables_snapshot_50000.jsonl] (blob).

---

**Citations:**  
- Application_Design_Document.docx  
- Security_Controls.docx  
- Network_Design_Document.docx  
- CRMS_Server_Communication_Matrix.xlsx  
- App_Infra_RBAC.xlsx

If you need a visual network diagram or more granular port mapping, let me know!

## 11. Identity providers

Here is a table of identity providers used by the application, based strictly on indexed Azure AI Search content:

| Identity Provider | Type                | Usage                        |
|-------------------|---------------------|------------------------------|
| Active Directory  | LDAP/Kerberos, SAML | User Authentication, API Access, Database Authentication (AD-integrated) [see Application_Design_Document_OnPrem_Detailed.docx] (blob) [see tables_snapshot_50000.jsonl] (blob) |
| Azure AD          | OAuth, OpenID       | External Identity Provider for API Access and SSO integration [see tables_snapshot_50000.jsonl] (blob) |

**Details:**
- The application authenticates users via Active Directory (AD) integration using Windows Authentication, leveraging LDAP/Kerberos protocols for internal users and SAML for federated access [see Application_Design_Document_OnPrem_Detailed.docx] (blob) [see tables_snapshot_50000.jsonl] (blob).
- Azure AD is used as an external identity provider for API access and Single Sign-On (SSO), supporting OAuth and OpenID Connect protocols for integrations with third-party platforms and external partners [see tables_snapshot_50000.jsonl] (blob).
- Database authentication is performed using AD-integrated authentication [see tables_snapshot_50000.jsonl] (blob).

If you need more granular details (e.g., environment-wise usage), let me know!

## 12. Automation

Yes, the application uses CI/CD for both application and infrastructure:

- **Continuous Integration (CI):** Automated builds are triggered on code commits, ensuring that changes are continuously integrated into release environments [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **Continuous Delivery (CD):** Automated deployment pipelines are used for both non-production and production environments, supporting phased rollout and incremental updates [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **Infrastructure Automation:** Infrastructure provisioning is automated using Azure Resource Manager (ARM) templates and PowerShell scripts [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **Deployment Strategy:** The overall CI/CD deployment strategy is a phased rollout using Azure DevOps pipelines, with support for both full deployments and delta (incremental) updates [see Application_Design_Document_OnPrem_Detailed.docx] (blob).

**Citations:**  
- CI: "Yes, the team uses continuous integration with automated builds triggered on code commits." [see Application_Design_Document_OnPrem_Detailed.docx] (blob)
- CD: "Yes, continuous delivery pipelines are used to automate deployments to non-production and production environments." [see Application_Design_Document_OnPrem_Detailed.docx] (blob)
- Infrastructure Automation: "Yes, infrastructure automation is leveraged using Azure Resource Manager (ARM) templates and PowerShell scripts." [see Application_Design_Document_OnPrem_Detailed.docx] (blob)
- Deployment Strategy: "The CI/CD deployment strategy is a phased rollout using Azure DevOps pipelines, with support for both full deployments and delta (incremental) updates." [see Application_Design_Document_OnPrem_Detailed.docx] (blob)

## 13. Observability

Here are the monitoring, security vulnerability, and observability tools used by the application and infrastructure, based strictly on indexed records:

- **Prometheus & Grafana:** Used for metrics collection, visualization, and alerting on application and infrastructure health. Metrics include CPU, memory, latency, and uptime. Integrated with the NOC for 24x7 monitoring. Synthetic probes are used for uptime checks [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **ELK Stack (Elasticsearch, Logstash, Kibana):** Centralized log aggregation for all tiers. Enables search, analysis, and visualization of application and infrastructure logs [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **Splunk (SIEM):** Security Information and Event Management platform. Application and infrastructure logs are streamed to Splunk for threat detection, monitoring, and compliance reporting [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **RabbitMQ Monitoring Dashboard:** Used for monitoring messaging queues and health of RabbitMQ nodes [see tables_snapshot_50000.jsonl] (blob).
- **Security Vulnerability Assessment:** Regular penetration testing and security patching are performed. Risks and vulnerabilities are tracked in the privacy and security assessment (Assessment ID: VSR-2023-015) [see tables_snapshot_50000.jsonl] (blob).
- **Backup Monitoring:** Veeam is used for backup monitoring, with nightly incremental and weekly full backups, including offsite storage [see Application_Design_Document_OnPrem_Detailed.docx] (blob).
- **Database Monitoring:** Prometheus scrapes metrics from SQL Server AlwaysOn cluster nodes for health and performance [see tables_snapshot_50000.jsonl] (blob).

**Summary:**  
The application and infrastructure use Prometheus, Grafana, ELK Stack, Splunk, RabbitMQ dashboard, and Veeam for monitoring, observability, and security vulnerability management. Regular penetration testing and SIEM integration are in place for security assurance.

**Citations:**  
- Application Design Document (On-Premises) [see Application_Design_Document_OnPrem_Detailed.docx] (blob)
- IntegrationDependency50000 [see tables_snapshot_50000.jsonl] (blob)
- AppDetails50000 (privacy/security assessment) [see tables_snapshot_50000.jsonl] (blob)

## 14. Operational Concerns

## 15. Migration acceptance Tests

## 16. Customer Impact

## 17. Supporting Documentation

Here is a list of supporting documents available for the application, as discovered in the Azure AI search index:

| Document Type                | Document Name / Description                        | Source/Citation |
|------------------------------|---------------------------------------------------|-----------------|
| Architecture Diagram         | Application_Design_Document.docx (includes high-level and detailed architecture diagrams, system context, logical and physical views) |  [see Application_Design_Document_OnPrem_Detailed.docx] (blob) |
| DR Diagram                   | Application_Design_Document.docx (includes DR site, failover, RTO/RPO details) |  [see Application_Design_Document_OnPrem_Detailed.docx] (blob) |
| Network Diagram              | Network_Design_Document.docx (includes VLANs, DMZ, load balancer, internal/external URLs) |  [see tables_snapshot_50000.jsonl] (blob) |
| Security Document            | Security_Controls.docx (covers authentication, authorization, encryption, SIEM, compliance, risks, data classification) |  [see tables_snapshot_50000.jsonl] (blob) |
| Compliance Document          | Security_Controls.docx (GDPR, SOX, EU data residency, local laws) |  [see tables_snapshot_50000.jsonl] (blob) |
| Run Book / SOP               | Operational support model and RACI matrix (roles/responsibilities) |  [see tables_snapshot_50000.jsonl] (blob) |
| Migration Acceptance Tests   | Migration_Test_Cases.xlsx (acceptance test cases for migration) |  [see tables_snapshot_50000.jsonl] (blob) |
| Server Inventory             | Server Inventory and Network Details for CRMS (lists all servers, roles, OS, IPs, ports) |  [see CRMS_Server_Inventory_and_IP_Details.docx] (blob) |
| Communication Matrix         | CRMS_Server_Communication_Matrix.xlsx (integration dependencies, port/protocol mapping) |  [see tables_snapshot_50000.jsonl] (blob) |
| Legacy OS Installation Guide | Legacy_OS_Installation_Guide.docx (installation/configuration for legacy OS) |  [see tables_snapshot_50000.jsonl] (blob) |
| Technical Documentation      | Solution Architecture Document, Security Architecture Design |  [see tables_snapshot_50000.jsonl] (blob) |

**Notes:**
- The Application_Design_Document.docx is the primary source for architecture, DR, and security diagrams.
- Security_Controls.docx covers both security and compliance requirements.
- Network_Design_Document.docx provides network topology and access details.
- Migration_Test_Cases.xlsx and Legacy_OS_Installation_Guide.docx support migration and operational readiness.
- The operational support model (RACI) and SOPs are referenced in AppDetails50000.

If you need the actual diagrams or document excerpts, please specify which document or section you need!
