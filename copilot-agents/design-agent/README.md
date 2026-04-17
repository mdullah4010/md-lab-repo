# Design Agent (Copilot Studio)

> **Purpose:** Acts as a **solutions architect** for Azure-native designs, applying **Azure Well-Architected Framework** and **Microsoft Security Control Framework** principles. Generates **five architectural views**, cost estimates, and sends the final JSON via email.

---

## 📌 Table of Contents
1. Introduction
2. Where-you-can-use-this-agent
3. Prerequisites
4. Import-the-agent-into-copilot-studio
5. Configure-required-connections
   - Outlook-connection
   - Sharepoint-knowledge-connection
6. Publish-to-microsoft-365-channels
7. How-to-start-with-a-prompt
8. Expected-output
9. References

---

## ✅ Introduction
This Copilot Studio agent:
- Specialises in **Azure-native solution design**.
- Produces:
  - **Five architectural views** (Logical, High-Level Technical, Infrastructure, Networking & Security, Monitoring & Logging) with **Mermaid diagrams**.
  - **Cost estimation** using Azure Pricing Calculator heuristics.
- Sends the **final JSON output via email** .

---

## ✅ Where You Can Use This Agent
- **Microsoft Teams** (chat, meetings, channels).
- **Microsoft 365 Copilot** (Word, Outlook, Excel side panels).
- **Web chat** or **custom channels** if enabled.

---

## ✅ Prerequisites
- **Copilot Studio access** with permission to import/export agents.
- **Office 365 Outlook connector** (licensed mailbox).
- **SharePoint connector** with access to the knowledge folder.
- Admin approval for publishing to **Teams + Microsoft 365 Copilot**.

---

## ✅ Import the Agent into Copilot Studio
1. Go to **Copilot Studio** → **Agents**.
2. Click **Import** → select the provided `.zip` (exported agent solution).
3. Review details and confirm.
4. After import, open the agent to verify:
   - **Topics** and **Actions** are present.
   - **Connections** show as **Not configured** (we’ll fix next).

---

## ✅ Configure Required Connections

### 🔹 Outlook Connection
- Purpose: Enables the agent to **send the final JSON via email**.
- Steps:
  1. In the agent, go to **Settings → Connections**.
  2. Locate **Office 365 Outlook**.
  3. Sign in with a mailbox-enabled account (service account recommended).
  4. Test by sending a sample email.

---

### 🔹 SharePoint Knowledge Connection
- Purpose: Allows the agent to **use a SharePoint folder as a knowledge base** for intake and inventory data.
- Steps:
  1. In **Copilot Studio**, open the agent → **Knowledge**.
  2. Click **Add knowledge source** → choose **SharePoint**.
  3. Provide the **site URL** and select the **folder/library**.
  4. Save and **publish**.

---

## ✅ Publish to Microsoft 365 Channels
1. In the agent, click **Publish**.
2. Go to **Channels** → enable **Teams + Microsoft 365**.
3. Choose availability (just you, specific groups, or org-wide).
4. Submit for **admin approval**.
5. Once approved, the agent appears in **Teams** and **Copilot Chat**.

---

## ✅ How to Start with a Prompt
Example prompts:
- *“Design a secure Azure architecture for a web app with 50k users. I’ll provide the input JSON next.”*
- *“Generate all five views and email the JSON to architecture@contoso.com.”*

### Input JSON Template:
```json
{
  "application": {
    "name": "",
    "purpose": "",
    "users": "",
    "tech_stack": "",
    "data_profile": "",
    "authentication": "",
    "access_method": "",
    "business_criticality": "",
    "compliance": ""
  },
  "migration": {
    "strategy": "",
    "target_services": {
      "app_hosting": "",
      "database": "",
      "batch_processing": "",
      "access": "",
      "identity": "",
      "monitoring": "",
      "ci_cd": "",
      "security": "",
      "availability": "",
      "disaster_recovery": "",
      "data_retention": "",
      "environment_separation": ""
    }
  }
}
```

---

## ✅ Expected Output
- **Five architectural views** with Mermaid diagrams.
- **Cost estimate** with `monthly_total_eur` and resource breakdown.
- **Email** sent with **JSON only**.

---

## ✅ References
- https://learn.microsoft.com/microsoft-copilot-studio/publication-fundamentals-publish-channels
- https://learn.microsoft.com/microsoft-copilot-studio/knowledge-add-sharepoint
- https://learn.microsoft.com/connectors/office365/

