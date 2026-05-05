"""Training and validation datasets for summarizer prompt optimization.

Each task has:
  - input: multi-analyst output to summarize (simulating GroupChat results)
  - expected_output: reference summary with headline, bullets, datasets
"""
from __future__ import annotations


TRAIN_TASKS: list[dict] = [
    {
        "input": (
            "[Outage Analyst]\n"
            "Sev 1 outage INC-2026-40020 in eastus affecting Azure DNS. TTM P75 = 4.2h. "
            "22 child incidents. 42 linked SRs. Root cause: backend configuration change deployed at 06:00 UTC.\n\n"
            "[AIRO Analyst]\n"
            "Azure DNS AIRO in eastus dropped to 96.1% from 98.5% baseline. "
            "Fleet-wide DNS AIRO declined 2.1% this quarter.\n\n"
            "[Customer Insights]\n"
            "5 S500 customers impacted. Fabrikam Inc filed 8 SRs including 1 CritSit (Sev A). "
            "Woodgrove Bank and Alpine Ski House also affected."
        ),
        "expected_output": (
            "## Headline\nSev 1 DNS outage in eastus impacted 5 S500 customers with 42 SRs\n\n"
            "## Key Findings\n"
            "- INC-2026-40020: Azure DNS connectivity failures in eastus caused by config change\n"
            "- TTM P75 at 4.2h, 22 child incidents indicate widespread platform impact\n"
            "- DNS AIRO dropped to 96.1% (from 98.5% baseline)\n"
            "- Fabrikam Inc most affected: 8 SRs, 1 CritSit Sev A\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, CustomerImpact"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "3 Sev 2 outages in centralus this week. AKS (2) and Azure SQL (1). "
            "Average TTM 3.5h. No Sev 1 incidents.\n\n"
            "[Customer Insights]\n"
            "Tailspin Toys filed 4 SRs. No CritSits. Adventure Works has 2 open cases on AKS."
        ),
        "expected_output": (
            "## Headline\n3 Sev 2 outages in centralus — AKS dominant with 2 incidents\n\n"
            "## Key Findings\n"
            "- centralus had 3 Sev 2 outages: AKS (2), Azure SQL (1)\n"
            "- Average TTM 3.5h, no Sev 1 escalations\n"
            "- Tailspin Toys (4 SRs) and Adventure Works (2 open AKS cases) impacted\n\n"
            "## Datasets Referenced\nOutages, SupportCases"
        ),
    },
    {
        "input": (
            "[AIRO Analyst]\n"
            "Fleet-wide AIRO stable at 98.7%. No services below 97% target. "
            "Azure Storage improved 0.3pp to 99.4%.\n\n"
            "[Outage Analyst]\n"
            "Zero outages in the last 48 hours. Lowest outage-free period this quarter.\n\n"
            "[Customer Insights]\n"
            "No active CritSits. 3 S500 customers have open non-critical SRs."
        ),
        "expected_output": (
            "## Headline\nAll-clear: no outages in 48h, AIRO stable at 98.7%, zero CritSits\n\n"
            "## Key Findings\n"
            "- Fleet AIRO stable at 98.7%, all services above 97% target\n"
            "- Azure Storage improved 0.3pp to 99.4%\n"
            "- Zero outages in 48 hours — best stretch this quarter\n"
            "- No active CritSits across S500 accounts\n\n"
            "## Datasets Referenced\nAIRO, Outages, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Cascading outage: Azure DNS failure in australiaeast cascaded to AKS and App Service. "
            "3 Sev 2 incidents created. TTM still open at 5+ hours.\n\n"
            "[AIRO Analyst]\n"
            "australiaeast AIRO dropped from 99.1% to 95.2% — worst region currently. "
            "All 3 impacted services below 97% threshold.\n\n"
            "[Customer Insights]\n"
            "Alpine Ski House and Datum Corp affected. Alpine Ski House filed CritSit (Sev A) on Cosmos DB. "
            "Datum Corp has 3 open SRs."
        ),
        "expected_output": (
            "## Headline\nCascading DNS outage in australiaeast — AIRO at 95.2%, 2 customers with CritSits\n\n"
            "## Key Findings\n"
            "- DNS failure cascaded to AKS and App Service (3 Sev 2 incidents)\n"
            "- TTM still open at 5+ hours\n"
            "- australiaeast AIRO crashed to 95.2% (from 99.1%)\n"
            "- Alpine Ski House filed CritSit Sev A; Datum Corp has 3 open SRs\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, CascadingImpact"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "MSO event detected: ImpactedServiceOid != ResponsibleServiceOid for INC-2026-40015. "
            "Azure SQL responsible, but Azure App Service and Azure Functions also impacted. "
            "4 services total.\n\n"
            "[AIRO Analyst]\n"
            "AIRO for all 4 services below 98% in westeurope. "
            "Cross-service correlation coefficient 0.92.\n\n"
            "[Customer Insights]\n"
            "Fabrikam Inc, Woodgrove Bank, Tailspin Toys all affected. "
            "Combined 15 SRs, 3 CritSits."
        ),
        "expected_output": (
            "## Headline\nMulti-Service Outage: Azure SQL cascading to 3 dependent services in westeurope\n\n"
            "## Key Findings\n"
            "- MSO event: SQL responsible, App Service + Functions + 1 other impacted\n"
            "- All 4 services AIRO below 98% in westeurope (correlation 0.92)\n"
            "- 3 S500 customers affected: 15 SRs and 3 CritSits combined\n\n"
            "## Datasets Referenced\nOutages, MSO, AIRO, SupportCases, CustomerImpact"
        ),
    },
    {
        "input": (
            "[Customer Insights]\n"
            "Fabrikam Inc has 7 repeat escalations this quarter on Azure SQL Database. "
            "Pattern: connectivity timeout → escalation within 2 hours → DRI engagement. "
            "QEIS score dropped to 68.2 (High Risk)."
        ),
        "expected_output": (
            "## Headline\nFabrikam Inc escalation pattern: 7 repeat SQL DB escalations, QEIS at High Risk\n\n"
            "## Key Findings\n"
            "- 7 repeat escalations on Azure SQL Database this quarter\n"
            "- Pattern: connectivity timeout → escalation within 2h → DRI engagement\n"
            "- QEIS score at 68.2 — classified as High Risk\n\n"
            "## Datasets Referenced\nSupportCases, QEIS, EscalationHistory"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Bowler scorecard update: TTM P75=4.1h (target 4.0h, MISS by 6min). "
            "TTO P75=38min (MEET). TTN P75=12min (MEET). AutoDetect=58% (target 60%, MISS).\n\n"
            "[AIRO Analyst]\n"
            "Fleet AIRO at 98.7%, down from 99.1% at start of quarter. "
            "3 services consistently below target."
        ),
        "expected_output": (
            "## Headline\nBowler scorecard: TTM and AutoDetect targets missed; AIRO declining\n\n"
            "## Key Findings\n"
            "- TTM P75 at 4.1h — MISS by 6 minutes\n"
            "- TTO P75 (38min) and TTN P75 (12min) both MEET targets\n"
            "- AutoDetect at 58% — MISS (target 60%)\n"
            "- Fleet AIRO declined from 99.1% to 98.7% this quarter\n\n"
            "## Datasets Referenced\nBowlerMetrics, AIRO"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Azure Load Balancer: 4 outages in southcentralus this month. "
            "All Sev 3, avg TTM 1.5h. No CritSits.\n\n"
            "[AIRO Analyst]\n"
            "Load Balancer AIRO in southcentralus: 97.9%. Below 99% target for 3 consecutive months.\n\n"
            "[Customer Insights]\n"
            "No S500 customers with active issues on Load Balancer. "
            "2 non-S500 customers filed SRs."
        ),
        "expected_output": (
            "## Headline\nAzure Load Balancer chronic issues in southcentralus — 4 outages, AIRO at 97.9%\n\n"
            "## Key Findings\n"
            "- 4 Sev 3 outages this month, avg TTM 1.5h\n"
            "- AIRO at 97.9% — below target for 3 consecutive months\n"
            "- No S500 impact; 2 non-S500 customers filed SRs\n"
            "- Low severity but chronic pattern needs attention\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Repair items: 45 open across all services. Azure Networking has lowest completion rate at 68%.\n\n"
            "[AIRO Analyst]\n"
            "Services with incomplete repair items correlate with lower AIRO (r=-0.75)."
        ),
        "expected_output": (
            "## Headline\n45 open repair items — Azure Networking completion at 68% correlates with low AIRO\n\n"
            "## Key Findings\n"
            "- 45 open repair items across services\n"
            "- Azure Networking worst at 68% completion rate\n"
            "- Strong correlation (r=-0.75) between incomplete repairs and low AIRO\n\n"
            "## Datasets Referenced\nRepairItems, AIRO"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Configuration change outages account for 28% of all incidents this quarter. "
            "Up from 22% last quarter. Top offenders: Azure DNS and AKS.\n\n"
            "[AIRO Analyst]\n"
            "Config-change-related outages drag AIRO down by 1.2pp on average vs other root causes.\n\n"
            "[Customer Insights]\n"
            "62% of S500 CritSits this quarter were triggered by config-change outages. "
            "Fabrikam Inc had 3 config-related CritSits."
        ),
        "expected_output": (
            "## Headline\nConfiguration changes are the #1 outage driver at 28% — linked to 62% of S500 CritSits\n\n"
            "## Key Findings\n"
            "- Config change outages at 28% of total (up from 22% last quarter)\n"
            "- Top offenders: Azure DNS and AKS\n"
            "- Config-related outages drag AIRO 1.2pp lower than other causes\n"
            "- 62% of S500 CritSits linked to config changes; Fabrikam worst with 3\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, RootCauseAnalysis"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Historical comparison: April 2026 has 55 outages vs 42 in April 2025 (+31%). "
            "Sev 1 count doubled from 3 to 6.\n\n"
            "[Customer Insights]\n"
            "28 unique customers impacted this month vs 22 last month. "
            "8 S500 accounts affected."
        ),
        "expected_output": (
            "## Headline\nApril 2026 outages up 31% YoY — Sev 1 doubled, 28 customers impacted\n\n"
            "## Key Findings\n"
            "- 55 outages in April 2026 vs 42 in April 2025 (+31%)\n"
            "- Sev 1 count doubled: 3 → 6\n"
            "- 28 unique customers impacted (up from 22), including 8 S500 accounts\n\n"
            "## Datasets Referenced\nOutages, CustomerImpact"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "eastus had 45 outages this quarter — most of any region. "
            "15 Sev 1, 20 Sev 2, 10 Sev 3.\n\n"
            "[AIRO Analyst]\n"
            "eastus AIRO at 97.5% — third worst region. "
            "Below target for 5 consecutive months.\n\n"
            "[Customer Insights]\n"
            "12 S500 customers have resources in eastus. "
            "Fabrikam Inc and Woodgrove Bank are the most concentrated."
        ),
        "expected_output": (
            "## Headline\neastus is the most outage-prone region: 45 incidents, AIRO at 97.5%\n\n"
            "## Key Findings\n"
            "- 45 outages in eastus — highest of any region (15 Sev1, 20 Sev2, 10 Sev3)\n"
            "- AIRO at 97.5%, below target for 5 consecutive months\n"
            "- 12 S500 customers in eastus; Fabrikam and Woodgrove most concentrated\n\n"
            "## Datasets Referenced\nOutages, AIRO, CustomerSubscriptions"
        ),
    },
    {
        "input": (
            "[AIRO Analyst]\n"
            "AIRO distribution: P25=97.8%, P50=98.9%, P75=99.4%, P95=99.8%. "
            "Long tail below P25 driven by 5 services.\n\n"
            "[Outage Analyst]\n"
            "The 5 services in AIRO long tail account for 60% of all outages."
        ),
        "expected_output": (
            "## Headline\n5 services drive AIRO long tail and 60% of all outages\n\n"
            "## Key Findings\n"
            "- AIRO distribution: P50=98.9%, P25=97.8%\n"
            "- 5 services below P25 create a long tail\n"
            "- These same 5 services account for 60% of all outages\n\n"
            "## Datasets Referenced\nAIRO, Outages"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "TTT (Time to Triage) analysis: P75 = 8 minutes this month. "
            "Best performing: Azure Storage (3 min). Worst: Azure DNS (18 min).\n\n"
            "[AIRO Analyst]\n"
            "No direct AIRO correlation with TTT — triage speed doesn't predict availability."
        ),
        "expected_output": (
            "## Headline\nTTT P75 at 8 minutes — Azure DNS slowest at 18 min, no AIRO correlation\n\n"
            "## Key Findings\n"
            "- TTT P75 = 8 minutes (fleet-wide)\n"
            "- Best: Azure Storage (3 min), Worst: Azure DNS (18 min)\n"
            "- No statistically significant correlation between TTT and AIRO\n\n"
            "## Datasets Referenced\nTTTMetrics, AIRO"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Auto-detection rate at 58%, target 60%. SHIM auto-detection improved for Cosmos DB "
            "but declined for AKS.\n\n"
            "[Customer Insights]\n"
            "Manually declared outages have 40% higher SR count than auto-detected ones. "
            "Customer sees impact before detection."
        ),
        "expected_output": (
            "## Headline\nAuto-detection at 58% (target: 60%) — manual declarations correlate with 40% more SRs\n\n"
            "## Key Findings\n"
            "- Auto-detection rate 58%, missing 60% target\n"
            "- Cosmos DB improved; AKS declined in auto-detection\n"
            "- Manually declared outages have 40% more SRs — customers detect before monitoring\n\n"
            "## Datasets Referenced\nOutages, SHIMAutoDetection, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "No data available — query timed out.\n\n"
            "[AIRO Analyst]\n"
            "Fleet AIRO at 98.7%. All metrics nominal.\n\n"
            "[Customer Insights]\n"
            "No data available — service unavailable."
        ),
        "expected_output": (
            "## Headline\nPartial data: AIRO nominal at 98.7% — outage and customer data unavailable\n\n"
            "## Key Findings\n"
            "- Fleet AIRO at 98.7% — all services nominal\n"
            "- Outage analyst data unavailable (query timeout)\n"
            "- Customer insights data unavailable (service error)\n"
            "- Recommend retry for complete picture\n\n"
            "## Datasets Referenced\nAIRO (partial)"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Conflicting data: Azure SQL shows 0 outages in eastus2 this week, "
            "but 15 SRs filed mentioning SQL connectivity failures.\n\n"
            "[Customer Insights]\n"
            "Woodgrove Bank filed 5 of the 15 SRs. All mention 'connection timeout'. "
            "No linked incident found."
        ),
        "expected_output": (
            "## Headline\n15 SQL connectivity SRs in eastus2 with no declared outage — potential undeclared event\n\n"
            "## Key Findings\n"
            "- 0 declared outages for SQL in eastus2, but 15 SRs mention connectivity failures\n"
            "- Woodgrove Bank accounts for 5 of 15 SRs (all 'connection timeout')\n"
            "- Data suggests potential undeclared outage — recommend investigation\n\n"
            "## Datasets Referenced\nOutages, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Sev 3 outage INC-2026-40050 in japaneast. Azure Event Hubs. TTM 45 min. "
            "1 child incident. 2 linked SRs.\n\n"
            "[AIRO Analyst]\n"
            "Event Hubs AIRO in japaneast: 99.4%. Minimal impact.\n\n"
            "[Customer Insights]\n"
            "Datum Corp filed both SRs. Non-CritSit, Sev C. Low urgency."
        ),
        "expected_output": (
            "## Headline\nLow-impact Sev 3 Event Hubs outage in japaneast — 45min TTM, 2 SRs from Datum Corp\n\n"
            "## Key Findings\n"
            "- INC-2026-40050: Sev 3, Event Hubs, japaneast, TTM 45 minutes\n"
            "- AIRO impact minimal — still at 99.4%\n"
            "- Only Datum Corp affected (2 non-critical SRs)\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "10 outages mitigated in the last 24 hours. Average TTM 2.1h. "
            "All Sev 2 or 3. Repair items generated for 8 of 10.\n\n"
            "[AIRO Analyst]\n"
            "AIRO recovering across all regions. Average recovery time 6 hours post-mitigation.\n\n"
            "[Customer Insights]\n"
            "12 customers received postmortem notifications. "
            "Satisfaction survey sent to 8 S500 accounts."
        ),
        "expected_output": (
            "## Headline\n10 outages mitigated in 24h — AIRO recovering, postmortems in progress\n\n"
            "## Key Findings\n"
            "- 10 outages mitigated, avg TTM 2.1h (all Sev 2/3)\n"
            "- 8 of 10 have repair items generated\n"
            "- AIRO recovering, avg 6h post-mitigation recovery time\n"
            "- 12 customers notified; satisfaction surveys sent to 8 S500 accounts\n\n"
            "## Datasets Referenced\nOutages, RepairItems, AIRO, CustomerNotifications"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Weekly trend: 8 outages Mon, 12 Tue, 15 Wed, 10 Thu, 5 Fri. "
            "Wednesday spike correlates with deployment windows.\n\n"
            "[AIRO Analyst]\n"
            "Wednesday AIRO dips are consistent pattern — avg 0.5pp lower than other weekdays."
        ),
        "expected_output": (
            "## Headline\nWednesday outage spike (15) correlates with deployment windows and AIRO dips\n\n"
            "## Key Findings\n"
            "- Weekly pattern: Mon 8, Tue 12, Wed 15, Thu 10, Fri 5\n"
            "- Wednesday spike coincides with deployment windows\n"
            "- Wednesday AIRO averages 0.5pp lower than other weekdays\n\n"
            "## Datasets Referenced\nOutages, AIRO, DeploymentSchedule"
        ),
    },
]


VAL_TASKS: list[dict] = [
    {
        "input": (
            "[Outage Analyst]\n"
            "2 Sev 1 outages in westus2: Azure Networking and AKS. Both active, TTM > 3 hours.\n\n"
            "[AIRO Analyst]\n"
            "westus2 AIRO at 95.8%. Worst since January.\n\n"
            "[Customer Insights]\n"
            "Adventure Works and Contoso Ltd affected. Adventure Works filed CritSit Sev A."
        ),
        "expected_output": (
            "## Headline\n2 active Sev 1 outages in westus2 — AIRO at 95.8%, Adventure Works CritSit\n\n"
            "## Key Findings\n"
            "- Azure Networking + AKS Sev 1 outages in westus2, both active (TTM > 3h)\n"
            "- AIRO crashed to 95.8% — worst since January\n"
            "- Adventure Works filed CritSit Sev A; Contoso Ltd also impacted\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[AIRO Analyst]\n"
            "Azure DNS AIRO improved 1.5pp in australiaeast after remediation. Now at 98.8%.\n\n"
            "[Customer Insights]\n"
            "Alpine Ski House confirmed improvement — no new SRs in 10 days."
        ),
        "expected_output": (
            "## Headline\nDNS AIRO recovery in australiaeast: up 1.5pp to 98.8%, Alpine Ski House stable\n\n"
            "## Key Findings\n"
            "- Azure DNS AIRO in australiaeast recovered 1.5pp to 98.8%\n"
            "- Alpine Ski House: zero new SRs in 10 days, confirming improvement\n\n"
            "## Datasets Referenced\nAIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Capacity exhaustion in southcentralus: 3 services affected (AKS, App Service, Functions). "
            "Throughput dropped 80% from baseline.\n\n"
            "[AIRO Analyst]\n"
            "southcentralus AIRO for compute services: 93.2%. Critical threshold breached.\n\n"
            "[Customer Insights]\n"
            "Tailspin Toys and Fabrikam Inc both report deployment failures. "
            "Tailspin filed CritSit. 18 total SRs from 6 customers."
        ),
        "expected_output": (
            "## Headline\nCapacity exhaustion in southcentralus — compute AIRO at 93.2%, 6 customers impacted\n\n"
            "## Key Findings\n"
            "- Capacity exhaustion affecting AKS, App Service, Functions\n"
            "- Throughput down 80%; compute AIRO at 93.2% (critical)\n"
            "- Tailspin Toys filed CritSit; Fabrikam also affected\n"
            "- 18 SRs from 6 customers — deployment failures reported\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, CapacityMetrics"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Quiet week: 3 Sev 3 outages only. All mitigated within 1 hour.\n\n"
            "[AIRO Analyst]\n"
            "All services above 99% AIRO.\n\n"
            "[Customer Insights]\n"
            "No CritSits. 5 routine SRs across S500 accounts."
        ),
        "expected_output": (
            "## Headline\nQuiet week: 3 low-severity outages, all services above 99% AIRO, no CritSits\n\n"
            "## Key Findings\n"
            "- Only 3 Sev 3 outages, all mitigated within 1 hour\n"
            "- All services above 99% AIRO target\n"
            "- No CritSits; 5 routine SRs across S500 accounts\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "QCO summary: 8 Quality Critical Outages this month. 4 DNS, 2 AKS, 2 SQL.\n\n"
            "[AIRO Analyst]\n"
            "QCO services average AIRO 96.5% vs 98.9% fleet average.\n\n"
            "[Customer Insights]\n"
            "QCO outages linked to 85 SRs and 12 CritSits across 15 customers."
        ),
        "expected_output": (
            "## Headline\n8 QCOs this month — DNS leads with 4; 12 CritSits across 15 customers\n\n"
            "## Key Findings\n"
            "- 8 QCOs: DNS (4), AKS (2), SQL (2)\n"
            "- QCO service AIRO at 96.5% vs 98.9% fleet average\n"
            "- 85 SRs and 12 CritSits linked across 15 customers\n\n"
            "## Datasets Referenced\nQCO, AIRO, SupportCases, CustomerImpact"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Sev 1 outage INC-2026-40030 resolved after 12 hours. Azure Networking in centralus. "
            "Root cause: BGP route leak.\n\n"
            "[AIRO Analyst]\n"
            "centralus AIRO recovering — currently at 97.2%, up from 94.5% during incident.\n\n"
            "[Customer Insights]\n"
            "8 S500 customers affected. Woodgrove Bank had 3-hour complete outage. "
            "Total: 35 SRs, 5 CritSits."
        ),
        "expected_output": (
            "## Headline\n12-hour Sev 1 networking outage resolved in centralus — BGP route leak, 8 S500 customers hit\n\n"
            "## Key Findings\n"
            "- INC-2026-40030: Azure Networking, centralus, TTM 12h, BGP route leak\n"
            "- AIRO recovering from 94.5% to 97.2%\n"
            "- 8 S500 customers affected; Woodgrove Bank had 3h complete outage\n"
            "- 35 SRs and 5 CritSits filed\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, CustomerImpact"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Monthly comparison: 55 outages in April vs 48 in March (+15%). "
            "Sev 1 count unchanged at 6.\n\n"
            "[Customer Insights]\n"
            "Customer satisfaction survey results: 72% satisfied (down from 78% in March). "
            "Top complaint: slow communication during outages."
        ),
        "expected_output": (
            "## Headline\nApril outages up 15% vs March — customer satisfaction dropped to 72%\n\n"
            "## Key Findings\n"
            "- 55 outages in April vs 48 in March (+15%); Sev 1 stable at 6\n"
            "- Customer satisfaction at 72% (down from 78%)\n"
            "- Top complaint: slow communication during outages\n\n"
            "## Datasets Referenced\nOutages, CustomerSatisfaction"
        ),
    },
    {
        "input": (
            "[AIRO Analyst]\n"
            "Azure Cosmos DB AIRO at 99.3% fleet-wide. Top performer for the quarter.\n\n"
            "[Customer Insights]\n"
            "Cosmos DB has the lowest SR rate per 1000 deployments. "
            "Only 2 CritSits across all customers this quarter."
        ),
        "expected_output": (
            "## Headline\nAzure Cosmos DB top performer: 99.3% AIRO, lowest SR rate, only 2 CritSits\n\n"
            "## Key Findings\n"
            "- Cosmos DB AIRO at 99.3% — best in fleet\n"
            "- Lowest SR rate per 1000 deployments\n"
            "- Only 2 CritSits across all customers this quarter\n\n"
            "## Datasets Referenced\nAIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Azure Functions: 0 outages this month. First outage-free month in 6 months.\n\n"
            "[AIRO Analyst]\n"
            "Functions AIRO at 99.7% — highest ever recorded.\n\n"
            "[Customer Insights]\n"
            "Functions SRs down 60% from last month. Zero escalations."
        ),
        "expected_output": (
            "## Headline\nAzure Functions milestone: first outage-free month, AIRO at record 99.7%\n\n"
            "## Key Findings\n"
            "- Zero outages — first outage-free month in 6 months\n"
            "- AIRO at 99.7% (record high)\n"
            "- SRs down 60%, zero escalations\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "TTM trends by service: Azure DNS improving (-15%), AKS stable, Azure SQL worsening (+8%).\n\n"
            "[AIRO Analyst]\n"
            "TTM improvement correlates with AIRO improvement (r=0.68).\n\n"
            "[Customer Insights]\n"
            "Customers of improving-TTM services show 12% higher satisfaction scores."
        ),
        "expected_output": (
            "## Headline\nTTM trends mixed: DNS improving, SQL worsening — correlates with AIRO and satisfaction\n\n"
            "## Key Findings\n"
            "- TTM: DNS -15% (improving), AKS stable, SQL +8% (worsening)\n"
            "- TTM improvement correlates with AIRO improvement (r=0.68)\n"
            "- Improving-TTM services see 12% higher customer satisfaction\n\n"
            "## Datasets Referenced\nTTMMetrics, AIRO, CustomerSatisfaction"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Weekend outage pattern: 70% fewer outages on weekends. "
            "But weekend Sev 1 TTM is 40% longer than weekday.\n\n"
            "[Customer Insights]\n"
            "Weekend CritSits take 2x longer to first DRI response."
        ),
        "expected_output": (
            "## Headline\nWeekend outages 70% fewer but Sev 1 TTM 40% longer — DRI response 2x slower\n\n"
            "## Key Findings\n"
            "- 70% fewer outages on weekends\n"
            "- But weekend Sev 1 TTM is 40% longer than weekday\n"
            "- Weekend CritSits take 2x longer to first DRI response\n\n"
            "## Datasets Referenced\nOutages, SupportCases, DRIResponse"
        ),
    },
    {
        "input": (
            "[Outage Analyst]\n"
            "Cross-region outage: Azure DNS affected 4 regions simultaneously. "
            "eastus, centralus, westeurope, australiaeast. 1 Sev 1, 3 Sev 2.\n\n"
            "[AIRO Analyst]\n"
            "DNS AIRO dropped below 95% in all 4 regions. Fleet DNS AIRO at 94.2%.\n\n"
            "[Customer Insights]\n"
            "All 52 S500 customers notified. 22 filed SRs. 8 CritSits filed within 30 minutes."
        ),
        "expected_output": (
            "## Headline\nCross-region DNS outage: 4 regions, fleet DNS AIRO at 94.2%, 8 CritSits in 30 minutes\n\n"
            "## Key Findings\n"
            "- Azure DNS affected 4 regions: eastus, centralus, westeurope, australiaeast\n"
            "- 1 Sev 1 + 3 Sev 2; DNS AIRO below 95% in all 4 regions (fleet 94.2%)\n"
            "- All 52 S500 customers notified; 22 filed SRs; 8 CritSits within 30 min\n\n"
            "## Datasets Referenced\nOutages, AIRO, SupportCases, CustomerNotifications"
        ),
    },
]
