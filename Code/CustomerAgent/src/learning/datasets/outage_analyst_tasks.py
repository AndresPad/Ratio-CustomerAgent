"""Training and validation datasets for outage analyst prompt optimization.

Each task has:
  - input: natural language query about outages/incidents
  - expected_sql: reference T-SQL query or key fragments
  - expected_output: reference analytical output
"""
from __future__ import annotations


TRAIN_TASKS: list[dict] = [
    {
        "input": "How many Sev 1 outages occurred in the last 30 days?",
        "expected_sql": "SELECT COUNT(*) FROM Outages WHERE Severity = 1 AND CreateDate >= DATEADD(day, -30, GETDATE())",
        "expected_output": "Sev 1 outages in last 30 days: 12 total. 8 mitigated, 4 active.",
    },
    {
        "input": "What is the TTM P75 for Azure SQL Database outages this quarter?",
        "expected_sql": "SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY TTM) FROM Outages WHERE ServiceName = 'Azure SQL Database' AND IsOutage = 1",
        "expected_output": "TTM P75 for Azure SQL Database: 4.2 hours this quarter. Target: 4.0 hours. Over target by 12 minutes.",
    },
    {
        "input": "Show me the outage severity distribution by region",
        "expected_sql": "SELECT Region, Severity, COUNT(*) FROM Outages WHERE IsOutage = 1 GROUP BY Region, Severity",
        "expected_output": "Outage distribution: eastus leads with 45 outages (15 Sev1, 20 Sev2, 10 Sev3). westeurope second with 32.",
    },
    {
        "input": "Which services caused the most QCO outages this month?",
        "expected_sql": "SELECT OwningTenantName, COUNT(*) AS QCOCount FROM Outages WHERE IsQCO = 1 GROUP BY OwningTenantName ORDER BY QCOCount DESC",
        "expected_output": "Top QCO services: Azure DNS (8), Azure Kubernetes Service (6), Azure SQL Database (4), Azure Load Balancer (3).",
    },
    {
        "input": "How many CritSits were filed in the last week?",
        "expected_sql": "SELECT COUNT(*) FROM SupportCases WHERE IsCritSit = 1 AND CreatedDateTime >= DATEADD(day, -7, GETDATE())",
        "expected_output": "CritSits filed last 7 days: 15 total. 5 Sev A, 10 Sev B. 3 still active.",
    },
    {
        "input": "What are the top 5 root cause categories for outages this quarter?",
        "expected_sql": "SELECT RootCauseCategory, COUNT(*) FROM Outages WHERE IsOutage = 1 GROUP BY RootCauseCategory ORDER BY COUNT(*) DESC",
        "expected_output": "Top RCA categories: 1. Configuration Change (28%), 2. Capacity Exhaustion (22%), 3. Software Bug (18%), 4. Dependency Failure (15%), 5. Network Issue (10%).",
    },
    {
        "input": "Show me the TTO trend for Sev 2 outages month over month",
        "expected_sql": "SELECT DATEPART(month, CreateDate), AVG(TTO) FROM Outages WHERE Severity = 2 AND IsOutage = 1 GROUP BY DATEPART(month, CreateDate)",
        "expected_output": "TTO trend Sev2: Jan 35min, Feb 38min, Mar 42min, Apr 45min. Worsening by ~3min/month.",
    },
    {
        "input": "List all active outages with more than 5 child incidents",
        "expected_sql": "SELECT IncidentId, Title, OwningTenantName, ChildCount FROM Outages WHERE Status = 'Active' AND ChildCount > 5",
        "expected_output": "Active multi-child outages: INC-2026-40009 (22 children, AKS), INC-2026-40004 (9 children, AKS), INC-2026-40008 (3 children, DNS).",
    },
    {
        "input": "What is the average mitigation time by service for QCS outages?",
        "expected_sql": "SELECT ServiceName, AVG(TTM) FROM Outages INNER JOIN QCServices ON Outages.ServiceOid = QCServices.ServiceOid WHERE IsOutage = 1 GROUP BY ServiceName",
        "expected_output": "Avg TTM by QCS service: Azure DNS 2.1h, Azure SQL DB 3.5h, AKS 4.8h, Azure Networking 5.2h.",
    },
    {
        "input": "How many outages were auto-detected vs manually declared this month?",
        "expected_sql": "SELECT IsAutoDetected, COUNT(*) FROM Outages WHERE IsOutage = 1 AND CreateDate >= DATEADD(month, -1, GETDATE()) GROUP BY IsAutoDetected",
        "expected_output": "Auto-detected: 35 (58%), Manually declared: 25 (42%). Auto-detection rate improving from 52% last month.",
    },
    {
        "input": "Show cascading outages in eastus in the last 60 days",
        "expected_sql": "SELECT IncidentId, Title, CascadingCategory FROM Outages WHERE Region = 'eastus' AND CascadingCategory IS NOT NULL AND CreateDate >= DATEADD(day, -60, GETDATE())",
        "expected_output": "Cascading outages in eastus: 4 events. 2 MCIO-level impact, 1 multi-service cascade, 1 regional cascade.",
    },
    {
        "input": "What is the TTN P75 for all outages this quarter?",
        "expected_sql": "SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY TTN) FROM Outages WHERE IsOutage = 1",
        "expected_output": "TTN P75 this quarter: 12 minutes. Target: 15 minutes. Meeting target with 3-minute margin.",
    },
    {
        "input": "Which teams own the most repeat outages (same service, same region)?",
        "expected_sql": "SELECT OwningTenantName, Region, COUNT(*) AS RepeatCount FROM Outages WHERE IsOutage = 1 GROUP BY OwningTenantName, Region HAVING COUNT(*) > 2",
        "expected_output": "Repeat outage teams: Azure DNS/eastus (5 repeats), AKS/southcentralus (4 repeats), Azure SQL/centralus (3 repeats).",
    },
    {
        "input": "Show the Bowler scorecard metrics for this month",
        "expected_sql": "SELECT MetricName, Value, Target FROM BowlerMetrics WHERE Month = DATEPART(month, GETDATE())",
        "expected_output": "Bowler scorecard: TTM P75=4.1h (target 4.0h, MISS), TTO P75=38min (target 45min, MEET), TTN P75=12min (target 15min, MEET), AutoDetect=58% (target 60%, MISS).",
    },
    {
        "input": "How many unique S500 customers had 2+ CritSits this month?",
        "expected_sql": "SELECT COUNT(DISTINCT CustomerName) FROM SupportCases WHERE IsCritSit = 1 AND IsS500 = 1 GROUP BY CustomerName HAVING COUNT(*) >= 2",
        "expected_output": "Unique S500 customers with 2+ CritSits: 8 customers. Fabrikam Inc leads with 4 CritSits.",
    },
    {
        "input": "What are the MSO (Multi-Service Outage) events this quarter?",
        "expected_sql": "SELECT EventId, ImpactedServices, ResponsibleService FROM MSO WHERE CreateDate >= DATEADD(quarter, -1, GETDATE())",
        "expected_output": "MSO events this quarter: 6 total. 2 involved Azure DNS cascading to AKS and SQL. 3 were networking-related.",
    },
    {
        "input": "Show the repair item completion rate by owning team",
        "expected_sql": "SELECT OwningTeam, COUNT(CASE WHEN Status = 'Completed' THEN 1 END) * 100.0 / COUNT(*) AS CompletionRate FROM RepairItems GROUP BY OwningTeam",
        "expected_output": "Repair item completion: Azure DNS 72%, AKS 85%, Azure SQL 91%, Azure Networking 68%. Azure Networking lowest.",
    },
    {
        "input": "What outages in the last 7 days had customer-visible impact?",
        "expected_sql": "SELECT IncidentId, Title, Severity, SRCount FROM Outages WHERE IsOutage = 1 AND SRCount > 0 AND CreateDate >= DATEADD(day, -7, GETDATE())",
        "expected_output": "Customer-visible outages last 7 days: 8 total with 145 linked SRs. Highest: INC-2026-40020 with 42 SRs.",
    },
    {
        "input": "Compare TTM between QCS and non-QCS services",
        "expected_sql": "SELECT IsQCS, AVG(TTM), PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY TTM) FROM Outages GROUP BY IsQCS",
        "expected_output": "TTM comparison: QCS avg 3.2h (P75 4.1h) vs non-QCS avg 5.1h (P75 6.8h). QCS significantly faster.",
    },
    {
        "input": "Show the incident timeline for INC-2026-40020",
        "expected_sql": "SELECT EventTimestamp, EventType, Description FROM IncidentTimeline WHERE IncidentId = 'INC-2026-40020' ORDER BY EventTimestamp",
        "expected_output": "INC-2026-40020 timeline: Impact start 06:40 → Detected 06:52 → Bridge opened 07:00 → DRI engaged 07:05 → Mitigated 10:30. Total TTM: 3h50m.",
    },
    {
        "input": "What is the outage rate per 10,000 subscriptions by service?",
        "expected_sql": "SELECT ServiceName, COUNT(*) * 10000.0 / TotalSubscriptions AS OutageRate FROM Outages GROUP BY ServiceName, TotalSubscriptions",
        "expected_output": "Outage rate per 10K subs: Azure DNS 2.1, AKS 1.8, Azure SQL 1.2, Azure Storage 0.5. DNS highest normalized rate.",
    },
]


VAL_TASKS: list[dict] = [
    {
        "input": "How many Sev 2 outages were there in westeurope last month?",
        "expected_sql": "SELECT COUNT(*) FROM Outages WHERE Severity = 2 AND Region = 'westeurope' AND CreateDate >= DATEADD(month, -1, GETDATE())",
        "expected_output": "Sev 2 outages in westeurope last month: 7 total. 5 mitigated, 2 resolved.",
    },
    {
        "input": "What is the TTIM for Azure Kubernetes Service outages?",
        "expected_sql": "SELECT AVG(TTIM), PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY TTIM) FROM Outages WHERE ServiceName = 'Azure Kubernetes Service'",
        "expected_output": "AKS TTIM: avg 2.8h, P75 3.5h. Improving from 4.1h P75 last quarter.",
    },
    {
        "input": "Show the outage count by day for the last 14 days",
        "expected_sql": "SELECT CAST(CreateDate AS DATE), COUNT(*) FROM Outages WHERE IsOutage = 1 AND CreateDate >= DATEADD(day, -14, GETDATE()) GROUP BY CAST(CreateDate AS DATE)",
        "expected_output": "Daily outage count: ranges from 2-8 per day. Peak on April 15 (8 outages) during DNS incident.",
    },
    {
        "input": "Which services have improving TTM trends?",
        "expected_sql": "SELECT ServiceName, AVG(TTM) AS CurrentTTM FROM Outages WHERE IsOutage = 1 GROUP BY ServiceName HAVING AVG(TTM) < LAG(AVG(TTM))",
        "expected_output": "Improving TTM: Azure Storage (-15%), Azure App Service (-8%), Azure Functions (-12%). All 3 below target.",
    },
    {
        "input": "How many outages were related to configuration changes?",
        "expected_sql": "SELECT COUNT(*) FROM Outages WHERE RootCauseCategory = 'Configuration Change' AND IsOutage = 1",
        "expected_output": "Configuration change outages: 28 this quarter (28% of total). Highest contributor to outages.",
    },
    {
        "input": "List the Sev 1 outages that took more than 6 hours to mitigate",
        "expected_sql": "SELECT IncidentId, Title, TTM FROM Outages WHERE Severity = 1 AND TTM > 6.0 AND IsOutage = 1",
        "expected_output": "Long-running Sev1 outages: 3 incidents. INC-2026-40001 (8.5h, DNS), INC-2026-40009 (7.2h, AKS), INC-2026-40015 (6.3h, SQL).",
    },
    {
        "input": "Show support request volume linked to outages by severity",
        "expected_sql": "SELECT Severity, SUM(SRCount) FROM Outages WHERE IsOutage = 1 AND SRCount > 0 GROUP BY Severity",
        "expected_output": "SR volume by severity: Sev1 = 420 SRs, Sev2 = 280 SRs, Sev3 = 95 SRs. Sev1 accounts for 53% of outage-linked SRs.",
    },
    {
        "input": "What is the outage frequency for Azure DNS by month?",
        "expected_sql": "SELECT DATEPART(month, CreateDate), COUNT(*) FROM Outages WHERE ServiceName = 'Azure DNS' AND IsOutage = 1 GROUP BY DATEPART(month, CreateDate)",
        "expected_output": "Azure DNS outage frequency: Jan 4, Feb 6, Mar 5, Apr 8. April spike driven by 3 multi-region incidents.",
    },
    {
        "input": "Show me the root responsible incident distribution",
        "expected_sql": "SELECT IsRootResponsible, COUNT(*) FROM Outages WHERE IsOutage = 1 GROUP BY IsRootResponsible",
        "expected_output": "Root responsible: 65 root incidents, 48 child incidents. 58% of outages are root-responsible.",
    },
    {
        "input": "What are the QCO outages for centralus this quarter?",
        "expected_sql": "SELECT IncidentId, Title, OwningTenantName, CreateDate FROM Outages WHERE IsQCO = 1 AND Region = 'centralus'",
        "expected_output": "QCO outages centralus: 5 total. AKS (2), Azure DNS (1), Azure SQL (1), Azure Networking (1).",
    },
    {
        "input": "How does the current month compare to the same month last year for outage count?",
        "expected_sql": "SELECT YEAR(CreateDate), COUNT(*) FROM Outages WHERE MONTH(CreateDate) = MONTH(GETDATE()) AND IsOutage = 1 GROUP BY YEAR(CreateDate)",
        "expected_output": "YoY comparison: Apr 2025 = 42 outages, Apr 2026 = 55 outages. 31% increase year-over-year.",
    },
    {
        "input": "Which customers were affected by the most outages?",
        "expected_sql": "SELECT CustomerName, COUNT(DISTINCT IncidentId) FROM OutageCustomerImpact GROUP BY CustomerName ORDER BY COUNT(*) DESC",
        "expected_output": "Most affected customers: Fabrikam Inc (12 outages), Woodgrove Bank (9), Adventure Works (8), Tailspin Toys (7).",
    },
]
