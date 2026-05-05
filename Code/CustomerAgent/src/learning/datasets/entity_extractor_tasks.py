"""Training and validation datasets for entity extractor prompt optimization.

Each task has:
  - input: user query containing entity mentions
  - expected_entities: dict with normalized entities
  - expected_output: reference output text
"""
from __future__ import annotations


TRAIN_TASKS: list[dict] = [
    {
        "input": "What outages hit Azure SQL Database in East US last week?",
        "expected_entities": {
            "services": ["Azure SQL Database"],
            "regions": ["eastus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure SQL Database, Region=eastus",
    },
    {
        "input": "Show me CritSits for Fabrikam Inc on Cosmos DB in westeurope and eastus2",
        "expected_entities": {
            "services": ["Azure Cosmos DB"],
            "regions": ["westeurope", "eastus2"],
            "customers": ["Fabrikam Inc"],
        },
        "expected_output": "Entities: Service=Azure Cosmos DB, Region=westeurope, eastus2, Customer=Fabrikam Inc",
    },
    {
        "input": "How many Sev 1 incidents affected Woodgrove Bank's AKS clusters in southcentralus?",
        "expected_entities": {
            "services": ["Azure Kubernetes Service"],
            "regions": ["southcentralus"],
            "customers": ["Woodgrove Bank"],
        },
        "expected_output": "Entities: Service=Azure Kubernetes Service, Region=southcentralus, Customer=Woodgrove Bank",
    },
    {
        "input": "Get AIRO scores for SQL DB and Storage across all regions",
        "expected_entities": {
            "services": ["Azure SQL Database", "Azure Storage"],
            "regions": [],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure SQL Database, Azure Storage",
    },
    {
        "input": "Tailspin Toys has been complaining about DNS issues in australiaeast",
        "expected_entities": {
            "services": ["Azure DNS"],
            "regions": ["australiaeast"],
            "customers": ["Tailspin Toys"],
        },
        "expected_output": "Entities: Service=Azure DNS, Region=australiaeast, Customer=Tailspin Toys",
    },
    {
        "input": "What is the TTM for Azure App Service outages in centralus and westus2?",
        "expected_entities": {
            "services": ["Azure App Service"],
            "regions": ["centralus", "westus2"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure App Service, Region=centralus, westus2",
    },
    {
        "input": "Adventure Works escalated a case on CosmosDB in Japan East",
        "expected_entities": {
            "services": ["Azure Cosmos DB"],
            "regions": ["japaneast"],
            "customers": ["Adventure Works"],
        },
        "expected_output": "Entities: Service=Azure Cosmos DB, Region=japaneast, Customer=Adventure Works",
    },
    {
        "input": "Show me all outages for Azure Kubernetes Service",
        "expected_entities": {
            "services": ["Azure Kubernetes Service"],
            "regions": [],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Kubernetes Service",
    },
    {
        "input": "Any impact on Alpine Ski House from the Load Balancer issue in west europe?",
        "expected_entities": {
            "services": ["Azure Load Balancer"],
            "regions": ["westeurope"],
            "customers": ["Alpine Ski House"],
        },
        "expected_output": "Entities: Service=Azure Load Balancer, Region=westeurope, Customer=Alpine Ski House",
    },
    {
        "input": "Datum Corp reported problems with their SQL databases in EastUS and EastUS2",
        "expected_entities": {
            "services": ["Azure SQL Database"],
            "regions": ["eastus", "eastus2"],
            "customers": ["Datum Corp"],
        },
        "expected_output": "Entities: Service=Azure SQL Database, Region=eastus, eastus2, Customer=Datum Corp",
    },
    {
        "input": "What's the AIRO for Azure Networking in scus?",
        "expected_entities": {
            "services": ["Azure Networking"],
            "regions": ["southcentralus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Networking, Region=southcentralus",
    },
    {
        "input": "Check if Contoso Ltd is affected by the VM allocation failures in wus2",
        "expected_entities": {
            "services": ["Azure Virtual Machines"],
            "regions": ["westus2"],
            "customers": ["Contoso Ltd"],
        },
        "expected_output": "Entities: Service=Azure Virtual Machines, Region=westus2, Customer=Contoso Ltd",
    },
    {
        "input": "How many QCOs were caused by Azure Storage in east us and central us?",
        "expected_entities": {
            "services": ["Azure Storage"],
            "regions": ["eastus", "centralus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Storage, Region=eastus, centralus",
    },
    {
        "input": "Get the support cases for K8s and App Svc in japaneast for Fabrikam",
        "expected_entities": {
            "services": ["Azure Kubernetes Service", "Azure App Service"],
            "regions": ["japaneast"],
            "customers": ["Fabrikam Inc"],
        },
        "expected_output": "Entities: Service=Azure Kubernetes Service, Azure App Service, Region=japaneast, Customer=Fabrikam Inc",
    },
    {
        "input": "Show outage trends for Azure SQL in the last 90 days",
        "expected_entities": {
            "services": ["Azure SQL Database"],
            "regions": [],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure SQL Database",
    },
    {
        "input": "What SLI breaches did we have for Azure Databrcks in West US?",
        "expected_entities": {
            "services": ["Azure Databricks"],
            "regions": ["westus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Databricks, Region=westus",
    },
    {
        "input": "Contoso and Woodgrove both have CritSits on CosmosDB",
        "expected_entities": {
            "services": ["Azure Cosmos DB"],
            "regions": [],
            "customers": ["Contoso Ltd", "Woodgrove Bank"],
        },
        "expected_output": "Entities: Service=Azure Cosmos DB, Customer=Contoso Ltd, Woodgrove Bank",
    },
    {
        "input": "Azure Loadbalancer had an outage in aus east affecting multiple customers",
        "expected_entities": {
            "services": ["Azure Load Balancer"],
            "regions": ["australiaeast"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Load Balancer, Region=australiaeast",
    },
    {
        "input": "Show TTN metrics for the DNS service in southcentralus and eastus for Adventure Works and Tailspin Toys",
        "expected_entities": {
            "services": ["Azure DNS"],
            "regions": ["southcentralus", "eastus"],
            "customers": ["Adventure Works", "Tailspin Toys"],
        },
        "expected_output": "Entities: Service=Azure DNS, Region=southcentralus, eastus, Customer=Adventure Works, Tailspin Toys",
    },
    {
        "input": "Any Sev 2 outages in the last week?",
        "expected_entities": {
            "services": [],
            "regions": [],
            "customers": [],
        },
        "expected_output": "Entities: (none extracted — query is about severity, no specific service/region/customer)",
    },
    {
        "input": "VNet peering failures in West Europe for Datum Corp on Azure Networking",
        "expected_entities": {
            "services": ["Azure Networking"],
            "regions": ["westeurope"],
            "customers": ["Datum Corp"],
        },
        "expected_output": "Entities: Service=Azure Networking, Region=westeurope, Customer=Datum Corp",
    },
    {
        "input": "How is Azure Synapse performing in eus2?",
        "expected_entities": {
            "services": ["Azure Synapse Analytics"],
            "regions": ["eastus2"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Synapse Analytics, Region=eastus2",
    },
]


VAL_TASKS: list[dict] = [
    {
        "input": "What outages hit Azure Functions in westus2 this month?",
        "expected_entities": {
            "services": ["Azure Functions"],
            "regions": ["westus2"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Functions, Region=westus2",
    },
    {
        "input": "Fabrikam Inc has an escalation on AKS in central us",
        "expected_entities": {
            "services": ["Azure Kubernetes Service"],
            "regions": ["centralus"],
            "customers": ["Fabrikam Inc"],
        },
        "expected_output": "Entities: Service=Azure Kubernetes Service, Region=centralus, Customer=Fabrikam Inc",
    },
    {
        "input": "Show me AIRO for Azure SQL DB and Cosmos DB in eastus",
        "expected_entities": {
            "services": ["Azure SQL Database", "Azure Cosmos DB"],
            "regions": ["eastus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure SQL Database, Azure Cosmos DB, Region=eastus",
    },
    {
        "input": "Were there any SLI breaches for Azure Event Hubs in aus east?",
        "expected_entities": {
            "services": ["Azure Event Hubs"],
            "regions": ["australiaeast"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Event Hubs, Region=australiaeast",
    },
    {
        "input": "Alpine Ski House is reporting App Service slowness in westeurope",
        "expected_entities": {
            "services": ["Azure App Service"],
            "regions": ["westeurope"],
            "customers": ["Alpine Ski House"],
        },
        "expected_output": "Entities: Service=Azure App Service, Region=westeurope, Customer=Alpine Ski House",
    },
    {
        "input": "Get all QCOs across services for the last 30 days",
        "expected_entities": {
            "services": [],
            "regions": [],
            "customers": [],
        },
        "expected_output": "Entities: (none extracted — query spans all services)",
    },
    {
        "input": "How many CritSits for Woodgrove Bank on Azure Redis Cache in Japan East?",
        "expected_entities": {
            "services": ["Azure Cache for Redis"],
            "regions": ["japaneast"],
            "customers": ["Woodgrove Bank"],
        },
        "expected_output": "Entities: Service=Azure Cache for Redis, Region=japaneast, Customer=Woodgrove Bank",
    },
    {
        "input": "Check Datum Corp and Contoso for DNS issues in scus and wus2",
        "expected_entities": {
            "services": ["Azure DNS"],
            "regions": ["southcentralus", "westus2"],
            "customers": ["Datum Corp", "Contoso Ltd"],
        },
        "expected_output": "Entities: Service=Azure DNS, Region=southcentralus, westus2, Customer=Datum Corp, Contoso Ltd",
    },
    {
        "input": "Tailspin Toys VM issues in eastus2",
        "expected_entities": {
            "services": ["Azure Virtual Machines"],
            "regions": ["eastus2"],
            "customers": ["Tailspin Toys"],
        },
        "expected_output": "Entities: Service=Azure Virtual Machines, Region=eastus2, Customer=Tailspin Toys",
    },
    {
        "input": "Azure Service Bus latency in West US for Adventure Works",
        "expected_entities": {
            "services": ["Azure Service Bus"],
            "regions": ["westus"],
            "customers": ["Adventure Works"],
        },
        "expected_output": "Entities: Service=Azure Service Bus, Region=westus, Customer=Adventure Works",
    },
    {
        "input": "What's happening with Azure Stroage in southcentralus?",
        "expected_entities": {
            "services": ["Azure Storage"],
            "regions": ["southcentralus"],
            "customers": [],
        },
        "expected_output": "Entities: Service=Azure Storage, Region=southcentralus",
    },
    {
        "input": "Show TTM for CosmoDB in eus and weu for Fabrikam and Woodgrove",
        "expected_entities": {
            "services": ["Azure Cosmos DB"],
            "regions": ["eastus", "westeurope"],
            "customers": ["Fabrikam Inc", "Woodgrove Bank"],
        },
        "expected_output": "Entities: Service=Azure Cosmos DB, Region=eastus, westeurope, Customer=Fabrikam Inc, Woodgrove Bank",
    },
]
