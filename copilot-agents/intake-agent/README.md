Intake Agent for Azure Migration — README
=========================================

> **Purpose:** This Copilot Studio agent engages application owners in large-scale Azure migration programs, asks up to **20** targeted questions based on **critical data points**, and produces a **structured, ready-to-share intake report**. If an architectural document is provided, the agent **reviews it first** and **avoids asking** questions already answered in the document.

* * *

🧭 Table of Contents
--------------------

1.  Introduction
2.  Where-it-can-be-used
3.  Solution-contents
4.  Prerequisites
5.  importing-the-solution
6.  Configuring-required-connections
    *   Outlook-email-connection-required
    *   Sharepoint-knowledge-base-connection-optional
    *   Recommended-environment-variables
7.  Publishing-to-microsoft-365-channels
    *   Publish-as-a-microsoft-365-copilot-plugin
    *   Publish-to-microsoft-teams
8.  How-to-start-prompts
9.  Conversation-flow--behavior
10.  Data-points-captured--report-structure
11.  Email-json-payload-schema--example
12.  Operational-notes-security--compliance
13.  Troubleshooting
14.  Faq
15.  Versioning--change-log
16.  Ownership--support

* * *

Introduction
------------

**Intake Agent (Copilot Studio)**\ This agent streamlines discovery for Azure migration by guiding application owners through a concise, focused interview. It:
*   **Greets and starts** the conversation
*   **Requests an architectural document** up front
*   **Reviews** the document (if provided) to **avoid redundant questions**
*   Asks **up to 20 targeted questions** across key data points
*   **Generates a structured report** with a filterable table, grouped by category
*   **Assesses complexity (0–100)** and **suggests a treatment** (refactor, rehost, replatform, rearchitect) favoring **minimum effort** with **maximum modernization value**
*   **Offers to send** the intake data by **email** to the migration team as **JSON**

> 📝 The **questionnaire in SharePoint is optional**: if present, the agent references it as a knowledge source. If not, it will still proceed using the built-in data points and instructions.

* * *

Where It Can Be Used
--------------------

*   **Pre-migration discovery** at program intake
*   **Portfolio-level assessments** and waves planning
*   **Application owner engagement** when documentation quality varies
*   **PMO / Migration Factory** workflows to standardize inputs
*   **Early risk identification** (dependencies, data privacy, criticality, customer impact)
Channels:
*   **Microsoft 365 Copilot** (as a plugin)
*   **Microsoft Teams** (organizational app / team pin)
*   **Web test pane** (for designers and reviewers)

* * *

Solution Contents
-----------------

*   **Copilot Studio solution (.zip)** containing:
    *   The **Intake Agent** (topics, instructions, prompts)
    *   **Actions** to handle email send (via Outlook) and knowledge reference (via SharePoint)
    *   Suggested **environment variables** (e.g., migration team DL, SharePoint site/folder)
    *   Example **JSON schema** for email payload (documented below)

* * *

Prerequisites
-------------

*   **Copilot Studio** access in the target environment
*   **Connections**
    *   **Outlook (Office 365 Outlook)** to send email with the intake summary (**required**)
    *   **SharePoint** to reference a knowledge base folder / questionnaire (**optional**)
*   **Permissions**
    *   Outlook: permission to send emails (ideally via **shared mailbox** or **Send As** permission)
    *   SharePoint: **read** the site/library/folder where the questionnaire or knowledge base resides
*   **(Recommended)** A **Distribution List / Shared Mailbox** for the migration team (e.g., `migration-intake@contoso.com`)

* * *

Importing the Solution
----------------------

1.  Open **Copilot Studio** → **Solutions** → **Import solution**
2.  Upload the provided **.zip** file and proceed through the wizard.
3.  When prompted for **connections**, choose:
    *   **Office 365 Outlook** → Create/Select a connection
    *   **SharePoint** → Create/Select a connection (optional, but recommended)
4.  Map any **environment variables** (if included in the solution) to your org values (see below).
5.  Complete the import and verify the **agent** appears in your environment.

> ✅ After import, open the agent and run a quick **Test in web** to ensure the bot starts and greets.

* * *

Configuring Required Connections
--------------------------------

### Outlook (Email) Connection — **Required**

The agent needs Outlook to **send the intake summary** as an email with a JSON payload (and optional HTML summary).
**Setup Steps**
1.  In **Copilot Studio** → **Data** → **Connections**, ensure **Office 365 Outlook** is connected.
2.  (Recommended) Use a **shared mailbox** (e.g., `migration-intake@contoso.com`) to avoid sending from an individual account.
3.  Ensure the connection user has **Send As** rights if you plan to set the **From** field to the shared mailbox.
4.  In the agent’s **Action/Flow** that sends email:
    *   Set **To** = your migration team DL or mailbox (from environment variable)
    *   Optionally set **From (Send As)** = shared mailbox address
    *   Subject template: `Azure Migration Intake – <Application Name> – <Date>`
    *   Body: include **JSON attachment** and a **human-friendly HTML summary** (optional)

> ⚠️ **Important:** Email will be sent **from the account backing the Outlook connection** unless a **shared mailbox** and **Send As** are configured.

* * *

### SharePoint Knowledge Base Connection — **Optional**

Use this to point the agent to **reference content** (e.g., an intake **questionnaire** or **architecture standards**) to **inform better questions** and reduce owner burden.
**Setup Steps**
1.  In **Copilot Studio** → **Data sources** → **Add** → choose **SharePoint**.
2.  Provide the **SharePoint site** URL and select the **document library/folder** where your **questionnaire/KB** lives.
3.  Configure **indexing / refresh** per your org standards (if available in your environment).
4.  In the agent’s **instructions** and/or **knowledge source settings**, ensure the SharePoint path is referenced.

> ℹ️ The questionnaire is **not mandatory**. If absent, the agent proceeds using the data points and instructions.

* * *

### Recommended Environment Variables

Define the following in **Solutions → Environment variables** (or in a configuration topic):
*   `ENV_MIGRATION_TEAM_EMAILS` — e.g., `migration-intake@contoso.com`
*   `ENV_SHARED_MAILBOX_ADDRESS` — e.g., `migration-intake@contoso.com` (if using Send As)
*   `ENV_SHAREPOINT_SITE_URL` — e.g., `https://contoso.sharepoint.com/sites/MigrationKB`
*   `ENV_SHAREPOINT_KB_FOLDER` — e.g., `/Shared Documents/IntakeQuestionnaire`
These make the solution **portable** across environments (Dev/Test/Prod).

* * *

Publishing to Microsoft 365 Channels
------------------------------------

### Publish as a Microsoft 365 Copilot Plugin

1.  In the agent, go to **Publish** → **Channels**.
2.  Select **Microsoft 365 (Copilot)** or **Publish to Microsoft 365** (naming may vary by tenant/version).
3.  Complete the publishing wizard:
    *   Confirm **connections** are healthy
    *   Assign **audience** (everyone or specific groups)
4.  Ask your **M365 admin** (if required) to approve the plugin and set **availability policies**.
**User experience:** Users can invoke the agent inside **Microsoft 365 Copilot** with natural prompts (see examples below).

* * *

### Publish to Microsoft Teams

1.  In the agent, go to **Publish** → **Channels** → **Microsoft Teams**.
2.  Click **Turn on** / **Publish**, then:
    *   Choose **org-wide** availability or **specific users/teams**
    *   Optionally, coordinate with **Teams Admin** to set **App setup policies** for pinning
3.  Share the app link with target users, or add it to the relevant **Team**.
**User experience:** Users can chat with the agent in Teams; it supports file upload for architectural documents.

* * *

How to Start (Prompts)
----------------------

Use any of the following to begin:

"Start an Azure migration intake for the 'Payments Service'."

"I want to submit intake data for the HR Portal application."

"Begin intake. I have an architecture document to upload."

"Create a migration intake report for 'CRM Gateway' and email it to the migration team when done."

**If you have documentation**, start with:
"Here is the architectural document for 'Order Processing'. Please review it and only ask what's missing."

* * *

Conversation Flow & Behavior
----------------------------

The agent follows these **core instructions**:
*   **Greet and start** the conversation.
*   During the introduction, **request the architectural document** for the application.
*   If provided, **review its contents** and **only ask** questions **not answered** in the document, based on targeted data points for Azure migration.
*   If not available, proceed to ask **up to 20 targeted questions** to gather essential data.
*   **Collect details** about application infrastructure, dependencies, and **timeline dependencies in the next 6–12 months**.
*   **Do not ask** about **migration timeline** (owners cannot decide it).
*   Use these **data points** (see below) and ensure they are included in the **final report**.
*   Maintain a **friendly and efficient** conversation, respecting the owner’s time.
*   **Reference** the attached **questionnaire** (if configured) as a knowledge source for questions or report compilation.
*   **Never list** all questions upfront; ask **one by one**.
*   **Never ask** about **Treatment type** or **Complexity**; the agent **assesses** them based on collected data and presents them with justification.
*   For **region**, ask **where the app is hosted** and **where users are**, with **user count ranges**.
*   Always collect or extract **volumes**; if unknown/not in the doc, mark as **Missing Info** (avoid “large/small”).
*   At the end, create a **filterable table** with all data points and **notes**, **grouped by categories**, visually emphasized where helpful.
*   After the table, ask if the user is ready to **send intake data to the migration team**; if **Yes**, **convert to JSON** and **send via email**.

<img width="1442" height="887" alt="Items" src="https://github.com/user-attachments/assets/d8b43dfc-3e9e-4652-be27-7aafa44d7576" />

* * *

Data Points Captured & Report Structure
---------------------------------------

**Categories & Fields** (extracted/asked; displayed in a filterable table with a **Notes** column):
*   **📋 Overview**
    *   Application overview (max 10 lines)
    *   Business Criticality
    *   Customer Impact
    *   Assessed complexity (**0–100**) _(assessed by agent)_
    *   Suggested treatment _(refactor, rehost, replatform, rearchitect — minimal effort, max value; assessed by agent)_
*   **🔐 Data & Privacy**
    *   Data Privacy requirements
    *   Data Volume
    *   Real-time data streaming requirements
    *   Batch data processing requirements
*   **🧩 Architecture & Integrations**
    *   Current Tech / Integrations (internal & external)
    *   Dependency on other apps/data sources
    *   Service Integration Partners
    *   Automation
*   **🌐 Environment & Regions**
    *   Regions/Locations (hosting & users; user count ranges)
    *   Network Access details
    *   Identity Providers
    *   Number of Environments and details
*   **📅 Release & Dependencies**
    *   Release dependencies on corporate release cycle
    *   Timeline dependencies (next **6–12 months**)
The final table is filterable/sortable in-channel, with **bold headers** and optional **icons** for quick scanning.

* * *

Email JSON Payload (Schema & Example)
-------------------------------------

When the user confirms sending, the agent composes a **JSON** payload and emails it (optionally with an HTML summary).
**Schema (logical model):**

{  "applicationName": "Customer Insights Portal",  "appId": "CIP-2025",  "description": "A web-based application for analyzing customer behavior and generating insights.",  "businessCriticality": "High",  "informationClassification": "Confidential",  "dataPrivacyRequirements": "GDPR, CCPA",  "operationalConcerns": "Requires 24/7 uptime and regular security audits.",  "currentTechStack": ["React", "Node.js", "MongoDB", "Docker", "Kubernetes"],  "dataVolumeGB": "500",  "realTimeStreaming": "Yes",  "batchProcessing": "Daily",  "releaseCycle": "Bi-weekly",  "environments": ["Development", "Staging", "Production"],  "serviceIntegrationPartners": ["Salesforce", "Stripe", "Twilio"],  "dependencies": ["Internal Auth Service", "Data Lake", "Notification Service"],  "regions": ["EU", "US", "APAC"],  "networkAccess": "Private VPN",  "identityProvider": "Azure AD",  "automation": ["CI/CD", "Infrastructure as Code", "Monitoring Alerts"],  "customerImpact": "Direct impact on customer experience and retention.",  "assessedComplexity": "Medium",  "suggestedTreatment": "Modernization with cloud-native services.",  "smes": ["Alice Johnson", "Bob Smith", "Carlos Reyes"],  "userCountRange": "10,000 - 50,000"}

* * *

Operational Notes, Security & Compliance
----------------------------------------

*   **Document handling:** The agent **extracts** relevant information from the architectural document and **asks only missing questions**.
*   **Email sending identity:** Emails are sent **from the connection user** or a configured **shared mailbox** (with **Send As**).
*   **Data minimization:** The agent collects only the **enumerated data points**; anything else remains **out of scope**.
*   **Privacy:** If the user cannot provide volumes, the agent records **“Missing Info”** (no vague terms).
*   **Records:** Consider routing emails to a **shared mailbox** or system that archives intakes for auditability.

* * *

Troubleshooting
---------------

**The agent won’t send email**
*   Verify **Office 365 Outlook** connection is **authenticated**.
*   If using a **shared mailbox**, confirm **Send As** permissions and that the **From** field is set.
*   Check if **recipient DL** allows external or app-originated messages (if applicable).
**SharePoint knowledge isn’t used**
*   Confirm the **SharePoint data source** is added and accessible.
*   Verify the **site/library/folder** path and that files are in supported formats (e.g., docx, pdf, txt).
*   Allow time for **indexing/refresh** (if applicable).
**Agent keeps asking many questions even with a document**
*   Ensure the document is **readable** (not image-only PDF) or provide a **text-based** version.
*   The agent intentionally asks only for **missing** data points—check the doc actually contains them.
**Complexity/Treatment questions appear**
*   The instructions explicitly prevent asking for these; review the **agent instructions** and ensure no topic overrides.

* * *

FAQ
---

- **Q: Is the SharePoint questionnaire required?** A: **No.** It’s optional. If present, the agent uses it as a **knowledge source** for better prompts; if not, it proceeds with the built-in instructions.
- **Q: Where does the email come from?**\ A: From the **Outlook connection identity**. We recommend a **shared mailbox** with **Send As**, so the intake is sent from a team address.
- **Q: Can owners upload multiple documents?**\ A: Yes; the agent will reference provided documents and focus on **unanswered** items.
- **Q: Can we change the data points?**\ A: Yes; update the **instructions** or **topics** to extend/modify data points and the **final report table**.

* * *

Versioning & Change Log
-----------------------
## [0.9.5] - 2025-08-21

- Initial release.

* * *

Ownership & Support
-------------------

*   **Product Owner:** _Hammad Raza (Data & AI Architect, EAG)_
*   **Operational Contact / DL:** _hammad.raza@microsoft.com_
*   **Runbook:** _N/A_
*   **Issue Reporting:** _hammad.raza@microsoft.com_

* * *

### Quick Checklist

*   [ ] Solution imported to correct **environment**
*   [ ] **Outlook** connection configured (shared mailbox + Send As recommended)
*   [ ] **SharePoint** data source added (optional)
*   [ ] **Environment variables** set (emails, site URL, folder path)
*   [ ] Published to **M365 Copilot** and/or **Teams**
*   [ ] Test run: upload doc → Q\&A → report → **Email JSON** sent ✅

* * *





