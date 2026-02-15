# 🔮 Nexus-Zero — Deploy to Your GCP Project

> **One command. 15 minutes. Full autonomous SRE platform on your infrastructure.**

This guide walks you through deploying the complete Nexus-Zero platform — 5 AI agents, chaos demo service, MCP bridge, and interactive dashboard — to your own Google Cloud project.

---

## 📋 Prerequisites

Before you start, you'll need:

| Requirement | How to Get It |
|-------------|--------------|
| **Google Cloud account** | [console.cloud.google.com](https://console.cloud.google.com) |
| **GCP Project with billing** | [Create Project](https://console.cloud.google.com/projectcreate) → [Enable Billing](https://console.cloud.google.com/billing) |
| **gcloud CLI installed** | [Install Guide](https://cloud.google.com/sdk/docs/install) |
| **Gemini API Key** (free) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Archestra account** (free) | [archestra.ai](https://archestra.ai) |
| **GitHub Token** (optional) | [github.com/settings/tokens](https://github.com/settings/tokens) — scope: `repo` (read) |

### Estimated Cost

| Resource | Cost |
|----------|------|
| Cloud SQL (db-f1-micro) | ~$8/month |
| Cloud Run (8 services, scale-to-zero) | ~$0/month (free tier) |
| Cloud Build | Free tier (120 min/day) |
| Secret Manager | Free tier |
| **Total** | **~$8-12/month** or ~$0.30/day |

> 💡 **Tip:** Stop Cloud SQL when not demoing to save costs:
> ```bash
> gcloud sql instances patch nexus-zero-db --activation-policy=NEVER --project=YOUR_PROJECT
> ```

---

## 🚀 Quick Start (One Command)

```bash
# Clone the repository
git clone https://github.com/something1703/Nexus-Zero.git
cd Nexus-Zero

# Authenticate with GCP
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Deploy everything
chmod +x deploy.sh
./deploy.sh --project YOUR_PROJECT_ID
```

The script will interactively prompt for:
1. **Database password** — Choose a strong password (min 8 chars)
2. **Gemini API Key** — From [AI Studio](https://aistudio.google.com/apikey)
3. **GitHub Token** — Optional, press Enter to skip

### What Gets Deployed

```
Your GCP Project
├── Cloud SQL (PostgreSQL 15)
│   └── nexus_zero database (schema + seed data)
├── Secret Manager
│   ├── db-password
│   ├── gemini-api-key
│   └── github-token (optional)
└── Cloud Run (8 services)
    ├── nexus-sentinel-agent    ← Detects anomalies in Cloud Logging
    ├── nexus-detective-agent   ← Root cause analysis with Gemini
    ├── nexus-historian-agent   ← Searches playbooks & past incidents
    ├── nexus-mediator-agent    ← Risk assessment & blast radius
    ├── nexus-executor-agent    ← Executes approved remediations
    ├── nexus-archestra-bridge  ← MCP proxy for Archestra A2A
    ├── nexus-order-api         ← Demo service with chaos injection
    └── nexus-dashboard         ← Landing page + interactive dashboard
```

---

## 🔗 Post-Deploy: Connect to Archestra

After `deploy.sh` finishes, it prints your agent SSE endpoints. You need to register them in Archestra Hub:

### Step 1: Register MCP Servers

1. Go to [Archestra Hub](https://archestra.ai) → Sign in
2. Navigate to **MCP Servers** → **Add Remote Server**
3. For each agent, add a remote MCP server:

| Name | SSE Endpoint |
|------|-------------|
| nexus-sentinel | `https://nexus-sentinel-agent-HASH.us-central1.run.app/sse` |
| nexus-detective | `https://nexus-detective-agent-HASH.us-central1.run.app/sse` |
| nexus-historian | `https://nexus-historian-agent-HASH.us-central1.run.app/sse` |
| nexus-mediator | `https://nexus-mediator-agent-HASH.us-central1.run.app/sse` |
| nexus-executor | `https://nexus-executor-agent-HASH.us-central1.run.app/sse` |

> Replace `HASH` with your actual Cloud Run service hash (printed by deploy.sh).

### Step 2: Create an Archestra Agent

1. Go to **Agents** → **Create Agent**
2. **Name:** `Nexus-Zero SRE`
3. **Model:** `gemini-2.5-flash`
4. **MCP Servers:** Select all 5 nexus-* servers you just added
5. **System Prompt:**

```
You are Nexus-Zero, an autonomous SRE incident response system.

When asked to detect anomalies or investigate incidents, follow this pipeline:
1. Use sentinel tools to detect anomalies in GCP Cloud Logging
2. For each incident found, use detective tools to investigate root cause
3. Use historian tools to search for matching playbooks and past solutions
4. Use mediator tools to assess blast radius and produce a risk recommendation
5. Use executor tools to propose remediation (requires human approval)

Always explain your reasoning. Be thorough but concise.
```

6. Save the agent

### Step 3: Get Your Credentials

1. **Agent ID:** Copy the UUID shown on your agent's detail page
2. **API Token:** Go to **Settings** → **API Keys** → Copy your bearer token

### Step 4: Update Dashboard

Run this command to connect your dashboard to Archestra:

```bash
gcloud run services update nexus-dashboard \
  --region us-central1 \
  --project YOUR_PROJECT_ID \
  --update-env-vars "ARCHESTRA_TOKEN=your-bearer-token,SENTINEL_AGENT_ID=your-agent-uuid"
```

---

## 🧪 Test Your Deployment

### 1. Open the Dashboard

Open the dashboard URL printed by `deploy.sh` in your browser. You should see the Nexus-Zero landing page.

### 2. Inject Chaos

Click **"Try Live Demo"** → then click **"💣 Break Service"** on any chaos mode. This injects real failures into your order-api service.

### 3. Trigger Detection

Click **"🔍 Run Sentinel Detection"**. The Sentinel agent scans your Cloud Logging for the errors caused by chaos injection.

### 4. Watch the Agents

The **Agent Thinking Panel** on the right shows real-time agent reasoning:
- 🛡️ Sentinel detects the anomaly
- 🔍 Detective investigates root cause
- 📚 Historian finds matching playbooks
- ⚖️ Mediator assesses risk and blast radius
- 🚀 Executor proposes remediation

### 5. Approve & Resolve

When a remediation action appears in **Pending Approvals**, click **✅ Approve** to execute it. Watch the incident resolve in real-time.

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Archestra Hub                      │
│            (A2A Agent Orchestration)                 │
│         ┌─────────────────────────┐                 │
│         │  Gemini 2.5 Flash LLM   │                 │
│         └────────────┬────────────┘                 │
│                      │ MCP Protocol                 │
│    ┌────┬────┬───────┼────┬─────┐                   │
│    ▼    ▼    ▼       ▼    ▼     │                   │
│  🛡️   🔍   📚      ⚖️   🚀    │                   │
│  SEN  DET  HIS     MED  EXE   │                   │
│    │    │    │       │    │     │                   │
│    └────┴────┴───────┴────┘     │                   │
│              │                   │                   │
│         Cloud SQL                │                   │
│     (incidents, playbooks,       │                   │
│      audit logs, services)       │                   │
└─────────────────────────────────────────────────────┘
          │                               │
    ┌─────▼─────┐                   ┌─────▼─────┐
    │ Dashboard  │                   │ Order API  │
    │ (Flask)    │                   │ (Chaos)    │
    └────────────┘                   └────────────┘
```

**Protocol Stack:**
- **MCP (Model Context Protocol):** Each agent exposes tools as MCP endpoints
- **Archestra A2A:** Agent-to-agent orchestration via the Hub
- **SSE (Server-Sent Events):** Real-time tool invocation transport
- **Cloud SQL:** Shared state store for all agents

---

## 🔧 Troubleshooting

### "Cloud SQL connection failed"
```bash
# Check instance is running
gcloud sql instances describe nexus-zero-db --project=YOUR_PROJECT

# Restart if stopped
gcloud sql instances patch nexus-zero-db --activation-policy=ALWAYS --project=YOUR_PROJECT
```

### "Agent tools not loading in Archestra"
- Agents may be cold-starting (scale-to-zero). Wait 30 seconds and retry.
- Verify SSE endpoint is accessible: `curl https://nexus-sentinel-agent-HASH.us-central1.run.app/sse`

### "Detection triggered but nothing happens"
- Check that `ARCHESTRA_TOKEN` and `SENTINEL_AGENT_ID` are set on the dashboard.
- Verify the Archestra agent has all 5 MCP servers connected.

### "Approvals don't appear"
- Make sure chaos was injected first (errors need to exist in Cloud Logging).
- Sentinel detection needs to be triggered to create incidents.
- Check the Agent Thinking panel for error messages.

### "Permission denied" errors during deploy
```bash
# Ensure you have Owner or Editor role
gcloud projects get-iam-policy YOUR_PROJECT --flatten="bindings[].members" \
  --filter="bindings.members:$(gcloud config get-value account)" \
  --format="table(bindings.role)"
```

---

## 🧹 Tear Down

To completely remove all Nexus-Zero resources:

```bash
./teardown.sh --project YOUR_PROJECT_ID
```

This deletes:
- All 8+ Cloud Run services
- Cloud SQL instance and all data
- Secret Manager secrets
- Container images

> ⚠️ **Warning:** This is irreversible. All incident data and playbooks will be lost.

---

## 📁 Project Structure

```
Nexus-Zero/
├── deploy.sh              ← One-command deployment
├── teardown.sh            ← One-command teardown
├── JUDGE_SETUP.md         ← This file
├── database/
│   ├── schema.sql         ← 9 tables, indexes, functions
│   └── seed_data.sql      ← 5 services, 8 playbooks, 10 historical incidents
├── mcp-agents/
│   ├── common/            ← Shared config, credential store, DB utils
│   ├── sentinel-agent/    ← Anomaly detection (Cloud Logging)
│   ├── detective-agent/   ← Root cause analysis (Gemini + GitHub)
│   ├── historian-agent/   ← Playbook search (pgvector similarity)
│   ├── mediator-agent/    ← Risk assessment (blast radius + guardrails)
│   ├── executor-agent/    ← Gated remediation (human approval required)
│   └── archestra-bridge/  ← MCP→HTTP proxy for Archestra Hub
├── demo-services/
│   └── order-api/         ← Chaos-injectable Flask service
├── web/
│   ├── app.py             ← Flask backend (14+ API endpoints)
│   └── templates/
│       ├── index.html      ← Landing page
│       ├── dashboard.html  ← Interactive SRE dashboard
│       └── how-it-works.html ← Agent deep-dive documentation
└── scripts/               ← Utility scripts (chaos, reset, verify)
```

---

## 💡 Tips for Judges

1. **Inject chaos first** — The system needs real errors to detect. Click "Break Service" before running detection.

2. **Watch Agent Thinking** — The right panel shows real-time AI reasoning. This is where the explainability shines.

3. **Try different chaos modes** — Each mode (db_pool_exhaustion, memory_leak, cascade_failure) produces different error patterns that agents handle differently.

4. **Check the audit trail** — Click on any incident to see the full timeline of every agent action.

5. **Read "How We Work"** — The deep-dive page explains each agent's tools, reasoning, and cost transparency.

---

<p align="center">
  Built with ❤️ by <strong>Team Nexus-Zero</strong><br>
  <em>2 Fast 2 MCP Hackathon by Archestra — February 2026</em>
</p>
