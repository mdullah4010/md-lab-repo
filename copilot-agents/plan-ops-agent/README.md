# PlanOps Agent (Copilot Studio, Microsoft 365)

> **Purpose:** An agent dedicated to **Azure Migration Programs**. It manages **application portfolios**, **inventories**, and **intake data**; analyzes **migration progress**; and presents inventories in a **readable, filterable tabular format** suitable for **executives** and **technical SMEs**.

---

## Table of Contents

1. Introduction  
2. Where-you-can-use-this-agent  
3. Prerequisites  
4. Import-the-agent-into-copilot-studio  
5. Configure-required-connections  
   - Sharepoint-knowledge-connection-required  
   - Outlook-connection-optional-but-recommended  
6. Publish-to-microsoft-365-channels-teams--m365-copilot  
7. How-to-start-with-a-prompt  
8. Expected-behavior--outputs  
9. Wave-planning-rules-implemented-by-the-agent  
10. Data-locations--naming  
11. Governance--security--alm-tips  
12. Troubleshooting  
13. References  

---

## Introduction

**Scope & behavior (as implemented in the agent instructions):**

- Specializes in **Azure Migration Programs**; manages **portfolios, inventories, and intake**.  
- Accepts **intake data** and **analyzes migration progress** from provided data points.  
- Presents **inventories in tabular format** designed to be easily filtered and consumed by **executives** (plain language) and **technical SMEs** (drill‑down on request).  
- **Audience‑aware responses**: avoids jargon for executives; supports deep technical breakdowns when asked.  
- **No repetition** unless explicitly requested; **English‑only** communication; declines out‑of‑scope questions.  
- **Visuals**: Always uses **Mermaid** for Gantt charts, flows, and other supported visuals.  
- **Data sources (required by the agent logic):**  
  - Looks in SharePoint folder **`Intakes`** for **latest intake summaries/data**.  
  - Uses **`App-Portfolio-Contoso-Migration-Status-2026.json`** to read/update migration stages (e.g., Intake, Planning).  
- **Intake email drafting:** When asked to draft an intake request email, the agent **finds the application owner** and drafts a personalized message that:  
  - Briefs them about the migration program  
  - Directs them to initiate intake via **Intake Agent URL**: `[Intake Agent URL]`  
  - Explains that the **Intake agent** will ask questions and **send the data automatically**  

---

## Where you can use this agent

- **Microsoft Teams** (chats, meetings, channels) after you enable the channel.   
- **Copilot for Microsoft 365 (Copilot Chat)**—available alongside other agents once enabled and approved.   
- Optional channels such as **demo website** or **SharePoint pages** if you choose to enable them later.   

---

## Prerequisites

- **Copilot Studio access** with permission to **import/export agents** and **publish** to channels.  
- **SharePoint Online** site where your **`Intakes`** folder and **`App-Portfolio-Contoso-Migration-Status-2026.json`** reside (see **Data locations** below).  
- **Office 365 Outlook** connector (for optional send‑mail actions) using a **mailbox‑enabled** identity (service/shared mailbox recommended).   
- Appropriate **DLP policies** to allow **SharePoint** and **Outlook** connectors.

---

## Import the agent into Copilot Studio

1. Open **https://copilotstudio.microsoft.com/** → **Agents**.  
2. Select **Import** → choose the exported **agent `.zip`** file.  
3. Confirm details and proceed.  
4. After import, open the agent and verify:
   - **Knowledge** and **Tools (actions)** are present.  
   - **Connections** show as **Not configured** (we’ll configure next).  
5. Save.

> This flow is specific to **Copilot Studio agents** (not generic Power Platform solutions). After import, you’ll configure connections and then **publish** to Teams + Microsoft 365. 

---

## Configure required connections

### SharePoint knowledge connection (required)

**Why:** The agent must access **`Intakes`** and the **portfolio status JSON** to analyze progress and present inventories.

**Steps:**
1. Open the agent → **Knowledge** → **Add knowledge** → **SharePoint**.  
2. Provide your **SharePoint site URL** and **scope to the correct library/folder** (recommended: point directly to the **`Intakes`** folder and the library that hosts your JSON file).  
3. Save and **Publish** the agent to make the knowledge source active in channels.  
   - With **Authenticate with Microsoft** (default), the agent uses **the signed‑in user’s** permissions in Teams/Copilot. Ensure readers have access. 

> **Precision tip:** You can scope knowledge to a **site**, **library**, or **folder** for better relevance (e.g., point exactly at `/Documents/Intakes`). 

---

### Outlook connection (optional but recommended)

**Why:** Enables the agent (or supporting flows/tools) to **send** the intake request email (the agent always drafts the email; sending is optional).

**Steps:**
1. In the agent, open **Settings → Connections**.  
2. Locate **Office 365 Outlook** and **Sign in** using a **mailbox‑enabled** identity (service/shared mailbox recommended).  
3. Test with a simple **Send an email (V2)** action bound to this connection.  
   - If you see connector errors and you’re using a training/demo account, verify the mailbox license exists. 

---

## Publish to Microsoft 365 channels (Teams & M365 Copilot)

1. In the agent, click **Publish** to push the latest content.   
2. Open **Channels** → **Teams + Microsoft 365**.  
   - Toggle **Make agent available in Microsoft 365 Copilot chat** (if desired).  
   - Click **Availability options** → choose **Just me**, **Selected groups**, or **Org‑wide (admin approval)** → **Submit**.   
3. After admin approval, the agent appears in **Teams** and **Copilot Chat**. (Admins manage it under **Integrated apps**.) 

> Re‑publish after any update to apply changes everywhere. 

---

## How to start with a prompt

Use these prompts directly in **Teams** or **Copilot Chat**:

- **Executive portfolio snapshot**  
  *“Give me a plain‑English portfolio snapshot for Q4: total apps, intake completion %, current wave count, and any risks. Avoid jargon.”*

- **SME deep dive (filterable table)**  
  *“Show a filterable inventory table for apps with treatment ‘refactor’ and complexity ≥ medium. Include owner, BU, current stage, target wave, and projected Azure consumption.”*

- **Wave planning**  
  *“Create a 5‑wave migration plan for the portfolio using the wave planning rules. Max six apps per wave. Include rationale per wave.”*

- **Gantt**  
  *“Produce a Mermaid Gantt for the next 5 months with Waves 1–3 and key milestones (intake, target design, non‑prod, prod, cutover).”*

- **Intake request email draft**  
  *“Draft the intake request email for application ‘ContosoPay’. Find the app owner and personalize the message. Include the Intake Agent URL and explain that the Intake agent will ask questions and send data automatically.”*

---

## Expected behavior & outputs

- **Tables**: Clear, concise **markdown tables** suitable for filtering/export (e.g., to Excel) with columns like *App, Owner, BU, Treatment, Complexity, Current Stage, Target Wave, Azure Consumption*.  
- **Progress reporting per app** (when asked), using the **fixed stage scale**:  
  - Infrastructure data collected = **10%**  
  - Intake completed = **20%**  
  - Target design & migration planning = **40%**  
  - Non‑Prod migration execution = **60%**  
  - Prod migration execution = **80%**  
  - Cutover & Operations = **100%**  
- **Mermaid visuals** for timelines/flows (Gantt, process maps).  
- **Audience‑aware language**: executive summaries vs. SME detail on demand.  
- **No repetition** unless asked; **English‑only** responses.

---

## Wave planning rules (implemented by the agent)

1. **Objective:** Structured wave plan aligned to **timeline**, **complexity**, and **Azure consumption** goals.  
2. **Wave count:** Evenly distribute across program timeline; **max 5 waves**.  
3. **Apps per wave:** **≤ 6 apps** to reduce risk and maintain focus.  
4. **Complexity distribution:**  
   - **Waves 1–2:** least complex first (use complexity scores; if missing, use **treatment** as proxy).  
   - **Waves 3–5:** mix of **medium** and **high** complexity—ensure readiness and support capacity.  
5. **Azure consumption:** Consider projected consumption; if target architecture is missing, use **current infra metrics** to estimate.  
6. **Documentation:** For each wave, document **rationale**, **complexity**, **business priority**, and **Azure consumption** impact.

---

## Data locations & naming

- **SharePoint Site/Library**: Host portfolio data here.  
- **Folder:** **`Intakes/`** — latest intake summaries & datasets.  
- **Portfolio status JSON:** **`App-Portfolio-Contoso-Migration-Status-2026.json`** (used for stage updates such as *Intake*).

---

## Governance, security & ALM tips

- **SharePoint knowledge** runs **as the signed‑in user** when using default *Authenticate with Microsoft* in Teams/Copilot; ensure permissions are correct on the **`Intakes`** folder and JSON library.   
- **Outlook sending** is optional; drafting works without it. If you enable sending, use a **service/shared mailbox** and confirm DLP allows **Office 365 Outlook**.   
- **Publishing**: After any change, **Publish** to push updates to all connected channels.   
- **Admin distribution**: Use **Availability options** and **admin approval** to make the agent available org‑wide. 

---

## Troubleshooting

- **Agent doesn’t appear in Teams/Copilot**  
  - Channel not added or not **admin‑approved**. Re‑publish, check **Channels**, and request approval.   

- **SharePoint content not found or incomplete**  
  - Scope knowledge to the **exact folder/library**; verify user access; re‑publish.   

- **Email send errors (optional)**  
  - Verify the sending identity has an **Exchange Online mailbox** and the **Outlook connector** is authenticated. 

---

## References

- **Publish & Channels (Copilot Studio Learn):** <https://learn.microsoft.com/en-us/microsoft-copilot-studio/publication-fundamentals-publish-channels>   
- **Teams + Microsoft 365 channel (how to make available & admin approval):** <https://community.powerplatform.com/blogs/post/?postid=b29d2a22-a31d-f011-998a-7c1e52696149>   
- **SharePoint as a knowledge source:** <https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-sharepoint>   
- **Outlook connector (Send an email (V2)):** <https://learn.microsoft.com/en-us/connectors/office365/>   
- **Agents visible in Outlook’s Copilot Chat (admin note):** <https://m365admin.handsontek.net/agents-microsoft-365-copilot-chat-outlook/> 

---


