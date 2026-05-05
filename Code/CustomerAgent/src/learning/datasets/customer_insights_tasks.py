"""Training and validation datasets for customer insights prompt optimization.

Each task has:
  - input: natural language query about customer impact
  - expected_sql: reference T-SQL query or key fragments
  - expected_output: reference analytical output
"""
from __future__ import annotations


TRAIN_TASKS: list[dict] = [
    {
        "input": "How many CritSits has Fabrikam Inc had this quarter?",
        "expected_sql": "SELECT COUNT(*) FROM SupportCases WHERE CustomerName = 'Fabrikam Inc' AND IsCritSit = 1 AND CreatedDateTime >= DATEADD(quarter, -1, GETDATE())",
        "expected_output": "Fabrikam Inc CritSits this quarter: 4 total. 2 Sev A (Azure SQL, Cosmos DB), 2 Sev B (AKS, DNS). 1 still active.",
    },
    {
        "input": "What is Woodgrove Bank's AIRO across all services?",
        "expected_sql": "SELECT ServiceName, AIROScore FROM AIRO WHERE CustomerName = 'Woodgrove Bank'",
        "expected_output": "Woodgrove Bank AIRO: Azure SQL DB 96.8%, Cosmos DB 99.1%, AKS 98.2%, Azure Storage 99.5%. SQL DB is concerning.",
    },
    {
        "input": "Show the QEI list for S500 customers this month",
        "expected_sql": "SELECT CustomerName, QEIScore, ImpactedServices FROM QEI WHERE IsS500 = 1 AND Month = DATEPART(month, GETDATE()) ORDER BY QEIScore ASC",
        "expected_output": "S500 QEI list: 12 customers below threshold. Worst: Datum Corp (72.1), Fabrikam Inc (78.3), Woodgrove Bank (81.5).",
    },
    {
        "input": "Which customers have escalation patterns on Azure SQL Database?",
        "expected_sql": "SELECT CustomerName, COUNT(*) AS EscalationCount FROM SupportCases WHERE SupportProductName = 'Azure SQL Database' AND IsEscalated = 1 GROUP BY CustomerName HAVING COUNT(*) > 1",
        "expected_output": "SQL DB escalation patterns: Fabrikam Inc (5 escalations in 90 days), Datum Corp (3), Adventure Works (2). Fabrikam is a repeat escalator.",
    },
    {
        "input": "Give me the support case correlation with outages for Adventure Works",
        "expected_sql": "SELECT o.IncidentId, o.Title, COUNT(s.CaseNumber) AS LinkedSRs FROM Outages o JOIN SupportCases s ON o.IncidentId = s.LinkedIncidentId WHERE s.CustomerName = 'Adventure Works' GROUP BY o.IncidentId, o.Title",
        "expected_output": "Adventure Works outage-SR correlation: 8 outages linked to 23 SRs. Highest: INC-2026-41006 (7 SRs). 65% of SRs filed during active outages.",
    },
    {
        "input": "What is the CritSit distribution by customer and service?",
        "expected_sql": "SELECT CustomerName, SupportProductName, COUNT(*) FROM SupportCases WHERE IsCritSit = 1 GROUP BY CustomerName, SupportProductName",
        "expected_output": "CritSit heatmap: Fabrikam/SQL (3), Woodgrove/Cosmos (2), Adventure Works/AKS (2), Datum/DNS (2). SQL and Cosmos drive most CritSits.",
    },
    {
        "input": "Show me the QEIS score for all S500 customers sorted by risk",
        "expected_sql": "SELECT CustomerName, QEISScore, RiskCategory FROM QEIS WHERE IsS500 = 1 ORDER BY QEISScore ASC",
        "expected_output": "S500 QEIS ranking: 5 High-Risk (score <75), 8 Medium-Risk (75-85), 15 Low-Risk (>85). Datum Corp worst at 68.2.",
    },
    {
        "input": "How many support cases did Tailspin Toys file this month?",
        "expected_sql": "SELECT COUNT(*) FROM SupportCases WHERE CustomerName = 'Tailspin Toys' AND CreatedDateTime >= DATEADD(month, -1, GETDATE())",
        "expected_output": "Tailspin Toys SRs this month: 18 total. 3 Sev A, 5 Sev B, 10 Sev C. 2 CritSits among them.",
    },
    {
        "input": "Which customers were most impacted by the DNS outages in April?",
        "expected_sql": "SELECT CustomerName, COUNT(DISTINCT IncidentId) AS OutageCount, SUM(SRCount) FROM Outages WHERE OwningTenantName = 'Azure DNS' AND CreateDate >= '2026-04-01' GROUP BY CustomerName ORDER BY OutageCount DESC",
        "expected_output": "DNS-impacted customers Apr 2026: Alpine Ski House (4 outages, 12 SRs), Tailspin Toys (3 outages, 8 SRs), Fabrikam Inc (3 outages, 15 SRs).",
    },
    {
        "input": "What is the average SR resolution time for Datum Corp?",
        "expected_sql": "SELECT AVG(DATEDIFF(hour, CreatedDateTime, ResolvedDateTime)) FROM SupportCases WHERE CustomerName = 'Datum Corp' AND State = 'Closed'",
        "expected_output": "Datum Corp avg SR resolution: 42 hours. Sev A: 8h, Sev B: 28h, Sev C: 65h. Below fleet averages for Sev A and B.",
    },
    {
        "input": "Show the customer impact summary for the INC-2026-40009 outage",
        "expected_sql": "SELECT CustomerName, SubscriptionCount, SRCount, IsCritSit FROM OutageCustomerImpact WHERE IncidentId = 'INC-2026-40009'",
        "expected_output": "INC-2026-40009 impact: 5 customers, 12 subscriptions, 28 SRs, 2 CritSits. Alpine Ski House and Woodgrove Bank most affected.",
    },
    {
        "input": "Which customers have the most open support cases right now?",
        "expected_sql": "SELECT CustomerName, COUNT(*) FROM SupportCases WHERE State = 'Active' GROUP BY CustomerName ORDER BY COUNT(*) DESC",
        "expected_output": "Open cases: Fabrikam Inc (12), Woodgrove Bank (8), Adventure Works (6), Tailspin Toys (5), Datum Corp (4).",
    },
    {
        "input": "What is the customer sentiment trend for Alpine Ski House?",
        "expected_sql": "SELECT Month, SentimentScore, SRCount, CritSitCount FROM CustomerSentiment WHERE CustomerName = 'Alpine Ski House' ORDER BY Month",
        "expected_output": "Alpine Ski House sentiment: Jan 82, Feb 78, Mar 71, Apr 65. Declining steadily — correlates with 3 DNS outages in Q2.",
    },
    {
        "input": "Show repeat escalations across all S500 customers this quarter",
        "expected_sql": "SELECT CustomerName, COUNT(*) AS RepeatEscalations FROM SupportCases WHERE IsS500 = 1 AND IsEscalated = 1 GROUP BY CustomerName HAVING COUNT(*) >= 2",
        "expected_output": "Repeat escalators: Fabrikam Inc (7), Datum Corp (4), Woodgrove Bank (3), Adventure Works (2). Fabrikam needs proactive engagement.",
    },
    {
        "input": "How does Contoso Ltd compare to fleet average on support metrics?",
        "expected_sql": "SELECT Metric, CustomerValue, FleetAvg FROM CustomerBenchmark WHERE CustomerName = 'Contoso Ltd'",
        "expected_output": "Contoso vs fleet: SR volume 15 vs avg 10, CritSit rate 8% vs avg 5%, AIRO 97.8% vs avg 98.7%. Above average on negative metrics.",
    },
    {
        "input": "What services generate the most CritSits across all customers?",
        "expected_sql": "SELECT SupportProductName, COUNT(*) FROM SupportCases WHERE IsCritSit = 1 GROUP BY SupportProductName ORDER BY COUNT(*) DESC",
        "expected_output": "CritSit by service: Azure SQL Database (15), Azure Cosmos DB (12), AKS (8), Azure DNS (6). SQL and Cosmos account for 55%.",
    },
    {
        "input": "Show the outage impact timeline for Fabrikam Inc across all services",
        "expected_sql": "SELECT o.CreateDate, o.Title, o.ServiceName, COUNT(s.CaseNumber) FROM Outages o JOIN SupportCases s ON o.IncidentId = s.LinkedIncidentId WHERE s.CustomerName = 'Fabrikam Inc' GROUP BY o.CreateDate, o.Title, o.ServiceName ORDER BY o.CreateDate",
        "expected_output": "Fabrikam outage timeline: 12 outages over 90 days. Peak in mid-April (4 outages in 1 week). Heaviest on SQL DB and DNS.",
    },
    {
        "input": "What is the customer churn risk for our top accounts based on incident data?",
        "expected_sql": "SELECT CustomerName, ChurnRiskScore, CritSitCount, OutageCount, AIROScore FROM CustomerRisk WHERE IsS500 = 1 ORDER BY ChurnRiskScore DESC",
        "expected_output": "Churn risk: HIGH — Datum Corp (score 82), Fabrikam Inc (75). MEDIUM — Woodgrove Bank (58), Alpine Ski House (55). LOW — remainder.",
    },
    {
        "input": "How many unique customers have been affected by outages this month?",
        "expected_sql": "SELECT COUNT(DISTINCT CustomerName) FROM OutageCustomerImpact WHERE CreateDate >= DATEADD(month, -1, GETDATE())",
        "expected_output": "Unique customers impacted by outages this month: 28. Up from 22 last month. 8 are S500 accounts.",
    },
    {
        "input": "Show Tailspin Toys' SLI breach history by region",
        "expected_sql": "SELECT Region, SLO_SliId, COUNT(*) AS BreachCount, AVG(TotalImpactDurationMin) FROM SLIBreaches WHERE CustomerName = 'Tailspin Toys' GROUP BY Region, SLO_SliId",
        "expected_output": "Tailspin SLI breaches: centralus (12 breaches, avg 105min), eastus (8 breaches, avg 76min), southcentralus (5 breaches, avg 200min).",
    },
    {
        "input": "What percentage of our S500 customers had a CritSit this quarter?",
        "expected_sql": "SELECT COUNT(DISTINCT CASE WHEN IsCritSit = 1 THEN CustomerName END) * 100.0 / COUNT(DISTINCT CustomerName) FROM SupportCases WHERE IsS500 = 1",
        "expected_output": "S500 CritSit rate: 35% (18 of 52 S500 customers had at least one CritSit this quarter). Up from 28% last quarter.",
    },
]


VAL_TASKS: list[dict] = [
    {
        "input": "How many support cases does Woodgrove Bank have on Azure Cosmos DB?",
        "expected_sql": "SELECT COUNT(*) FROM SupportCases WHERE CustomerName = 'Woodgrove Bank' AND SupportProductName = 'Azure Cosmos DB'",
        "expected_output": "Woodgrove Bank Cosmos DB cases: 6 total this quarter. 2 CritSits, 4 standard. Avg resolution 18 hours.",
    },
    {
        "input": "Show the QEI trend for Datum Corp over the last 6 months",
        "expected_sql": "SELECT Month, QEIScore FROM QEI WHERE CustomerName = 'Datum Corp' ORDER BY Month",
        "expected_output": "Datum Corp QEI trend: Nov 85, Dec 82, Jan 79, Feb 76, Mar 74, Apr 72. Steady decline — action required.",
    },
    {
        "input": "Which S500 customers have active CritSits right now?",
        "expected_sql": "SELECT CustomerName, CaseNumber, SupportProductName, Severity FROM SupportCases WHERE IsS500 = 1 AND IsCritSit = 1 AND State = 'Active'",
        "expected_output": "Active S500 CritSits: Fabrikam Inc (SR2183071212, Cosmos DB, Sev A), Adventure Works (SR2189157350, SQL DB, Sev A).",
    },
    {
        "input": "What services cause the most impact to Adventure Works?",
        "expected_sql": "SELECT SupportProductName, COUNT(*) AS CaseCount, COUNT(CASE WHEN IsEscalated = 1 THEN 1 END) AS Escalations FROM SupportCases WHERE CustomerName = 'Adventure Works' GROUP BY SupportProductName ORDER BY CaseCount DESC",
        "expected_output": "Adventure Works top impacted: Azure SQL DB (8 cases, 3 escalations), AKS (5 cases, 1 escalation), Cosmos DB (4 cases, 2 escalations).",
    },
    {
        "input": "Give me the customer impact summary for all Sev 1 outages this month",
        "expected_sql": "SELECT IncidentId, COUNT(DISTINCT CustomerName) AS Customers, SUM(SRCount) AS TotalSRs FROM OutageCustomerImpact WHERE Severity = 1 AND CreateDate >= DATEADD(month, -1, GETDATE()) GROUP BY IncidentId",
        "expected_output": "Sev1 customer impact: 5 incidents affected 35 unique customers with 180 SRs. Avg 7 customers per Sev1 outage.",
    },
    {
        "input": "How does Alpine Ski House's SR volume compare to last quarter?",
        "expected_sql": "SELECT DATEPART(quarter, CreatedDateTime), COUNT(*) FROM SupportCases WHERE CustomerName = 'Alpine Ski House' GROUP BY DATEPART(quarter, CreatedDateTime)",
        "expected_output": "Alpine Ski House SR volume: Q1 = 12, Q2 = 22 (current). 83% increase. Driven by DNS outages in australiaeast.",
    },
    {
        "input": "Show me customer subscription concentration risk",
        "expected_sql": "SELECT CustomerName, Region, COUNT(DISTINCT SubscriptionId) AS SubCount FROM CustomerSubscriptions GROUP BY CustomerName, Region ORDER BY SubCount DESC",
        "expected_output": "Concentration risk: Fabrikam Inc has 45 subscriptions in eastus (single region). Woodgrove Bank has 30 in eastus2. Both high concentration risk.",
    },
    {
        "input": "What is the mean time to first response for Tailspin Toys CritSits?",
        "expected_sql": "SELECT AVG(DATEDIFF(minute, CreatedDateTime, FirstResponseDateTime)) FROM SupportCases WHERE CustomerName = 'Tailspin Toys' AND IsCritSit = 1",
        "expected_output": "Tailspin Toys CritSit MTFR: 22 minutes average. Best: 8 min (Sev A), Worst: 45 min (Sev B). All within SLA.",
    },
    {
        "input": "List customers who filed SRs during the DNS outage on April 15",
        "expected_sql": "SELECT DISTINCT CustomerName, COUNT(*) FROM SupportCases WHERE LinkedIncidentId IN (SELECT IncidentId FROM Outages WHERE OwningTenantName = 'Azure DNS' AND CAST(CreateDate AS DATE) = '2026-04-15') GROUP BY CustomerName",
        "expected_output": "DNS outage Apr 15 SRs: Fabrikam Inc (8), Woodgrove Bank (5), Tailspin Toys (4), Alpine Ski House (3), Datum Corp (2).",
    },
    {
        "input": "What is the customer-level AIRO for our top 5 accounts?",
        "expected_sql": "SELECT CustomerName, AVG(AIROScore) FROM AIRO WHERE CustomerName IN ('Fabrikam Inc', 'Woodgrove Bank', 'Adventure Works', 'Tailspin Toys', 'Alpine Ski House') GROUP BY CustomerName",
        "expected_output": "Top 5 customer AIRO: Tailspin Toys 98.9%, Adventure Works 98.1%, Fabrikam Inc 97.5%, Woodgrove Bank 97.2%, Alpine Ski House 96.8%.",
    },
    {
        "input": "Show me repeat issue patterns for Contoso Ltd",
        "expected_sql": "SELECT SupportProductName, ProblemCategory, COUNT(*) FROM SupportCases WHERE CustomerName = 'Contoso Ltd' GROUP BY SupportProductName, ProblemCategory HAVING COUNT(*) > 1",
        "expected_output": "Contoso repeat issues: Azure SQL DB/connectivity (4 occurrences), AKS/pod scheduling (3), Azure DNS/resolution failures (2).",
    },
    {
        "input": "How many customer impacting outages have we had per month this year?",
        "expected_sql": "SELECT DATEPART(month, CreateDate), COUNT(DISTINCT IncidentId), COUNT(DISTINCT CustomerName) FROM OutageCustomerImpact WHERE YEAR(CreateDate) = 2026 GROUP BY DATEPART(month, CreateDate)",
        "expected_output": "Customer-impacting outages 2026: Jan 8 (18 customers), Feb 10 (22), Mar 12 (25), Apr 15 (28). Increasing trend.",
    },
]
