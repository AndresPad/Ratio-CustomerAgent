"""Training and validation datasets for AIRO analyst prompt optimization.

Each task has:
  - input: natural language query about AIRO metrics
  - expected_sql: reference T-SQL query or key fragments
  - expected_output: reference analytical output
"""
from __future__ import annotations


TRAIN_TASKS: list[dict] = [
    {
        "input": "What is the current AIRO score for Azure SQL Database across all regions?",
        "expected_sql": "SELECT ServiceName, Region, AIROScore FROM AIRO WHERE ServiceName = 'Azure SQL Database'",
        "expected_output": "AIRO for Azure SQL Database: fleet average 98.2% across 12 regions. Lowest region: southcentralus at 96.1%.",
    },
    {
        "input": "Show me AIRO trends for Cosmos DB in eastus over the last 90 days",
        "expected_sql": "SELECT Date, AIROScore FROM AIRO WHERE ServiceName = 'Azure Cosmos DB' AND Region = 'eastus' ORDER BY Date",
        "expected_output": "AIRO trend for Azure Cosmos DB in eastus: 99.1% → 98.5% → 97.8% over last 90 days, declining 0.13% per month.",
    },
    {
        "input": "Which services have AIRO below 99% this month?",
        "expected_sql": "SELECT ServiceName, AVG(AIROScore) AS AvgAIRO FROM AIRO WHERE AIROScore < 99 GROUP BY ServiceName",
        "expected_output": "Services below 99% AIRO: Azure DNS (98.2%), Azure Load Balancer (97.9%), Azure Kubernetes Service (98.5%).",
    },
    {
        "input": "Compare AIRO between eastus and westeurope for Azure Networking",
        "expected_sql": "SELECT Region, AIROScore FROM AIRO WHERE ServiceName = 'Azure Networking' AND Region IN ('eastus', 'westeurope')",
        "expected_output": "AIRO comparison: Azure Networking eastus=99.1%, westeurope=98.7%. Eastus outperforms by 0.4pp.",
    },
    {
        "input": "What is the fleet-wide AIRO for all QCS services?",
        "expected_sql": "SELECT ServiceName, AVG(AIROScore) FROM AIRO INNER JOIN QCServices ON AIRO.ServiceOid = QCServices.ServiceOid GROUP BY ServiceName",
        "expected_output": "Fleet-wide QCS AIRO: 98.9% average across 45 quality-critical services. 3 services below 99% target.",
    },
    {
        "input": "Show AIRO by division for the last quarter",
        "expected_sql": "SELECT Division, AVG(AIROScore) FROM AIRO WHERE Date >= DATEADD(quarter, -1, GETDATE()) GROUP BY Division",
        "expected_output": "AIRO by division: Cloud+AI 99.1%, Azure 98.7%, M365 99.3%. Azure division lowest with 3 services below target.",
    },
    {
        "input": "What are the top 5 regions with worst AIRO?",
        "expected_sql": "SELECT TOP 5 Region, AVG(AIROScore) AS AvgAIRO FROM AIRO GROUP BY Region ORDER BY AvgAIRO ASC",
        "expected_output": "Worst AIRO regions: 1. australiaeast (97.5%), 2. japaneast (97.8%), 3. southcentralus (98.0%), 4. centralus (98.2%), 5. westus2 (98.3%).",
    },
    {
        "input": "How has Azure App Service AIRO changed month over month?",
        "expected_sql": "SELECT DATEPART(month, Date) AS Month, AVG(AIROScore) FROM AIRO WHERE ServiceName = 'Azure App Service' GROUP BY DATEPART(month, Date)",
        "expected_output": "Azure App Service AIRO MoM: Jan 99.2%, Feb 99.0%, Mar 98.8%, Apr 98.5%. Declining trend of -0.23% per month.",
    },
    {
        "input": "Give me the AIRO breakdown for Fabrikam Inc by service",
        "expected_sql": "SELECT ServiceName, AIROScore FROM AIRO WHERE CustomerName = 'Fabrikam Inc' GROUP BY ServiceName",
        "expected_output": "Fabrikam Inc AIRO by service: Azure SQL DB 98.5%, Azure Cosmos DB 99.1%, AKS 97.8%, Azure Storage 99.4%.",
    },
    {
        "input": "What services in eastus2 are dragging AIRO below target?",
        "expected_sql": "SELECT ServiceName, AIROScore FROM AIRO WHERE Region = 'eastus2' AND AIROScore < 99.0 ORDER BY AIROScore ASC",
        "expected_output": "Services below AIRO target in eastus2: Azure DNS (96.5%), Azure Kubernetes Service (97.2%), Azure Load Balancer (98.1%).",
    },
    {
        "input": "Show the AIRO heatmap data for all regions and top services",
        "expected_sql": "SELECT ServiceName, Region, AVG(AIROScore) FROM AIRO GROUP BY ServiceName, Region",
        "expected_output": "AIRO heatmap: 12 services × 8 regions. Hotspots: Azure DNS/australiaeast (95.2%), AKS/southcentralus (96.8%).",
    },
    {
        "input": "What is the AIRO delta between prod and PPE environments?",
        "expected_sql": "SELECT Environment, AVG(AIROScore) FROM AIRO GROUP BY Environment",
        "expected_output": "AIRO delta: PROD 98.7%, PPE 99.5%. PPE outperforms by 0.8pp (expected — lower traffic, fewer edge cases).",
    },
    {
        "input": "List the services whose AIRO degraded more than 1% this quarter",
        "expected_sql": "SELECT ServiceName, AIROScore, LAG(AIROScore) OVER (ORDER BY Date) AS PrevAIRO FROM AIRO WHERE AIRODelta < -1.0",
        "expected_output": "Services with >1% AIRO degradation: Azure DNS (-2.1%), Azure Load Balancer (-1.5%), Azure Kubernetes Service (-1.2%).",
    },
    {
        "input": "What is Woodgrove Bank's worst AIRO service?",
        "expected_sql": "SELECT TOP 1 ServiceName, AIROScore FROM AIRO WHERE CustomerName = 'Woodgrove Bank' ORDER BY AIROScore ASC",
        "expected_output": "Woodgrove Bank worst AIRO: Azure SQL Database at 96.8% — driven by 3 Sev2 outages in eastus2 this month.",
    },
    {
        "input": "Show AIRO correlation with outage count per service",
        "expected_sql": "SELECT a.ServiceName, AVG(a.AIROScore), COUNT(o.IncidentId) FROM AIRO a LEFT JOIN Outages o ON a.ServiceOid = o.ServiceOid GROUP BY a.ServiceName",
        "expected_output": "AIRO vs outage count: Strong negative correlation (r=-0.82). Services with 5+ outages average 97.1% AIRO vs 99.3% for 0-outage services.",
    },
    {
        "input": "What is the overall AIRO trend across Azure?",
        "expected_sql": "SELECT Date, AVG(AIROScore) FROM AIRO GROUP BY Date ORDER BY Date",
        "expected_output": "Overall Azure AIRO trend: 99.1% in Jan → 98.7% in Apr. Steady decline of 0.1pp/month, driven by DNS and networking services.",
    },
    {
        "input": "Break down AIRO for centralus by QCS vs non-QCS services",
        "expected_sql": "SELECT IsQCS, AVG(AIROScore) FROM AIRO WHERE Region = 'centralus' GROUP BY IsQCS",
        "expected_output": "Central US AIRO: QCS services 98.9%, non-QCS 97.5%. QCS are better instrumented and maintained.",
    },
    {
        "input": "Give me weekly AIRO for Azure Storage in japaneast",
        "expected_sql": "SELECT DATEPART(week, Date) AS Week, AVG(AIROScore) FROM AIRO WHERE ServiceName = 'Azure Storage' AND Region = 'japaneast' GROUP BY DATEPART(week, Date)",
        "expected_output": "Azure Storage japaneast weekly AIRO: W1 99.5%, W2 99.3%, W3 98.9%, W4 99.1%. Dip in W3 correlates with planned maintenance.",
    },
    {
        "input": "Which customer has the lowest average AIRO across all services?",
        "expected_sql": "SELECT CustomerName, AVG(AIROScore) AS AvgAIRO FROM AIRO GROUP BY CustomerName ORDER BY AvgAIRO ASC",
        "expected_output": "Lowest AIRO customer: Datum Corp at 97.2% average — impacted by persistent Azure DNS issues in japaneast.",
    },
    {
        "input": "Show me the AIRO percentile distribution across all services",
        "expected_sql": "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY AIROScore) AS P50, PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY AIROScore) AS P75 FROM AIRO",
        "expected_output": "AIRO distribution: P25=97.8%, P50=98.9%, P75=99.4%, P95=99.8%. Long tail below P25 driven by 5 chronically impacted services.",
    },
    {
        "input": "Compare this month's AIRO to same month last year for Azure SQL",
        "expected_sql": "SELECT YEAR(Date) AS Yr, AVG(AIROScore) FROM AIRO WHERE ServiceName = 'Azure SQL Database' AND MONTH(Date) = MONTH(GETDATE()) GROUP BY YEAR(Date)",
        "expected_output": "Azure SQL AIRO YoY comparison: Apr 2025 = 99.1%, Apr 2026 = 98.5%. Year-over-year decline of 0.6pp.",
    },
]


VAL_TASKS: list[dict] = [
    {
        "input": "What is the AIRO for Azure Virtual Machines fleet-wide?",
        "expected_sql": "SELECT AVG(AIROScore) FROM AIRO WHERE ServiceName = 'Azure Virtual Machines'",
        "expected_output": "Azure Virtual Machines fleet-wide AIRO: 99.0%. Above 99% target.",
    },
    {
        "input": "Show me AIRO trends for Azure DNS in southcentralus over the last 60 days",
        "expected_sql": "SELECT Date, AIROScore FROM AIRO WHERE ServiceName = 'Azure DNS' AND Region = 'southcentralus' ORDER BY Date",
        "expected_output": "Azure DNS southcentralus AIRO trend: declining from 98.5% to 96.1% over 60 days.",
    },
    {
        "input": "Which regions had AIRO improvement this quarter?",
        "expected_sql": "SELECT Region, AIRODelta FROM AIRO WHERE AIRODelta > 0 GROUP BY Region",
        "expected_output": "Regions with AIRO improvement: westus (+0.3%), centralus (+0.1%), japaneast (+0.5%).",
    },
    {
        "input": "What is the AIRO breakdown by severity for outage-linked services?",
        "expected_sql": "SELECT Severity, AVG(AIROScore) FROM AIRO INNER JOIN Outages ON AIRO.ServiceOid = Outages.ServiceOid GROUP BY Severity",
        "expected_output": "AIRO by outage severity: Sev1 services avg 96.5%, Sev2 avg 97.8%, Sev3 avg 98.9%.",
    },
    {
        "input": "Show Tailspin Toys AIRO by region",
        "expected_sql": "SELECT Region, AVG(AIROScore) FROM AIRO WHERE CustomerName = 'Tailspin Toys' GROUP BY Region",
        "expected_output": "Tailspin Toys AIRO: eastus 98.5%, centralus 99.1%, australiaeast 97.2%.",
    },
    {
        "input": "What services have the highest AIRO volatility?",
        "expected_sql": "SELECT ServiceName, STDEV(AIROScore) AS Volatility FROM AIRO GROUP BY ServiceName ORDER BY Volatility DESC",
        "expected_output": "Highest AIRO volatility: Azure DNS (σ=1.8%), Azure Load Balancer (σ=1.5%), AKS (σ=1.2%).",
    },
    {
        "input": "Compare AIRO for Azure App Service across all divisions",
        "expected_sql": "SELECT Division, AVG(AIROScore) FROM AIRO WHERE ServiceName = 'Azure App Service' GROUP BY Division",
        "expected_output": "Azure App Service AIRO by division: Cloud+AI 99.0%, Azure 98.5%, M365 99.2%.",
    },
    {
        "input": "Show me the AIRO recovery time after Sev1 outages",
        "expected_sql": "SELECT ServiceName, AVG(DATEDIFF(hour, MitigationDate, AIRORecoveryDate)) FROM AIRO JOIN Outages ON AIRO.ServiceOid = Outages.ServiceOid WHERE Severity = 1",
        "expected_output": "AIRO recovery after Sev1: avg 18 hours to return to pre-outage baseline. Longest: Azure DNS at 72 hours.",
    },
    {
        "input": "What is the AIRO for Azure Event Hubs in westus?",
        "expected_sql": "SELECT AIROScore FROM AIRO WHERE ServiceName = 'Azure Event Hubs' AND Region = 'westus'",
        "expected_output": "Azure Event Hubs westus AIRO: 99.4%. Well above 99% target.",
    },
    {
        "input": "How does Adventure Works AIRO compare to fleet average?",
        "expected_sql": "SELECT CustomerName, AVG(AIROScore) FROM AIRO WHERE CustomerName IN ('Adventure Works', 'FleetAvg') GROUP BY CustomerName",
        "expected_output": "Adventure Works AIRO: 98.1% vs fleet average 98.7%. Below fleet by 0.6pp.",
    },
    {
        "input": "Show the worst AIRO day in the last 30 days across all services",
        "expected_sql": "SELECT TOP 1 Date, AVG(AIROScore) AS DailyAIRO FROM AIRO GROUP BY Date ORDER BY DailyAIRO ASC",
        "expected_output": "Worst AIRO day: April 15, 2026 at 96.2% fleet-wide — coincided with multi-region DNS incident.",
    },
    {
        "input": "Give me AIRO for all Tier0 services in eastus2",
        "expected_sql": "SELECT ServiceName, AIROScore FROM AIRO WHERE Tier = 0 AND Region = 'eastus2'",
        "expected_output": "Tier0 AIRO in eastus2: Azure SQL DB 98.5%, Azure Storage 99.2%, Azure Networking 98.0%, Cosmos DB 99.3%.",
    },
]
