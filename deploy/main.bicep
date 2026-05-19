// ============================================
// Ratio AI — Azure Container Apps Infrastructure
// ============================================
// Deploys:
//   - Container App: ca-ratio-ai-sr-insights (FastAPI backend)
//   - Container App: ca-ratio-ai-ui (React frontend)
// Into existing:
//   - Resource Group: rg-ratio-ai-dev
//   - Container Apps Environment: cae-ratio-ai-dev
//   - ACR: ratioaidev
// ============================================

// ── Parameters ──────────────────────────────────────────────

@description('Name of the existing Container Apps Environment')
param containerEnvName string = 'cae-ratio-ai-dev'

@description('Name of the existing Azure Container Registry')
param acrName string = 'ratioaidev'

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Docker image tag')
param imageTag string = 'latest'

// -- SR Insights config --

@description('Azure OpenAI endpoint URL')
param azureOpenAiEndpoint string

@description('Azure OpenAI deployment name')
param azureOpenAiDeployment string = 'gpt-4.1'

@description('Azure OpenAI API version')
param azureOpenAiApiVersion string = '2025-04-01-preview'

@description('Kusto cluster URI for outage data')
param kustoClusterUri string = 'https://ratioadxwus3prod.westus3.kusto.windows.net'

@description('Kusto database name')
param kustoDatabase string = 'ratiodata'

@description('Primo Kusto cluster URI for product name lookups')
param kustoPrimoClusterUri string = 'https://primodsshare.westus3.kusto.windows.net'

@description('Primo Kusto database name')
param kustoPrimoDatabase string = 'primosharedbdev'

// -- CustomerAgent investigation store --

@description('Existing Cosmos DB account holding the customer_agent container.')
param cosmosAccountName string = 'cosmos-ratio-ai-dev'

@description('Existing Cosmos SQL database name (holds customer_agent + leases).')
param cosmosDatabaseName string = 'customeragentdb'

@description('Name of the new lease container used by the Cosmos change-feed processor that powers /api/investigations/stream.')
param cosmosLeasesContainerName string = 'leases'

// -- CustomerAgent investigation scheduler (Container Apps Job) --

@description('Image tag for the scheduler container in ACR (customeragent-scheduler).')
param schedulerImageTag string = 'latest'

@description('Cron expression for the scheduler. Default = every hour at minute 0.')
param schedulerCron string = '0 * * * *'

@description('Default customer to investigate on each tick. Overridable per ad-hoc run via az containerapp job start --env-vars.')
param schedulerDefaultCustomerName string = 'BlackRock, Inc'

@description('Default lookback window in minutes. End time = utcnow at run; start = end - this.')
param schedulerDefaultLookbackMinutes int = 60

@description('Cloud CustomerAgent endpoint POSTed by the scheduler.')
param customerAgentEndpoint string = 'https://ca-ratio-customeragent-dev.graywater-ed11bb19.centralus.azurecontainerapps.io/api/run/services'

@description('Entra audience scope for the CustomerAgent app reg. Used by DefaultAzureCredential.')
param customerAgentAudienceScope string = 'de5f2e0f-ac6d-418e-a64c-e38dbbd116e5/.default'

// ── Variables ───────────────────────────────────────────────

var acrLoginServer = '${acrName}.azurecr.io'
var srInsightsAppName = 'ca-ratio-ai-sr-insights'
var uiAppName = 'ca-ratio-ai-ui'
var schedulerJobName = 'caj-customeragent-scheduler'

// ── Existing Resources (references) ─────────────────────────

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerEnvName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

// ── CustomerAgent investigation store (Cosmos) ──────────────
// `cosmos-ratio-ai-dev` is a serverless account, so containers must NOT
// declare `options.throughput` / autoscale settings. Adding either field
// will fail with: "Setting offer throughput or autopilot on container is
// not supported for serverless accounts."

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' existing = {
  parent: cosmosAccount
  name: cosmosDatabaseName
}

@description('Leases container for the /api/investigations/stream change-feed processor. PK = /id, no throughput (serverless).')
resource cosmosLeasesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: cosmosLeasesContainerName
  properties: {
    resource: {
      id: cosmosLeasesContainerName
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
      }
    }
  }
}

// ── SR Insights — FastAPI Container App ─────────────────────

resource srInsightsApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: srInsightsAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8006
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ratio-sr-insights'
          image: '${acrLoginServer}/ratio-sr-insights:${imageTag}'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAiEndpoint
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: azureOpenAiDeployment
            }
            {
              name: 'AZURE_OPENAI_API_VERSION'
              value: azureOpenAiApiVersion
            }
            {
              name: 'KUSTO_CLUSTER_URI'
              value: kustoClusterUri
            }
            {
              name: 'KUSTO_DATABASE'
              value: kustoDatabase
            }
            {
              name: 'KUSTO_PRIMO_CLUSTER_URI'
              value: kustoPrimoClusterUri
            }
            {
              name: 'KUSTO_PRIMO_DATABASE'
              value: kustoPrimoDatabase
            }
            {
              name: 'ALLOWED_ORIGINS'
              value: 'https://${uiAppName}.${containerEnv.properties.defaultDomain}'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ── React UI — Container App ────────────────────────────────

resource uiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: uiAppName
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ratio-ui-web'
          image: '${acrLoginServer}/ratio-ui-web:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'BACKEND_AGENTS_URL'
              value: 'https://ca-ratio-ai-agents.${containerEnv.properties.defaultDomain}'
            }
            {
              name: 'BACKEND_SR_INSIGHTS_URL'
              value: 'https://${srInsightsApp.properties.configuration.ingress.fqdn}'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ── CustomerAgent investigation scheduler — Container Apps Job ──
// Runs `Code/Scheduler/run_investigation.py` on a cron schedule
// (default every 5 min). Acquires an Entra token via the Job's
// system-assigned MI and POSTs to Manik's CustomerAgent endpoint.
//
// Ad-hoc per-run override (no schedule change):
//   az containerapp job start -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
//     --env-vars CUSTOMER_NAME="Contoso Capital" LOOKBACK_MINUTES=120

resource schedulerJob 'Microsoft.App/jobs@2024-03-01' = {
  name: schedulerJobName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerEnv.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 300                  // hard cap per run (sec). Endpoint is fast (<2s).
      replicaRetryLimit: 1                 // one retry on non-zero exit
      scheduleTriggerConfig: {
        cronExpression: schedulerCron
        parallelism: 1                     // don't overlap runs
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: acrLoginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'scheduler'
          image: '${acrLoginServer}/customeragent-scheduler:${schedulerImageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'CUSTOMER_NAME'
              value: schedulerDefaultCustomerName
            }
            {
              name: 'LOOKBACK_MINUTES'
              value: string(schedulerDefaultLookbackMinutes)
            }
            {
              name: 'ENDPOINT_URL'
              value: customerAgentEndpoint
            }
            {
              name: 'AUDIENCE_SCOPE'
              value: customerAgentAudienceScope
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
          ]
        }
      ]
    }
  }
}

// ── Outputs ─────────────────────────────────────────────────

output srInsightsFqdn string = srInsightsApp.properties.configuration.ingress.fqdn
output srInsightsUrl string = 'https://${srInsightsApp.properties.configuration.ingress.fqdn}'
output uiFqdn string = uiApp.properties.configuration.ingress.fqdn
output uiUrl string = 'https://${uiApp.properties.configuration.ingress.fqdn}'
output srInsightsPrincipalId string = srInsightsApp.identity.principalId
output cosmosLeasesContainerId string = cosmosLeasesContainer.id
// Surface the scheduler MI principal id so we can hand it to Manik for
// the `de5f2e0f-…` audience grant when he enables auth on the endpoint.
output schedulerJobName string = schedulerJob.name
output schedulerPrincipalId string = schedulerJob.identity.principalId
